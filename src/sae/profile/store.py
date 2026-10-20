"""Index and file IO for ``sae profile`` (C10 section 1, proposal 025 section 3.6, 3.9).

``campaigns.jsonl`` is append-only and written only here, under a ``O_CREAT|O_EXCL`` lock file.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from sae.contracts.c10_profile import CampaignEvent

log = logging.getLogger(__name__)

__all__ = [
    "INDEX_FILE",
    "LOCK_FILE",
    "IndexCorrupt",
    "IndexLocked",
    "append_events",
    "canonical_bytes",
    "now",
    "profile_dir",
    "read_index",
    "write_atomic",
]

INDEX_FILE = "campaigns.jsonl"
LOCK_FILE = "campaigns.jsonl.lock"


class IndexLocked(RuntimeError):
    """Another writer holds ``campaigns.jsonl.lock`` (a stale lock after a crash is removed by hand)."""


class IndexCorrupt(RuntimeError):
    """``campaigns.jsonl`` has a seq gap or repeat among valid rows."""


def now() -> datetime:
    """Current UTC time (one seam so tests can pin it)."""
    return datetime.now(UTC)


def profile_dir(sae: Path, player_key: str) -> Path:
    """``<sae_dir>/profile/<player_key>``."""
    return sae / "profile" / player_key


def read_index(path: Path) -> tuple[list[CampaignEvent], int, bool]:
    """Read the index: ``(valid rows, invalid line count, gap)``.

    Invalid lines are skipped and counted; a final line without LF counts if it parses as a row.
    ``gap`` is True when the valid rows' seq values are not 0, 1, 2 ... (a gap or a repeat).
    """
    if not path.exists():
        return [], 0, False
    data = path.read_bytes()
    pieces = data.split(b"\n")
    if not pieces[-1]:  # file ends with LF; otherwise the tail is validated like any other row
        pieces.pop()
    invalid = 0
    events: list[CampaignEvent] = []
    gap = False
    for line in pieces:
        try:
            ev = CampaignEvent.model_validate_json(line)
        except ValidationError:
            invalid += 1
            continue
        if ev.seq != len(events):
            gap = True
        events.append(ev)
    return events, invalid, gap


def append_events(path: Path, new: list[CampaignEvent]) -> list[CampaignEvent]:
    """Append ``new`` under the index lock; assign ``seq`` here (last valid seq + 1) and return the rows written.

    Raises:
        IndexLocked: the lock file exists; it is left untouched.
        IndexCorrupt: the index has a seq gap or repeat.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(LOCK_FILE)
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
    except FileExistsError:
        raise IndexLocked(f"index locked: {LOCK_FILE}") from None
    try:
        events, invalid, gap = read_index(path)
        if gap:
            raise IndexCorrupt("campaigns.jsonl corrupted; rename it and re-run update")
        if invalid:
            log.warning("campaigns.jsonl: %d invalid line(s) skipped", invalid)
        seq = events[-1].seq + 1 if events else 0
        written: list[CampaignEvent] = []
        out = bytearray()
        if path.exists() and path.stat().st_size > 0 and not path.read_bytes().endswith(b"\n"):
            out += b"\n"
        for row in new:
            row = row.model_copy(update={"seq": seq})
            seq += 1
            out += json.dumps(row.model_dump(mode="json", exclude_none=True), separators=(",", ":")).encode()
            out += b"\n"
            written.append(row)
        with path.open("ab") as fh:
            fh.write(bytes(out))
            fh.flush()
            os.fsync(fh.fileno())
        return written
    finally:
        lock.unlink(missing_ok=True)


def canonical_bytes(model: BaseModel) -> bytes:
    """Stable file bytes for profile files (025 section 3.6)."""
    return (json.dumps(model.model_dump(mode="json"), sort_keys=True, indent=1) + "\n").encode()


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to a temp file in the same directory, then ``os.replace`` it into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
