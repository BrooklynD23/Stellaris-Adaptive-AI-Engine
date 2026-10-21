"""Autosave discovery with a clock-skew-proof stability rule (T2 U2)."""

from __future__ import annotations

import fnmatch
import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_log = logging.getLogger("sae.adapters.savewatch")

STABLE_S = 2.0  # unchanged for at least this long (monotonic)
SEEN_S = 8.0  # and at least this long since this exact version was first seen (monotonic)
IRONMAN_NAME = "ironman.sav"  # A-34 UNVERIFIED: M0 checks the ironman save file name.


class _Stat(Protocol):
    @property
    def st_size(self) -> int: ...

    @property
    def st_mtime_ns(self) -> int: ...


StatFn = Callable[[Path], _Stat]
ListdirFn = Callable[[Path], Sequence[str]]


@dataclass
class _Entry:
    size: int
    mtime_ns: int
    first_seen: float
    done: bool = False


class SaveWatcher:
    """Reports a finished autosave once. Uses only the monotonic clock, never file times for timing."""

    def __init__(
        self,
        save_dir: Path,
        *,
        monotonic: Callable[[], float],
        patterns: Sequence[str] = ("autosave_*.sav",),
        include_ironman: bool = False,
        baseline_ironman: bool = True,
        stat: StatFn = os.stat,
        listdir: ListdirFn = os.listdir,
    ) -> None:
        self._dir = save_dir
        self._mono = monotonic
        self._patterns = tuple(patterns)
        self._ironman = include_ironman
        # C6 section 1.10: LIVE probes only an ironman.sav modified after start, never a pre-existing one
        self._baseline_ironman = baseline_ironman
        self._stat = stat
        self._listdir = listdir
        self._seen: dict[Path, _Entry] = {}
        self._first_poll = True

    def _candidates(self) -> list[Path] | None:
        """Matching files, or None if the save directory itself cannot be listed."""
        out: list[Path] = []
        try:
            folders = list(self._listdir(self._dir))
        except OSError:
            return None
        for folder in folders:
            try:
                names = list(self._listdir(self._dir / folder))
            except OSError:
                continue  # a plain file, or gone
            for name in names:
                if any(fnmatch.fnmatch(name, p) for p in self._patterns) or (
                    self._ironman and name == IRONMAN_NAME
                ):
                    out.append(self._dir / folder / name)
        return out

    def poll(self) -> Path | None:
        """The newest eligible save, at most once per (path, size, mtime); else None."""
        now = self._mono()
        found = self._candidates()
        if found is None:
            return None
        stats: dict[Path, tuple[int, int]] = {}
        failed: set[Path] = set()
        for p in found:
            try:
                st = self._stat(p)
            except OSError:
                failed.add(p)  # transient: keep its state, do not re-arm
                continue
            stats[p] = (st.st_size, st.st_mtime_ns)
        self._seen = {p: e for p, e in self._seen.items() if p in stats or p in failed}
        for p, (size, mtime) in stats.items():
            e = self._seen.get(p)
            if e is None or (e.size, e.mtime_ns) != (size, mtime):
                self._seen[p] = _Entry(size, mtime, now)
        if self._first_poll:
            self._first_poll = False
            pool = [p for p in stats if self._baseline_ironman or p.name != IRONMAN_NAME]
            newest = max(pool, key=lambda p: stats[p][1], default=None)
            for p, e in self._seen.items():
                e.done = p != newest  # baseline: only the newest existing save counts
        eligible = [
            p
            for p, e in self._seen.items()
            if not e.done and now - e.first_seen >= max(STABLE_S, SEEN_S)
        ]
        if not eligible:
            return None
        best = max(eligible, key=lambda p: (stats[p][1], str(p)))
        for p in eligible:
            self._seen[p].done = True
        _log.info("save eligible path=%s", best)
        return best
