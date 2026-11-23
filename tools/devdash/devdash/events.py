"""Append-only dev event log: build, redact, append, read, tail and the enabled flag.

Events live in ``.sae-dev/events.jsonl`` (WSL writers) and ``.sae-dev/events.win.jsonl`` (Windows-side runs). Each
event is one JSON line. Writers append with a single ``os.write`` on an ``O_APPEND`` descriptor and never rotate;
the server is the only rotator (:func:`rotate_if_needed`). Readers skip and count lines they cannot parse.

Stdlib-only: hooks run this module under a bare ``python3``.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import sys
from collections import deque
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, TypeGuard

from devdash import dev_dir

SCHEMA_VERSION: Final = 1
"""Value of the ``v`` field in every event this module writes or accepts."""

KINDS: Final[tuple[str, ...]] = ("info", "start", "progress", "pass", "fail", "skip", "owner", "done")
MSG_MAX: Final = 200
ROTATE_BYTES: Final = 5 * 1024 * 1024
"""Size at which the server rolls a log file. Amended from the spec's 20 MB for the dashboard memory budget."""

ENABLED_FLAG: Final = "enabled"
HOME_TOKEN: Final = "<HOME>"
WINHOME_TOKEN: Final = "<WINHOME>"
_SEP: Final = r"(?:\\\\|[\\/])"
_BAD: Final = r"/\\\s\"<>|:*?'"
_WORD: Final = rf"[^{_BAD}]+(?:'+[^{_BAD}]+)*"
_NAME: Final = rf"(?:{_WORD}(?: +{_WORD})*(?=[/\\\"'\r]|$)|{_WORD})"
_WINHOME_RE: Final = re.compile(
    rf"/mnt/[a-z]/users/{_NAME}|[a-z]:{_SEP}users{_SEP}{_NAME}", re.IGNORECASE | re.MULTILINE
)
_WRITER_FILES: Final[dict[str, str]] = {"wsl": "events.jsonl", "win": "events.win.jsonl"}
_MAX_LINE_BYTES: Final = 64 * 1024
"""Longest line :class:`Tail` buffers; a valid event is well under 1 KiB, so longer lines are dropped."""
_TS_RE: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
_CONTROL_RE: Final = re.compile(r"\r\n|[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _home_dir() -> str:
    """Return the current user's home directory, or an empty string when it cannot be determined."""
    try:
        return str(Path.home())
    except RuntimeError:
        return ""


def scrub_winhome(text: str) -> str:
    """Replace Windows profile paths by ``<WINHOME>``.

    Matches ``/mnt/<drive>/Users/<name>``, ``C:\\Users\\<name>`` (also JSON-escaped) and ``C:/Users/<name>`` for any
    drive letter and case. A name with spaces is replaced whole only when a separator, a quote or a line end follows it;
    otherwise the name ends at the first space. Apostrophes inside a name are part of it.
    """
    return _WINHOME_RE.sub(WINHOME_TOKEN, text)


def redact(text: str, home: str | None = None) -> str:
    """Replace the home directory in ``text`` by ``<HOME>``, in both slash styles, then Windows profile paths.

    Uses the same rule as ``tools/game/run_deferred.py``. A bare root (``/``, ``\\``, or an empty home) is never
    replaced, because that would mangle every path in the text.
    """
    root = _home_dir() if home is None else home
    for value in sorted({root, root.replace("\\", "/")}, key=len, reverse=True):
        if value.strip("/\\"):
            text = text.replace(value, HOME_TOKEN)
    return scrub_winhome(text)


def _clean_msg(msg: str) -> str:
    """Redact, flatten control characters (including CRLF) to spaces, and cut to :data:`MSG_MAX` characters."""
    return _CONTROL_RE.sub(" ", redact(msg))[:MSG_MAX]


def _rfc3339_ms(moment: datetime) -> str:
    """Format an aware datetime as UTC RFC 3339 with millisecond precision, e.g. ``2026-10-29T12:30:45.123Z``."""
    if moment.utcoffset() is None:
        raise ValueError("event timestamp must be timezone-aware")
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def make_event(
    source: str,
    kind: str,
    msg: str,
    *,
    ref: str | None = None,
    pct: int | None = None,
    session: str | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Build one v1 event. ``ref``, ``pct`` and ``session`` appear only when given.

    Raises ``ValueError`` for an unknown ``kind``, an empty ``source``, a ``pct`` outside 0..100 (booleans are
    rejected), or a naive ``now``. ``msg`` is redacted, flattened to one line and cut to :data:`MSG_MAX` characters.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown event kind {kind!r}; expected one of {', '.join(KINDS)}")
    if not source.strip():
        raise ValueError("event source must be non-empty")
    if pct is not None and (isinstance(pct, bool) or not 0 <= pct <= 100):
        raise ValueError(f"pct must be an integer in 0..100, got {pct!r}")
    event: dict[str, object] = {
        "v": SCHEMA_VERSION,
        "ts": _rfc3339_ms(datetime.now(UTC) if now is None else now),
        "source": source,
        "kind": kind,
        "msg": _clean_msg(msg),
    }
    if ref is not None:
        event["ref"] = ref
    if pct is not None:
        event["pct"] = pct
    if session is not None:
        event["session"] = session
    return event


def events_path(dev: Path, writer: str = "wsl") -> Path:
    """Return the log file for ``writer``: ``events.jsonl`` for ``"wsl"``, ``events.win.jsonl`` for ``"win"``."""
    name = _WRITER_FILES.get(writer)
    if name is None:
        raise ValueError(f"unknown writer {writer!r}; expected 'wsl' or 'win'")
    return dev / name


def _write_all(fd: int, data: bytes) -> None:
    """Write ``data`` completely. After a partial write that cannot be finished, close the torn line with a newline.

    The newline stops the fragment from joining the next event's line, so only the failed event is lost.
    """
    written = 0
    try:
        while written < len(data):
            count = os.write(fd, data[written:])
            if count <= 0:
                raise OSError(errno.EIO, "os.write made no progress")
            written += count
    except OSError:
        if written:
            with contextlib.suppress(OSError):
                os.write(fd, b"\n")
        raise


def append(event: Mapping[str, object], path: Path) -> None:
    """Append ``event`` to ``path`` as one JSON line, using one ``os.write`` on an ``O_APPEND`` descriptor.

    Writers never rotate. Raises ``ValueError`` when the event cannot be serialised as strict JSON and ``OSError``
    on I/O failure.
    """
    try:
        line = json.dumps(event, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"
    except (TypeError, ValueError) as exc:
        raise ValueError(f"event is not strict JSON: {exc}") from exc
    data = line.encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o644)
    try:
        _write_all(fd, data)
    finally:
        os.close(fd)


def rotate_if_needed(path: Path) -> Path | None:
    """Roll ``path`` to the next free ``<stem>.<n>.jsonl`` once it has reached :data:`ROTATE_BYTES`.

    Only the server calls this. ``events.jsonl`` becomes ``events.<n>.jsonl`` and ``events.win.jsonl`` becomes
    ``events.win.<n>.jsonl``, so the two writers' histories never mix. Returns the new path, or ``None`` when the
    file is missing or still small. Raises ``OSError`` if the rename fails.

    The caller must call :meth:`Tail.poll` immediately before rotating: lines appended between that poll and the
    rename are otherwise never seen by the tail, which restarts on the new file.
    """
    if not path.name.endswith(".jsonl"):
        raise ValueError(f"not a JSON Lines log: {path.name}")
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    if size < ROTATE_BYTES:
        return None
    stem = path.name[: -len(".jsonl")]
    index = 1
    while (target := path.with_name(f"{stem}.{index}.jsonl")).exists():
        index += 1
    os.replace(path, target)
    return target


def _is_event(obj: object) -> TypeGuard[dict[str, Any]]:
    """Return True when ``obj`` is a well-formed v1 event. Unknown extra keys are tolerated."""
    if not isinstance(obj, dict):
        return False
    version = obj.get("v")
    if type(version) is not int or version != SCHEMA_VERSION:
        return False
    ts = obj.get("ts")
    if not isinstance(ts, str) or _TS_RE.fullmatch(ts) is None:
        return False
    source = obj.get("source")
    if not isinstance(source, str) or not source.strip():
        return False
    if obj.get("kind") not in KINDS or not isinstance(obj.get("msg"), str):
        return False
    if "ref" in obj and not isinstance(obj["ref"], str):
        return False
    if "session" in obj and not isinstance(obj["session"], str):
        return False
    if "pct" in obj:
        pct = obj["pct"]
        if type(pct) is not int or not 0 <= pct <= 100:
            return False
    return True


def _parse_line(raw: bytes) -> dict[str, Any] | None:
    """Parse one complete line into an event, or return None when it is not a valid v1 event."""
    try:
        decoded: object = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    return decoded if _is_event(decoded) else None


def read_events(paths: Sequence[Path], since_ts: str | None = None) -> tuple[list[dict[str, Any]], int]:
    """Read every valid event in ``paths``, merged and sorted by ``ts``. Returns ``(events, invalid_lines)``.

    Missing files are skipped. Lines that are not valid v1 events are counted and skipped, and so is a final
    fragment with no newline (a write still in progress or torn). With ``since_ts`` only events strictly later
    than it are returned. This is a full read for tests and the CLI; the server uses :class:`Tail`.
    """
    collected: list[dict[str, Any]] = []
    invalid = 0
    for path in paths:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            continue
        *complete, fragment = raw.split(b"\n")
        if fragment.strip():
            invalid += 1
        for line in complete:
            if not line.strip():
                continue
            event = _parse_line(line)
            if event is None:
                invalid += 1
            elif since_ts is None or str(event["ts"]) > since_ts:
                collected.append(event)
    collected.sort(key=lambda event: str(event["ts"]))
    return collected, invalid


class Tail:
    """Incremental reader for one events file. Each :meth:`poll` returns only lines appended since the last call.

    The reader keeps the inode and byte offset. It holds back a trailing fragment until its newline arrives. It
    restarts at byte 0 when the inode changes (rotation) or the file shrinks. A line longer than 64 KiB is dropped
    and counted as invalid, and the reader skips to the next newline, so memory stays bounded. The newest
    ``maxlen`` valid events are kept in :attr:`events`. The file is opened and closed on each poll, so the rotator
    is never blocked by an open handle.

    Windows note: the inode check depends on ``st_ino``. If a filesystem reports 0 for it, rotation is detected
    only through the size check.
    """

    def __init__(self, path: Path, maxlen: int = 2000) -> None:
        """Track ``path``, keeping at most ``maxlen`` (at least 1) recent events in :attr:`events`."""
        if maxlen < 1:
            raise ValueError("maxlen must be at least 1")
        self.path: Path = path
        self.events: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._inode: int | None = None
        self._offset = 0
        self._fragment = b""
        self._skipping = False

    def poll(self) -> tuple[list[dict[str, Any]], int]:
        """Return ``(new_events, invalid_lines)`` for lines completed since the previous poll. A missing file yields
        nothing."""
        try:
            handle = self.path.open("rb")
        except FileNotFoundError:
            return [], 0
        with handle:
            info = os.fstat(handle.fileno())
            if self._inode != info.st_ino or info.st_size < self._offset:
                self._restart(info.st_ino)
            handle.seek(self._offset)
            chunk = handle.read()
        self._offset += len(chunk)
        return self._consume(chunk)

    def _restart(self, inode: int) -> None:
        """Forget the old position and read the file from byte 0."""
        self._inode = inode
        self._offset = 0
        self._fragment = b""
        self._skipping = False

    def _consume(self, chunk: bytes) -> tuple[list[dict[str, Any]], int]:
        """Split ``chunk`` into complete lines, parse them, and keep the trailing fragment for the next poll."""
        if self._skipping:
            cut = chunk.find(b"\n")
            if cut < 0:
                return [], 0
            self._skipping = False
            chunk = chunk[cut + 1 :]
        *complete, self._fragment = (self._fragment + chunk).split(b"\n")
        fresh: list[dict[str, Any]] = []
        invalid = 0
        for raw in complete:
            if not raw.strip():
                continue
            event = _parse_line(raw)
            if event is None:
                invalid += 1
                continue
            fresh.append(event)
            self.events.append(event)
        if len(self._fragment) > _MAX_LINE_BYTES:
            self._fragment = b""
            self._skipping = True
            invalid += 1
        return fresh, invalid


def is_enabled(dev: Path) -> bool:
    """Return True when the dev log is switched on, i.e. ``<dev>/enabled`` exists."""
    return (dev / ENABLED_FLAG).is_file()


def enable(dev: Path) -> None:
    """Create ``<dev>/enabled`` (and ``dev``) so emitters start writing. Raises ``OSError`` on failure."""
    dev.mkdir(parents=True, exist_ok=True)
    (dev / ENABLED_FLAG).touch(exist_ok=True)


def disable(dev: Path) -> None:
    """Remove ``<dev>/enabled``. Emitters become no-ops again. Missing flag is not an error."""
    (dev / ENABLED_FLAG).unlink(missing_ok=True)


def emit(
    source: str,
    kind: str,
    msg: str,
    *,
    ref: str | None = None,
    pct: int | None = None,
    session: str | None = None,
    dev: Path | None = None,
    writer: str = "wsl",
) -> bool:
    """Record one event if the log is enabled. Returns True only when a line was written.

    Never raises. A disabled log returns False without touching the disk. ``OSError`` and ``ValueError`` are logged
    to stderr and turned into False, so a hook can never fail the tool call that triggered it.
    """
    base = dev_dir() if dev is None else dev
    try:
        if not is_enabled(base):
            return False
        append(make_event(source, kind, msg, ref=ref, pct=pct, session=session), events_path(base, writer))
    except (OSError, ValueError) as exc:
        print(f"devdash: event not written (kind={kind}, source={source}): {exc}", file=sys.stderr)
        return False
    return True
