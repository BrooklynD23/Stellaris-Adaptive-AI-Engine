"""C9: control plane (``sae ctl``): file-inbox commands and results.

Semantics: ``contracts/C9_control_plane.md``. v0 commands: pause, resume, force_strategy, status.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import AwareDatetime, Field, TypeAdapter

from sae.contracts.c7_governor import GameState, HealthLevel
from sae.contracts.common import Archetype, Frozen, GameDate, Strategy

SCHEMA_VERSION: Final = 1
MAX_COMMAND_BYTES = 64 * 1024
RESULT_TIMEOUT_S = 10.0
COMMAND_MAX_AGE_S = 60.0  # proposal 016: older at pickup -> error="expired"
COMMAND_MAX_FUTURE_S = 5.0  # clock-skew allowance for issued_at ahead of the daemon clock

CommandId = Annotated[str, Field(pattern=r"^c-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")]


class Pause(Frozen):
    """Stop deciding and emitting; observation and logging continue (level S)."""

    cmd: Literal["pause"] = "pause"


class Resume(Frozen):
    """Leave level S."""

    cmd: Literal["resume"] = "resume"


class ForceStrategy(Frozen):
    """Override the counter table. ``strategy=None`` clears an active override."""

    cmd: Literal["force_strategy"] = "force_strategy"
    strategy: Strategy | None
    months: int = Field(default=24, ge=1, le=120, description="Override length in game months.")


class Status(Frozen):
    """Read-only snapshot; never logged as a state change."""

    cmd: Literal["status"] = "status"


Command = Annotated[Pause | Resume | ForceStrategy | Status, Field(discriminator="cmd")]


class ControlCommand(Frozen):
    """One file in ``runs/<run_id>/control/inbox/``."""

    v: Literal[1] = SCHEMA_VERSION
    command_id: CommandId
    issued_at: AwareDatetime
    operator: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=500)
    command: Command


class EngineStatus(Frozen):
    """Answer to ``status`` (also attached to every result)."""

    run_id: str
    game_date: GameDate | None
    suspended: bool
    state: GameState
    level: HealthLevel
    strategy: Strategy
    forced_until: GameDate | None
    commit_until: GameDate | None
    top_archetype: Archetype | None
    in_flight_batches: int = Field(ge=0)


class ControlResult(Frozen):
    """Written to ``control/done/<command_id>.result.json``."""

    v: Literal[1] = SCHEMA_VERSION
    command_id: str = Field(description="Echo; may be 'unknown' when the command file was unreadable.")
    accepted: bool
    error: Literal["invalid", "too_large", "unknown_cmd", "not_running", "expired"] | None = None
    detail: str | None = Field(default=None, max_length=2000)
    applied_at: GameDate | None = Field(description="Game date of the tick boundary where it took effect.")
    status: EngineStatus | None


CONTROL_COMMAND_ADAPTER: TypeAdapter[ControlCommand] = TypeAdapter(ControlCommand)


def command_expired(issued_at: datetime, now: datetime, session_started_at: datetime) -> str | None:
    """C9 section 3.1: ``None`` if the command may be applied, else the ``detail`` for ``error="expired"``.

    Checked once, when the daemon picks the file up from ``inbox/`` (not again at the tick boundary).
    All three arguments must be timezone-aware.
    """
    if issued_at < session_started_at:
        return "issued before this daemon session started"
    age = (now - issued_at).total_seconds()
    if age > COMMAND_MAX_AGE_S:
        return f"issued {age:.0f} s ago (max {COMMAND_MAX_AGE_S:.0f} s)"
    if -age > COMMAND_MAX_FUTURE_S:
        return f"issued_at is {-age:.0f} s in the future (max {COMMAND_MAX_FUTURE_S:.0f} s)"
    return None
