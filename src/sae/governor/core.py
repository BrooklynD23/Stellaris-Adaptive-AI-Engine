"""Pure Resource Governor (C7 sections 2-4). No I/O, no wall clock: time comes from ``SensorSample.t_mono_s``."""

from __future__ import annotations

import logging
import statistics
from collections import deque

from sae.contracts.c2_ini import KnobCommand
from sae.contracts.c7_governor import (
    GameState,
    GovernorConfig,
    GovernorDecision,
    HealthLevel,
    SensorSample,
    SpmSample,
    TelemetryTier,
    Trigger,
    WorkerOutcome,
)
from sae.contracts.common import GameDate

log = logging.getLogger(__name__)

_LEVELS = (HealthLevel.H0, HealthLevel.H1, HealthLevel.H2, HealthLevel.H3)
_MAX_DT_S = 60.0  # clock jump / host suspend: accumulators ignore dt outside [0, 60]
_CLOSED_DEFAULT_SPM_S = 30.0
_H1_SPARSE_MONTHS = 6.0  # C7 section 3: after 6 game months still at H1, parse every 3rd save
_Target = tuple[int, Trigger]


class ResourceGovernor:
    """Implements the C7 ``Governor`` protocol. Internal state is plain mutable; decisions are frozen models."""

    def __init__(
        self, cfg: GovernorConfig, min_level: HealthLevel, preset: str, *, cpu_count: int, emit_period: int
    ) -> None:
        if min_level is HealthLevel.S:
            raise ValueError("min_level can never be S")
        self._cfg = cfg
        self._floor = _LEVELS.index(min_level)
        self._dev = preset == "dev"
        self._cpus = max(cpu_count, 1)
        self._base_emit = emit_period
        self._t_prev: float | None = None
        self._months = 0.0
        self._delta = 0.0
        self._lvl = 0  # pressure level index into _LEVELS
        self._clean = 0.0
        self._h2m = 0.0
        self._h2_seen = False
        self._elapsed = 0.0  # sum of in-range dt
        self._pend: _Target | None = None
        self._suspended = False
        self._prev_susp = False
        self._prev_u = 0
        self._emitted_u = 0
        self._u_since = 0.0
        self._boundary: GameDate | None = None
        # fault state
        self._base: list[float] = []
        self._fault_ref: float | None = None  # frozen median + k * MAD
        self._fault_frozen = False
        self._t_first: float | None = None
        self._consec = 0
        # spm state
        self._win: deque[float] = deque(maxlen=cfg.spm_long_months)
        self._prev_spm: float | None = None
        self._last_spm_esc: float | None = None

    @property
    def game_months(self) -> float:
        """Game months counted so far (HB months plus CLOSED wall-time months)."""
        return self._months

    @property
    def spm_med6(self) -> float | None:
        """Median spm of the last ``spm_short_months`` samples (C5 monthly ``spm_med6``); None if no samples."""
        vals = list(self._win)[-self._cfg.spm_short_months :]
        return float(statistics.median(vals)) if vals else None

    @property
    def spm_med24(self) -> float | None:
        """Median spm of the whole ``spm_long_months`` window (C5 monthly ``spm_med24``); None if no samples."""
        return float(statistics.median(self._win)) if self._win else None

    # -- protocol ---------------------------------------------------------------------------------------------

    def set_next_boundary(self, date: GameDate | None) -> None:
        """Store the next commitment boundary (the H2 window itself is applied by T4's parse gate)."""
        self._boundary = date

    def suspend(self, suspended: bool) -> None:
        """Enter or leave level S."""
        self._suspended = suspended

    def on_worker(self, outcome: WorkerOutcome) -> None:
        """A budget breach escalates to at least H2 on the next poll (C7 v1.1 section 7, trigger budget_breach)."""
        if outcome.kind == "budget_breach":
            self._queue((2, Trigger.BUDGET_BREACH))

    def on_month(self, sample: SpmSample) -> None:
        """Feed one spm sample: step test, windows, spm escalation (never above H1)."""
        cfg = self._cfg
        w = sample.wall_s
        if sample.months > cfg.spm_short_months:
            # proposal 008 §8 clarification: months > spm_short_months is a game-date jump (save load), not play
            # time. Drop the windows and count none of these months toward h2/clean/H1-age/spm-escalation.
            self._win.clear()
            self._prev_spm = None
            return
        prev = self._prev_spm
        floor_s = 5 * cfg.poll_s
        if prev is not None and prev >= floor_s and w >= floor_s and abs(w - prev) / prev > cfg.spm_step_reset:
            self._win.clear()
        self._prev_spm = w
        self._win.extend([w] * sample.months)
        self._add_months(float(sample.months))
        r = self._ratio()
        last = self._last_spm_esc
        gap_ok = last is None or self._months - last >= cfg.spm_min_months_between_escalations
        if r is not None and r >= cfg.spm_h1_ratio and self._lvl == 0 and gap_ok:
            self._queue((1, Trigger.SPM))

    def poll(self, sample: SensorSample) -> GovernorDecision:
        """Evaluate state, RAM, faults, pending escalations and hysteresis; act on this same poll."""
        dt = self._advance_clock(sample.t_mono_s)
        state = self._state(sample)
        if state is GameState.CLOSED:
            self._add_months(dt / self._closed_spm())
        fault = self._update_faults(sample)
        delta, self._delta = min(self._delta, 1.0), 0.0  # proposal 008 §8 clarification: <= 1 month per poll
        ram = sample.available_ram_bytes
        h2_active = ram < self._cfg.h2_ram_bytes or fault
        self._h2_seen = self._h2_seen or h2_active
        if delta > 0:  # sustained H2 resets per clean month, not per clean poll
            self._h2m = self._h2m + delta if self._h2_seen else 0.0
            self._h2_seen = h2_active
        changed = self._escalate(ram, fault)
        if changed is None:
            changed = self._deescalate(ram, fault, delta)
        return self._decide(sample, state, changed)

    # -- internals --------------------------------------------------------------------------------------------

    def _queue(self, t: _Target) -> None:
        if self._pend is None or t[0] > self._pend[0]:
            self._pend = t

    def _add_months(self, k: float) -> None:
        self._months += k
        self._delta += k

    def _advance_clock(self, t: float) -> float:
        prev, self._t_prev = self._t_prev, t
        if prev is None:
            return 0.0
        dt = t - prev
        dt = dt if 0.0 <= dt <= _MAX_DT_S else 0.0
        self._elapsed += dt
        return dt

    def _closed_spm(self) -> float:
        if not self._win:
            return _CLOSED_DEFAULT_SPM_S
        med = float(statistics.median(list(self._win)[-self._cfg.spm_short_months :]))
        return max(med, 5 * self._cfg.poll_s)  # T4 floors wall_s at 0.001; never let that run months away

    def _ratio(self) -> float | None:
        cfg = self._cfg
        if len(self._win) < cfg.spm_long_months:
            return None
        vals = list(self._win)
        return float(statistics.median(vals[-cfg.spm_short_months :]) / statistics.median(vals))

    def _state(self, s: SensorSample) -> GameState:
        cfg = self._cfg
        if not s.game_running or (s.game_minimized_s or 0.0) >= cfg.closed_minimized_s:
            return GameState.CLOSED
        # ponytail: pause_detection=true behaves as PLAYING; UNVERIFIED A-25 until M1-P measures the CPU band.
        return GameState.PLAYING

    def _update_faults(self, s: SensorSample) -> bool:
        cfg = self._cfg
        rate = s.hard_fault_rate
        if self._t_first is None:
            self._t_first = s.t_mono_s
        wall = s.t_mono_s - self._t_first
        if not self._fault_frozen and (
            self._elapsed >= cfg.fault_baseline_s or (wall >= cfg.fault_baseline_s and len(self._base) >= 3)
        ):
            self._fault_frozen = True
            if self._base:
                med = statistics.median(self._base)
                mad = statistics.median([abs(x - med) for x in self._base])
                self._fault_ref = med + cfg.fault_mad_k * mad
        if not self._fault_frozen:
            if rate is not None and len(self._base) < int(cfg.fault_baseline_s / cfg.poll_s) + 1:
                self._base.append(rate)
            return False
        if rate is None or self._fault_ref is None or rate <= self._fault_ref:
            self._consec = 0
        else:
            self._consec += 1
        return self._consec >= cfg.fault_consecutive_polls

    def _ram_level(self, ram: int) -> int:
        c = self._cfg
        return 3 if ram < c.h3_ram_bytes else 2 if ram < c.h2_ram_bytes else 1 if ram < c.h1_ram_bytes else 0

    def _escalate(self, ram: int, fault: bool) -> Trigger | None:
        cands: list[_Target] = []
        if (rl := self._ram_level(ram)) > 0:
            cands.append((rl, Trigger.RAM))
        if fault:
            cands.append((2, Trigger.FAULTS))
        if self._pend is not None:
            cands.append(self._pend)
            self._pend = None
        if self._h2m >= self._cfg.h2_sustain_months_to_h3:
            cands.append((3, Trigger.SUSTAINED_H2))
        best: _Target | None = None
        for c in cands:
            if best is None or c[0] > best[0]:
                best = c
        if best is None or best[0] <= self._lvl:
            return None
        log.info("governor escalate %s -> %s (%s)", _LEVELS[self._lvl], _LEVELS[best[0]], best[1])
        self._lvl, self._clean, self._h2m = best[0], 0.0, 0.0
        if best[1] is Trigger.SPM:
            self._last_spm_esc = self._months
        return best[1]

    def _blocks_deescalation(self, ram: int, fault: bool) -> bool:
        c = self._cfg
        entry = {1: c.h1_ram_bytes, 2: c.h2_ram_bytes, 3: c.h3_ram_bytes}[self._lvl]
        if ram < entry + c.deescalate_ram_margin_bytes or fault:
            return True
        r = self._ratio()
        return self._lvl == 1 and r is not None and r >= c.spm_clear_ratio

    def _deescalate(self, ram: int, fault: bool, delta: float) -> Trigger | None:
        if self._lvl == 0:
            return None
        if self._blocks_deescalation(ram, fault):
            self._clean = 0.0
            return None
        self._clean += delta
        if self._clean < self._cfg.deescalate_after_months:
            return None
        log.info("governor deescalate %s -> %s", _LEVELS[self._lvl], _LEVELS[self._lvl - 1])
        self._lvl -= 1
        self._clean = 0.0
        return Trigger.DEESCALATE

    def _underlying(self) -> int:
        f = self._cfg.forced_level
        if f is not None:
            return _LEVELS.index(f)
        return max(self._lvl, self._floor)

    def _edges(self, u: int) -> tuple[bool, KnobCommand | None]:
        """Kill/knob from underlying-level edges; latched (not consumed) while suspended."""
        if self._suspended:
            return False, None
        prev, self._emitted_u = self._emitted_u, u
        kill = u >= 2 and u > prev
        knob = None
        if u == 3 and prev != 3:
            knob = KnobCommand(emit_period=self._cfg.h3_emit_period)
        elif prev == 3 and u != 3:
            knob = KnobCommand(emit_period=self._base_emit)
        return kill, knob

    def _decide(self, s: SensorSample, state: GameState, pressure: Trigger | None) -> GovernorDecision:
        cfg = self._cfg
        u = self._underlying()
        if u != self._prev_u:
            self._u_since = self._months
        trigger: Trigger | None = None
        if self._suspended != self._prev_susp:
            trigger = Trigger.OPERATOR
        elif u != self._prev_u and not self._suspended:
            trigger = pressure if pressure is not None else Trigger.PRESET
        self._prev_u, self._prev_susp = u, self._suspended
        kill, knob = self._edges(u)
        n, window = self._parse_fields(u)
        closed = state is GameState.CLOSED
        return GovernorDecision(
            state=state,
            level=HealthLevel.S if self._suspended else _LEVELS[u],
            parse_every_n=n,
            parse_window_only=window,
            worker_threads=max(self._cpus - 2, 1) if closed else 1,
            worker_mem_cap_bytes=cfg.worker_mem_cap_closed_bytes if closed else cfg.worker_mem_cap_bytes,
            kill_worker=kill,
            telemetry_tier=self._tier(u),
            knob_request=knob,
            trigger=trigger,
            daemon_should_exit=s.daemon_rss_bytes > cfg.daemon_rss_exit_bytes,
        )

    def _parse_fields(self, u: int) -> tuple[int, bool]:
        if u == 0:
            return 1, False
        if u == 1:
            return (3 if self._months - self._u_since >= _H1_SPARSE_MONTHS else 2), False
        return (1, True) if u == 2 else (0, False)

    def _tier(self, u: int) -> TelemetryTier:
        if u >= 2:
            return TelemetryTier.T0
        return TelemetryTier.T1 if u == 1 or not self._dev else TelemetryTier.T2
