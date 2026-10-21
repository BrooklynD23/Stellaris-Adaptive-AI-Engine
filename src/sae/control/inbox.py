"""C9 file inbox: ``runs/<run_id>/control/{inbox,done}``. Never raises ``OSError`` into the daemon."""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import stat
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from sae.contracts.c9_control import (
    CONTROL_COMMAND_ADAPTER,
    MAX_COMMAND_BYTES,
    ControlResult,
    command_expired,
)
from sae.daemon.ports import InboxItem

log = logging.getLogger(__name__)

__all__ = ["FileInbox", "open_inbox"]

_V0_CMDS = frozenset({"pause", "resume", "force_strategy", "status"})
_MOVE_TRIES = 3
_DETAIL_MAX = 2000


_CID_RE = re.compile(r"^c-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")


def _error(code: str, detail: str, stem: str, echo: bool = False) -> InboxItem:
    result = ControlResult.model_validate(
        {
            "command_id": stem if echo and _CID_RE.fullmatch(stem) else "unknown",
            "accepted": False,
            "error": code,
            "detail": detail[:_DETAIL_MAX],
            "applied_at": None,
            "status": None,
        }
    )
    return InboxItem(command=None, error=result, handle=stem)


class FileInbox:
    """Polls ``inbox/`` and records results in ``done/`` (C9)."""

    def __init__(self, run_dir: Path, sleep: Callable[[float], None] = time.sleep, *,
                 started_at: datetime | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._inbox = run_dir / "control" / "inbox"
        self._now = now
        self._started_at = started_at if started_at is not None else now()
        self._done = run_dir / "control" / "done"
        self._sleep = sleep
        self._seen: set[str] = set()

    def poll(self) -> list[InboxItem]:
        """New items, oldest (by name) first; each handle returned once until ``complete``."""
        try:
            names = sorted(e.name for e in os.scandir(self._inbox) if e.name.endswith(".json"))
        except OSError:
            log.exception("inbox scan failed", extra={"inbox": str(self._inbox)})
            return []
        items: list[InboxItem] = []
        for name in names:
            stem = name[: -len(".json")]
            if stem in self._seen:
                continue
            try:
                if (self._done / f"{stem}.result.json").exists():
                    self._move(stem)  # crash after result: retry the move, never re-apply
                    continue
                item = self._read(stem, self._inbox / name)
            except OSError:
                log.exception("inbox read failed", extra={"file": name})
                continue
            self._seen.add(stem)
            items.append(item)
        return items

    def _read(self, stem: str, path: Path) -> InboxItem:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        if not hasattr(os, "O_NOFOLLOW") and path.is_symlink():
            return _error("invalid", "not a regular file", stem)
        try:
            fd = os.open(path, flags)
        except OSError as e:
            # Windows: os.open on a directory raises PermissionError (EACCES), not EISDIR
            if path.is_symlink() or e.errno in (errno.ELOOP, errno.ENXIO) or path.is_dir():
                return _error("invalid", "not a regular file", stem)
            raise
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return _error("invalid", "not a regular file", stem)
            with os.fdopen(fd, "rb", closefd=False) as f:
                raw = f.read(MAX_COMMAND_BYTES + 1)
        finally:
            os.close(fd)
        if len(raw) > MAX_COMMAND_BYTES:
            return _error("too_large", f"command file exceeds {MAX_COMMAND_BYTES} bytes", stem)
        try:
            cmd = CONTROL_COMMAND_ADAPTER.validate_json(raw)
        except ValidationError as e:
            return self._classify(raw, stem, e)
        if cmd.command_id != stem:
            return _error("invalid", f"command_id {cmd.command_id!r} does not match file name", stem, echo=True)
        expired = command_expired(cmd.issued_at, self._now(), self._started_at)  # C9 3.1, proposal 016
        if expired is not None:
            result = ControlResult(command_id=cmd.command_id, accepted=False, error="expired", detail=expired,
                                   applied_at=None, status=None)
            return InboxItem(command=cmd, error=result, handle=stem)
        return InboxItem(command=cmd, error=None, handle=stem)

    @staticmethod
    def _classify(raw: bytes, stem: str, exc: ValidationError) -> InboxItem:
        try:
            doc = json.loads(raw)
        except (ValueError, RecursionError):
            doc = None
        inner = doc.get("command") if isinstance(doc, dict) else None
        cmd = inner.get("cmd") if isinstance(inner, dict) else None
        if isinstance(cmd, str) and cmd not in _V0_CMDS:
            return _error("unknown_cmd", f"unknown command {cmd[:64]!r}", stem, echo=True)
        errs = exc.errors(include_url=False, include_input=False)
        loc = ".".join(str(p) for p in errs[0]["loc"]) if errs else ""
        msg = errs[0]["msg"] if errs else "invalid command"
        return _error("invalid", f"{loc}: {msg}".strip(": "), stem)

    def complete(self, item: InboxItem, result: ControlResult) -> None:
        """Write the result atomically, then move the command file to ``done/``."""
        stem = item.handle
        tmp = self._done / f".{stem}.result.json.tmp"
        try:
            self._done.mkdir(parents=True, exist_ok=True)
            tmp.write_text(result.model_dump_json(), encoding="utf-8")
            self._replace(tmp, self._done / f"{stem}.result.json", stem)
        except OSError:
            log.exception("result write failed", extra={"handle": stem})
            tmp.unlink(missing_ok=True)
        if self._move(stem):
            self._seen.discard(stem)  # on failure keep it seen: never re-apply

    def _replace(self, src: Path, dst: Path, stem: str) -> None:
        """``os.replace`` retrying PermissionError (Windows sharing violations); raises after the last try."""
        for attempt in range(_MOVE_TRIES):
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                if attempt == _MOVE_TRIES - 1:
                    raise
                self._sleep(0.05)

    def _move(self, stem: str) -> bool:
        src = self._inbox / f"{stem}.json"
        for attempt in range(_MOVE_TRIES):
            try:
                os.replace(src, self._done / f"{stem}.json")
                return True
            except FileNotFoundError:
                return True
            except PermissionError:
                if attempt < _MOVE_TRIES - 1:
                    self._sleep(0.05)
                else:
                    log.exception("command move failed", extra={"handle": stem})
            except OSError:
                log.exception("command move failed", extra={"handle": stem})
                return False
        return False


def open_inbox(run_dir: Path, started_at: datetime | None = None) -> FileInbox:
    """Create ``control/inbox`` and ``control/done`` under ``run_dir`` and return the inbox.

    ``started_at`` is the daemon session start (default: now); older commands are ``expired`` (C9 3.1).
    """
    for sub in ("inbox", "done"):
        (run_dir / "control" / sub).mkdir(parents=True, exist_ok=True)
    return FileInbox(run_dir, started_at=started_at)
