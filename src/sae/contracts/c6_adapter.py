"""C6: the GameAdapter protocol, the Observation model and the fog-of-war boundary.

Semantics: ``contracts/C6_game_adapter.md``. The engine core (T4) depends only on this module,
never on ``sae.adapters``. :class:`Observation` has no field that can carry god-view data; god-view
data travels only as :class:`GodObservation` to the god sink.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from enum import StrEnum
from typing import Final, Literal, Protocol, TypeAlias, runtime_checkable

from pydantic import Field

from sae.contracts.c3_intent import IntentBatch
from sae.contracts.c4_parse import GodView, LegalView, ParseResult
from sae.contracts.common import AckCode, AckStatus, FolderKey, Frozen, GameDate, MonthIndex, Seq, SeqOrZero, Sid

SCHEMA_VERSION: Final = 1


class AdapterMode(StrEnum):
    """Where observations come from and where intents go."""

    LIVE = "live"
    REPLAY = "replay"
    SYNTH = "synth"
    RELEASE = "release"


class GameEvent(StrEnum):
    """Discrete things the adapter noticed this observation."""

    HELLO = "hello"
    DATE_REGRESSED = "date_regressed"
    SAVE_PARSED = "save_parsed"


class ManagedState(Frozen):
    """A managed AI's own state, merged from its latest SNAP line and latest save."""

    sid: Sid
    name: str
    applied_seq: SeqOrZero = Field(description="sae_seq_applied from SNAP or save (0 = none yet).")
    pending_seq: SeqOrZero | None = Field(default=None, description="sae_seq_pending; save only (SNAP lacks it).")
    applied_econ: int | None = Field(default=None, ge=0, le=4)
    applied_mil: int | None = Field(default=None, ge=0, le=3)
    fleet_power: float | None = Field(default=None, ge=0)
    naval_cap_used: float | None = Field(default=None, ge=0)
    naval_cap: float | None = Field(default=None, ge=0)
    energy: float | None = None
    minerals: float | None = None
    alloys: float | None = None
    alloys_income: float | None = None
    systems_owned: int | None = Field(default=None, ge=0)
    wars: int | None = Field(default=None, ge=0)
    snap_date: GameDate | None = Field(default=None, description="Date of the SNAP these values came from.")


class Observation(Frozen):
    """One fog-legal observation, yielded at most once per game month plus once per parsed save."""

    schema_version: Literal[1] = SCHEMA_VERSION
    game_date: GameDate
    m: MonthIndex | None = Field(description="Mod month counter from HB; None before the first HB.")
    session_epoch: int = Field(ge=0, description="+1 on HELLO or a date regression (save reload).")
    source: Literal["log", "save"]
    player_sid: Sid | None
    managed: tuple[ManagedState, ...]
    player_legal: tuple[LegalView, ...] = Field(description="Per managed observer; from the latest save.")
    legal_as_of: GameDate | None = Field(description="Save date player_legal was computed from.")
    events: tuple[GameEvent, ...] = ()
    save_folder_key: FolderKey | None = Field(
        default=None, description="Salted key of the latest parsed save's folder (C6 v1.3 section 2); carried forward."
    )
    game_version: str | None = Field(
        default=None, max_length=64, description="Latest parsed save's meta.game_version (C4); carried forward."
    )


class GodObservation(Frozen):
    """God-view data: the parser's GodView and raw PLYR fields. Written to bus.god.jsonl only."""

    schema_version: Literal[1] = SCHEMA_VERSION
    game_date: GameDate
    source: Literal["log", "save"]
    player: GodView | None = None
    plyr: dict[str, float] = Field(default_factory=dict, description="Numeric PLYR fields, C1 keys.")


class IntentAck(Frozen):
    """A mod ACK (C1) as delivered to the engine, or an adapter-simulated one in replay/synth."""

    seq: Seq
    country: Sid
    status: AckStatus
    code: AckCode
    game_date: GameDate
    reason: int | None = Field(default=None, ge=0, le=6)
    applied: SeqOrZero | None = Field(default=None, description="Mod's sae_seq_applied (DUPLICATE acks).")


class EmitReceipt(Frozen):
    """Result of :meth:`GameAdapter.emit`. ``ok`` means the file is in place, NOT that it was applied."""

    seq: Seq = Field(description="The batch seq.")
    ok: bool
    path: str | None = None
    bytes_written: int = Field(default=0, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error: Literal["write_failed", "release_mode", "closed", "multiplayer", "ironman"] | None = Field(
        default=None,
        description=(
            "multiplayer = refused, the mod reported mp=1 (C6 section 1.6); ironman = refused, Ironman detected "
            "(C6 section 1.10); nothing written in either case."
        ),
    )
    detail: str | None = None


class GameClock(Frozen):
    """The adapter's view of game time."""

    game_date: GameDate | None
    m: MonthIndex | None
    session_epoch: int = Field(ge=0)


class SaveParser(Protocol):
    """Injected by the daemon: runs ``sae-parse`` under Governor control (C7) and classifies the result."""

    async def parse(self, save_path: str, *, sections: tuple[str, ...] | None = None) -> ParseResult | None:
        """Return the parse result, or None if the Governor skipped it or the worker failed
        (the failure is already reported as a ``health`` event by the caller).

        ``sections`` is passed as ``sae-parse --sections`` (C4 section 1); ``None`` = all sections.
        The LiveAdapter passes ``("meta",)`` for ``ironman.sav`` (C6 section 1.10, v1.2)."""
        ...


class GodSink(Protocol):
    """Where an adapter writes god-view data (the bus writer's god file in practice)."""

    def write_god(self, obs: GodObservation) -> None:
        """Persist one god observation. Must not raise into the adapter on I/O failure."""
        ...


@runtime_checkable
class GameAdapter(Protocol):
    """The single seam between the engine and a game (dev-env plan protocol, advisor's pick)."""

    @property
    def mode(self) -> AdapterMode:
        """Which adapter this is."""
        ...

    def observations(self) -> AsyncIterator[Observation]:
        """Fog-legal observations in game-date order (non-decreasing within a session_epoch)."""
        ...

    async def emit(self, batch: IntentBatch) -> EmitReceipt:
        """Compile (C3) and write (C2) one batch. Never raises for I/O; reports in the receipt."""
        ...

    def acks(self) -> AsyncIterator[IntentAck]:
        """ACKs in arrival order. Duplicates are possible and must be tolerated by the consumer."""
        ...

    def clock(self) -> GameClock:
        """Current game time as last observed."""
        ...

    async def close(self) -> None:
        """Stop iterators and release files. Idempotent."""
        ...


#: Builds an adapter from the daemon's governed parser and the bus writer's god sink (C8 section 5,
#: proposal 004-T4). The daemon calls it once.
AdapterFactory: TypeAlias = Callable[[SaveParser, GodSink], GameAdapter]
