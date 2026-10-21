"""C7: Resource Governor contract: states, health ladder, thresholds, sensors, worker spawn.

Semantics: ``contracts/C7_governor.md`` (resource proposal section 4 as amended by the signoff).
T5 implements :class:`Governor`, :class:`Sensors` and :class:`Platform`; T4's daemon drives them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol

from pydantic import Field, model_validator

from sae.contracts.c2_ini import KnobCommand
from sae.contracts.c4_parse import Cost
from sae.contracts.common import Frozen, GameDate

MIB = 1024 * 1024
GIB = 1024 * MIB


class GameState(StrEnum):
    """Opportunity axis. PAUSED is only produced when ``pause_detection`` is on (M1-P passed)."""

    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    CLOSED = "CLOSED"


class HealthLevel(StrEnum):
    """Pressure axis. S is entered only by the operator ``pause`` command (C9)."""

    H0 = "H0"
    H1 = "H1"
    H2 = "H2"
    H3 = "H3"
    S = "S"


class TelemetryTier(StrEnum):
    """Resource proposal section 7 tiers."""

    T0 = "T0"
    T1 = "T1"
    T2 = "T2"


class Trigger(StrEnum):
    """Why the level changed."""

    RAM = "ram"
    FAULTS = "faults"
    SPM = "spm"
    SUSTAINED_H2 = "sustained_h2"
    PRESET = "preset"
    OPERATOR = "operator"
    DEESCALATE = "deescalate"
    BUDGET_BREACH = "budget_breach"


class GovernorConfig(Frozen):
    """Every Governor number. Defaults are the amended resource proposal; presets override some."""

    poll_s: float = Field(default=2.0, ge=2.0, description="Nothing polls faster than 2 s.")
    h1_ram_bytes: int = Field(default=2 * GIB, gt=0)
    h2_ram_bytes: int = Field(default=int(1.5 * GIB), gt=0)
    h3_ram_bytes: int = Field(default=1 * GIB, gt=0)
    deescalate_ram_margin_bytes: int = Field(default=GIB // 2, ge=0)
    spm_short_months: int = Field(default=6, ge=1)
    spm_long_months: int = Field(default=24, ge=2)
    spm_h1_ratio: float = Field(default=1.10, gt=1.0)
    spm_clear_ratio: float = Field(default=1.05, gt=1.0)
    spm_step_reset: float = Field(default=0.40, gt=0, description="Month-to-month jump treated as a speed change.")
    spm_min_months_between_escalations: int = Field(default=6, ge=1)
    fault_baseline_s: float = Field(default=600.0, gt=0, description="First 10 min of the session.")
    fault_mad_k: float = Field(default=3.0, gt=0)
    fault_mad_floor: float = Field(
        default=1.0, ge=0, description="Pages/s; the fault trigger uses max(MAD, floor) (C7 section 4.2)."
    )
    fault_consecutive_polls: int = Field(default=3, ge=1)
    h2_sustain_months_to_h3: int = Field(default=6, ge=1)
    deescalate_after_months: int = Field(default=6, ge=1)
    reach_within_s: float = Field(default=10.0, gt=0, description="CI-3 / R-4 end-to-end bound.")
    daemon_rss_breach_bytes: int = Field(default=150 * MIB, gt=0)
    daemon_rss_exit_bytes: int = Field(default=300 * MIB, gt=0)
    worker_mem_cap_bytes: int = Field(default=1 * GIB, gt=0)
    worker_mem_cap_closed_bytes: int = Field(default=2 * GIB, gt=0)
    parse_stable_s: float = Field(default=2.0, ge=0)
    parse_min_age_s: float = Field(default=8.0, ge=0)
    parse_timeout_s: float = Field(default=60.0, gt=0)
    h2_parse_window_months: int = Field(default=3, ge=0)
    h3_emit_period: int = Field(default=12, ge=1, le=12)
    pause_detection: bool = Field(default=False, description="Off until M1-P passes.")
    paused_debounce_s: float = Field(default=5.0, ge=4.0, le=6.0)
    paused_cpu_max_pct: float | None = Field(
        default=None, ge=0, description="Game CPU % of one core at or below which it counts as paused (from M1-P)."
    )
    closed_minimized_s: float = Field(default=600.0, gt=0)
    forced_level: HealthLevel | None = Field(default=None, description="Test hook (CI-1); None in play.")

    @model_validator(mode="after")
    def _check(self) -> GovernorConfig:
        if not self.h3_ram_bytes < self.h2_ram_bytes < self.h1_ram_bytes:
            raise ValueError("RAM thresholds must satisfy h3 < h2 < h1")
        margin = self.deescalate_ram_margin_bytes
        if self.h3_ram_bytes + margin < self.h2_ram_bytes or self.h2_ram_bytes + margin < self.h1_ram_bytes:
            raise ValueError("de-escalation margin must bridge the RAM bands: h3 + margin >= h2 and h2 + margin >= h1")
        if self.spm_clear_ratio >= self.spm_h1_ratio:
            raise ValueError("spm_clear_ratio must be below spm_h1_ratio")
        if self.spm_short_months >= self.spm_long_months:
            raise ValueError("spm_short_months must be below spm_long_months")
        if self.daemon_rss_breach_bytes >= self.daemon_rss_exit_bytes:
            raise ValueError("daemon RSS breach threshold must be below the exit threshold")
        if self.forced_level is HealthLevel.S:
            raise ValueError("level S is operator-only (C9 pause); forced_level cannot be S")
        if self.pause_detection and self.paused_cpu_max_pct is None:
            raise ValueError("pause_detection needs paused_cpu_max_pct (measured in M1-P)")
        return self


class SensorSample(Frozen):
    """One 2 s poll of cheap probes. ``None`` = probe unavailable on this platform."""

    t_mono_s: float = Field(ge=0)
    available_ram_bytes: int = Field(ge=0)
    hard_fault_rate: float | None = Field(default=None, ge=0, description="Pages input/s (UNVERIFIED source).")
    game_running: bool
    game_cpu_s: float | None = Field(default=None, ge=0, description="Cumulative CPU time of the game process.")
    game_minimized_s: float | None = Field(default=None, ge=0)
    daemon_rss_bytes: int = Field(ge=0)
    daemon_cpu_pct: float = Field(ge=0, description="Percent of one core over the last poll.")


class GovernorDecision(Frozen):
    """What the daemon may do until the next poll."""

    state: GameState
    level: HealthLevel
    parse_every_n: int = Field(ge=0, description="Parse every n-th eligible autosave; 0 = none.")
    parse_window_only: bool = Field(description="H2: parse only within h2_parse_window_months of a boundary.")
    worker_threads: int = Field(ge=0)
    worker_mem_cap_bytes: int = Field(ge=0)
    kill_worker: bool
    telemetry_tier: TelemetryTier
    knob_request: KnobCommand | None = Field(description="Queue into the next sae_in.ini (H3 / preset).")
    trigger: Trigger | None = Field(description="Set on the poll where the level changed.")
    daemon_should_exit: bool = Field(description="Daemon RSS above daemon_rss_exit_bytes.")


class SpmSample(Frozen):
    """Seconds per game month, measured at poll resolution (C7 section 4.3).

    ``wall_s`` = wall time between the previous poll that saw an HB and this one, divided by the
    number of HB lines this poll saw. Always > 0 because polls are >= 2 s apart.
    """

    game_date: GameDate
    wall_s: float = Field(gt=0)
    months: int = Field(default=1, ge=1, description="HB lines seen in this poll.")


class WorkerOutcome(Frozen):
    """How one parse worker run ended, classified per C4 exit codes and C7 section 7."""

    kind: Literal["ok", "clean_failure", "budget_breach", "killed_by_governor"]
    exit_code: int | None
    cost: Cost | None
    detail: str | None = None


class Governor(Protocol):
    """Pure decision logic. No I/O: sensors and clock come in, decisions come out (CI-3 testable)."""

    def poll(self, sample: SensorSample) -> GovernorDecision:
        """Evaluate RAM/fault/state triggers; act on this same poll."""
        ...

    def on_month(self, sample: SpmSample) -> None:
        """Feed one spm sample (from HB lines); drives spm triggers and month-based hysteresis."""
        ...

    def on_worker(self, outcome: WorkerOutcome) -> None:
        """Record a worker outcome; a budget breach raises the level per C7 section 7."""
        ...

    def set_next_boundary(self, date: GameDate | None) -> None:
        """Next commitment boundary from the engine (used by the H2 parse window)."""
        ...

    def suspend(self, suspended: bool) -> None:
        """Enter or leave level S (operator pause/resume)."""
        ...


class Sensors(Protocol):
    """Injectable probe set (psutil/PDH in production, scripted traces in tests)."""

    def sample(self) -> SensorSample:
        """Take one sample. Must not block longer than 100 ms."""
        ...


class WorkerSpec(Frozen):
    """How to launch one ``sae-parse`` run (C4 CLI)."""

    exe: str
    args: tuple[str, ...]
    mem_cap_bytes: int = Field(gt=0)
    timeout_s: float = Field(gt=0)
    threads: int = Field(ge=1)


class WorkerHandle(Protocol):
    """A running worker."""

    async def wait(self) -> tuple[int | None, bytes, bytes]:
        """Wait for exit; return (exit code or None if killed by signal, stdout, stderr)."""
        ...

    def kill(self) -> None:
        """Terminate now. Idempotent."""
        ...


class Platform(Protocol):
    """OS controls. Windows-only calls sit behind platform checks (C7 section 8)."""

    def lower_self_priority(self) -> None:
        """Daemon: BELOW_NORMAL + EcoQoS (Windows) or nice 10 (Linux). Never touches the game."""
        ...

    async def spawn_worker(self, spec: WorkerSpec) -> WorkerHandle:
        """Spawn ``sae-parse`` at IDLE priority inside its own memory-capped Job / scope."""
        ...
