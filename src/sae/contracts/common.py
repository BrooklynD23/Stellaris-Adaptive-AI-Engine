"""Shared types for all SAE contracts (C1-C9).

Every enum here is part of a frozen contract. Integer codes are the values written into the game
(C2) and read back from it (C1); changing one is a breaking change that needs a proposal.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

#: Largest absolute integer SAE ever writes into a Clausewitz variable. Stellaris variables are
#: fixed-point; the exact range is UNVERIFIED (A-17), so SAE stays far inside +-2,147,483.
CLAUSEWITZ_INT_MAX = 1_000_000

_DATE_RE = re.compile(r"^(\d{1,4})\.(\d{1,2})\.(\d{1,2})$")


def normalize_game_date(value: str) -> str:
    """Return a game date as zero-padded ``YYYY.MM.DD``.

    Accepts 1-2 digit months and days because the game's date printing is UNVERIFIED (A-05).

    Raises:
        ValueError: if the text is not a date or month/day are out of range.
    """
    match = _DATE_RE.match(value)
    if match is None:
        raise ValueError(f"not a game date: {value!r}")
    year, month, day = (int(g) for g in match.groups())
    if not 1 <= month <= 12 or not 1 <= day <= 30:
        raise ValueError(f"game date out of range: {value!r}")
    return f"{year:04d}.{month:02d}.{day:02d}"


#: A Stellaris in-game date, normalised to ``YYYY.MM.DD`` (Stellaris months have 30 days).
GameDate = Annotated[str, AfterValidator(normalize_game_date)]

#: SAE country id: a positive integer the mod assigns on a country's first emit (C1 ``sid``).
Sid = Annotated[int, Field(ge=1, le=CLAUSEWITZ_INT_MAX)]

#: Batch sequence number written to ``sae_seq_pending`` (C2) and echoed in ACK lines (C1).
Seq = Annotated[int, Field(ge=1, le=CLAUSEWITZ_INT_MAX)]

#: Mod month counter ``m`` (C1 HB): months since the mod first ran in this save.
MonthIndex = Annotated[int, Field(ge=0, le=CLAUSEWITZ_INT_MAX)]

#: A seq read back from the game where 0 means "none yet" (``sae_seq_applied``/``sae_seq_pending``).
SeqOrZero = Annotated[int, Field(ge=0, le=CLAUSEWITZ_INT_MAX)]

#: A non-negative game count; bounded like every other integer that crosses the game boundary (A-17).
GameCount = Annotated[int, Field(ge=0, le=CLAUSEWITZ_INT_MAX)]


class Frozen(BaseModel):
    """Base for every contract model: immutable and strict about unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Archetype(StrEnum):
    """Player archetypes the v0/v1 belief filter tracks (settled decision)."""

    TURTLE = "turtle"
    TECH_RUSH = "tech_rush"
    EARLY_AGGRESSION = "early_aggression"
    BALANCED = "balanced"


class Strategy(StrEnum):
    """Counter-strategy templates. ``baseline`` is the safety-floor default."""

    OUTSCALE_SIEGE = "outscale_siege"
    EARLY_PUNISH = "early_punish"
    HOLD_COUNTERPUNCH = "hold_counterpunch"
    BASELINE = "baseline"


#: The v0/v1 counter table (C3 section 6). Below the belief threshold the engine uses BASELINE.
COUNTER_TABLE: dict[Archetype, Strategy] = {
    Archetype.TURTLE: Strategy.OUTSCALE_SIEGE,
    Archetype.TECH_RUSH: Strategy.EARLY_PUNISH,
    Archetype.EARLY_AGGRESSION: Strategy.HOLD_COUNTERPUNCH,
    Archetype.BALANCED: Strategy.BASELINE,
}


class _Coded(StrEnum):
    """A string enum whose 1-based declaration order is the integer written into the game."""

    @property
    def code(self) -> int:
        """Integer code used in C2 variables and C1 fields (declaration order, starting at 1)."""
        return list(type(self)).index(self) + 1

    @classmethod
    def from_code(cls, code: int) -> _Coded:
        """Inverse of :attr:`code`. Raises ``ValueError`` for an unknown code."""
        members = list(cls)
        if not 1 <= code <= len(members):
            raise ValueError(f"{cls.__name__} has no code {code}")
        return members[code - 1]


class EconPlan(_Coded):
    """``SET_ECON_PLAN`` values; ``code`` is written to ``sae_p_econ``."""

    BALANCED = "balanced"
    MILITARY = "military"
    RESEARCH = "research"
    EXPANSION = "expansion"


class Posture(_Coded):
    """``SET_MILITARY_POSTURE`` values; ``code`` is written to ``sae_p_mil``."""

    DEFENSIVE = "defensive"
    DETERRENT = "deterrent"
    OFFENSIVE = "offensive"


class Stance(_Coded):
    """``DIPLO_STANCE`` values; ``code`` is written to ``sae_p_dip``."""

    FRIENDLY = "friendly"
    NEUTRAL = "neutral"
    RIVAL = "rival"
    HOSTILE = "hostile"


class ShipSize(StrEnum):
    """Standard hull sizes naming the reserved C2 slots ``sae_p_ship_<size>`` (always -1 in v1.1; A-22)."""

    CORVETTE = "corvette"
    FRIGATE = "frigate"
    DESTROYER = "destroyer"
    CRUISER = "cruiser"
    BATTLESHIP = "battleship"


class ReasonKey(_Coded):
    """Pre-authored localisation keys for the in-game flavour event.

    ``code`` is written to ``sae_p_reason``; 0 means "no reason / do not fire". The localisation key
    is ``sae_reason_<value>``; T1 authors the strings.
    """

    TURTLE_SIEGE = "turtle_siege"
    TECH_RUSH_PUNISH = "tech_rush_punish"
    AGGRESSION_HOLD = "aggression_hold"
    BASELINE = "baseline"
    OPERATOR_FORCED = "operator_forced"
    SAFETY_FLOOR = "safety_floor"


class AckStatus(StrEnum):
    """Outcome of one (country, seq) batch. The first five come from the mod (C1 ACK);
    ``LOST`` and ``SUPERSEDED`` are assigned by the engine (C3 section 5)."""

    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    STALE = "STALE"
    DUPLICATE = "DUPLICATE"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    LOST = "LOST"
    SUPERSEDED = "SUPERSEDED"


#: Statuses the mod itself may print in a C1 ACK line.
MOD_ACK_STATUSES = frozenset(
    {AckStatus.APPLIED, AckStatus.REJECTED, AckStatus.STALE, AckStatus.DUPLICATE, AckStatus.SCHEMA_MISMATCH}
)


class AckCode(StrEnum):
    """Machine-readable reason in an ACK line (``code=``)."""

    OK = "OK"
    RANGE = "RANGE"
    NOT_MANAGED = "NOT_MANAGED"
    BAD_TARGET = "BAD_TARGET"
    MULTIPLAYER = "MULTIPLAYER"
    SEQ_OLD = "SEQ_OLD"
    EXPIRED = "EXPIRED"
    SCHEMA = "SCHEMA"
    TIMEOUT = "TIMEOUT"
    NEWER_BATCH = "NEWER_BATCH"
