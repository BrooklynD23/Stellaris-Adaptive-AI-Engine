"""Offline compaction of ``bus.god.jsonl`` (plan T6 §6 #10). The legal ``bus.jsonl`` is never compacted."""

from __future__ import annotations

import os
from pathlib import Path

from sae.bus.reader import iter_events
from sae.contracts.c5_bus import GOD_BUS_EVENT_ADAPTER, GOD_FILE, ObservationGodEvent, RunStartedEvent
from sae.control.runlive import is_run_live

__all__ = ["CompactRefused", "compact_god"]


class CompactRefused(RuntimeError):
    """The run is live (``runs/CURRENT`` names it with a living pid)."""


def _month_index(date: str) -> int:
    return int(date[:4]) * 12 + int(date[5:7]) - 1


def compact_god(run_dir: Path, *, keep_every_months: int, runs_dir_current: Path | None = None) -> int:
    """Keep ``run_started`` and the first ``observation_god`` of each ``keep_every_months``-month bucket.

    Invalid lines and a partial tail are dropped; ``seq`` is renumbered gaplessly. Writes a tmp file, fsyncs,
    ``os.replace``s. Returns the number of lines kept. Raises :class:`CompactRefused` for a live run.
    """
    if keep_every_months < 1:
        raise ValueError("keep_every_months must be >= 1")
    if is_run_live((runs_dir_current.parent if runs_dir_current else run_dir.parent), run_dir.name):
        raise CompactRefused(f"run {run_dir.name} is live; stop the daemon first")
    path = run_dir / GOD_FILE
    kept: list[bytes] = []
    seen: set[int] = set()
    for ev in iter_events(path, god=True):
        if isinstance(ev, ObservationGodEvent):
            bucket = _month_index(ev.payload.game_date) // keep_every_months
            if bucket in seen:
                continue
            seen.add(bucket)
        elif not isinstance(ev, RunStartedEvent):
            continue
        kept.append(GOD_BUS_EVENT_ADAPTER.dump_json(ev.model_copy(update={"seq": len(kept)})) + b"\n")
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("wb") as fh:
            fh.write(b"".join(kept))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return len(kept)
