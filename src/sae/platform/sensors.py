"""Cheap 2 s sensors (C7 section 5). Every probe failure yields None / last good; ``sample`` never raises.

UNVERIFIED A-26: Linux ``pgmajfault`` / Windows ``Pages Input/sec`` track real paging pressure; M0 tests
it by forcing memory pressure and watching the rate.
"""

from __future__ import annotations

import logging
import math
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import psutil

from sae.contracts.c7_governor import SensorSample

from . import _win32

log = logging.getLogger(__name__)
_RESCAN_S = 10.0


def _default_names() -> tuple[str, ...]:
    return ("stellaris.exe",) if sys.platform == "win32" else ("stellaris", "stellaris.exe")  # .exe: Proton


def _finite(x: float | None) -> float | None:
    return x if x is not None and math.isfinite(x) else None


def _read_vmstat(path: str) -> str:
    return Path(path).read_text(encoding="ascii")


class SystemSensors:
    """psutil / procfs / PDH backed :class:`~sae.contracts.c7_governor.Sensors`."""

    def __init__(
        self,
        game_names: tuple[str, ...] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        psutil_mod: ModuleType | Any = psutil,
        read_text: Callable[[str], str] = _read_vmstat,
        system: str | None = None,
        win32: ModuleType | Any = _win32,
    ) -> None:
        self._names = frozenset(n.lower() for n in (game_names or _default_names()))
        self._clock = clock
        self._ps = psutil_mod
        self._read_text = read_text
        self._system = system or sys.platform
        self._win32 = win32
        try:
            self._last_ram = int(self._ps.virtual_memory().available)
        except Exception as exc:
            raise RuntimeError(f"RAM probe unavailable: {exc}") from exc
        self._me = self._ps.Process()
        self._last_rss = 0
        try:
            self._me.cpu_percent(None)  # prime
        except Exception:
            log.warning("daemon cpu_percent priming failed", exc_info=True)
        self._game: Any = None
        self._game_ct: float | None = None
        self._next_scan = 0.0
        self._prev_faults: tuple[float, float] | None = None
        self._pdh: Callable[[], float] | None = None
        if self._system == "win32":
            try:
                self._pdh = self._win32.open_pages_input_counter()
            except Exception:
                log.warning("PDH counter unavailable; hard_fault_rate=None", exc_info=True)
        self._min_since: float | None = None
        self._min_last = 0.0

    # --- probes -------------------------------------------------------------------------------
    def _ram(self) -> int:
        try:
            v = int(self._ps.virtual_memory().available)
            if v >= 0:
                self._last_ram = v
        except Exception:
            log.debug("RAM probe failed; using last good", exc_info=True)
        return self._last_ram

    def _faults(self, now: float) -> float | None:
        try:
            if self._pdh is not None:
                return _finite(self._pdh())
            if not self._system.startswith("linux"):
                return None
            total: float | None = None
            for line in self._read_text("/proc/vmstat").splitlines():
                k, _, v = line.partition(" ")
                if k == "pgmajfault":
                    total = float(v)
            if total is None:
                return None
            prev, self._prev_faults = self._prev_faults, (now, total)
            if prev is None or now <= prev[0] or total < prev[1]:
                return None
            return _finite((total - prev[1]) / (now - prev[0]))
        except Exception:
            log.debug("fault probe failed", exc_info=True)
            return None

    def _find_game(self, now: float) -> Any:
        """Cached Process, validated by is_running (PID reuse); rescan by name at most every 10 s."""
        if self._game is not None:
            try:
                if self._game.is_running() and self._game.create_time() == self._game_ct:
                    return self._game
            except Exception:
                log.debug("cached game process invalid", exc_info=True)
            self._game = self._game_ct = None
        if now < self._next_scan:
            return None
        self._next_scan = now + _RESCAN_S
        for p in self._ps.process_iter(["name", "create_time"]):
            info = p.info
            if str(info.get("name") or "").lower() in self._names and info.get("create_time") is not None:
                self._game, self._game_ct = p, info["create_time"]
                return p
        return None

    def _game_probe(self, now: float) -> tuple[bool, float | None, float | None]:
        try:
            p = self._find_game(now)
        except Exception:
            log.debug("game scan failed", exc_info=True)
            return False, None, None
        if p is None:
            self._min_since = None
            return False, None, 0.0 if self._system == "win32" else None
        cpu: float | None = None
        try:
            t = p.cpu_times()
            cpu = _finite(float(t.user + t.system))
        except Exception:
            # transient / AccessDenied: keep the cached process while it is still alive (no state churn)
            log.debug("game cpu_times failed", exc_info=True)
            try:
                alive = bool(p.is_running())
            except Exception:
                alive = False
            if not alive:
                self._game = self._game_ct = None
                self._min_since = None
                return False, None, 0.0 if self._system == "win32" else None
        return True, cpu, self._minimized(p, now)

    def _minimized(self, p: Any, now: float) -> float | None:
        if self._system != "win32":
            return None
        try:
            mini = self._win32.is_minimized(p.pid)
        except Exception:
            self._min_since = None
            return 0.0
        if mini:
            if self._min_since is None:
                self._min_since = now
            return max(0.0, now - self._min_since)
        self._min_since = None
        return 0.0

    def _daemon(self) -> tuple[int, float]:
        try:
            self._last_rss = int(self._me.memory_info().rss)
        except Exception:
            log.debug("rss probe failed", exc_info=True)
        pct = 0.0
        try:
            pct = _finite(float(self._me.cpu_percent(None))) or 0.0
        except Exception:
            log.debug("cpu probe failed", exc_info=True)
        return self._last_rss, max(0.0, pct)

    def sample(self) -> SensorSample:
        """Take one sample; never raises."""
        now = self._clock()
        running, cpu, mini = self._game_probe(now)
        rss, pct = self._daemon()
        return SensorSample(
            t_mono_s=max(0.0, now),
            available_ram_bytes=self._ram(),
            hard_fault_rate=_faults_nonneg(self._faults(now)),
            game_running=running,
            game_cpu_s=None if cpu is None or cpu < 0 else cpu,
            game_minimized_s=mini,
            daemon_rss_bytes=rss,
            daemon_cpu_pct=pct,
        )


def _faults_nonneg(v: float | None) -> float | None:
    return v if v is not None and v >= 0 else None


def make_sensors(game_names: tuple[str, ...] | None = None) -> SystemSensors:
    """Build production sensors; raises if the RAM probe is unavailable."""
    return SystemSensors(game_names)


