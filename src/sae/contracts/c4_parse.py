"""C4: ``sae-parse`` stdout JSON and the synthetic save fixture spec.

CLI, exit codes and the fixture layout: ``contracts/C4_sae_parse.md``. The Rust parser (T3) emits
JSON that must validate against :class:`ParseOutput`; the daemon (T4/T5) validates it with
:data:`PARSE_OUTPUT_ADAPTER` before use.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field, Strict, TypeAdapter, model_validator

from sae.contracts.common import CLAUSEWITZ_INT_MAX, Frozen, GameCount, GameDate, SeqOrZero, Sid

SCHEMA_VERSION: Final = 1

#: Exit codes. 0 = success. 2..6 = clean failure WITH a ParseError JSON on stdout.
#: Anything else (including signals and allocation aborts) = budget breach (C7 section 7).
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_FORMAT = 4
EXIT_PARSE = 5
EXIT_UNSTABLE = 6
CLEAN_FAILURE_CODES = frozenset({EXIT_USAGE, EXIT_INPUT, EXIT_FORMAT, EXIT_PARSE, EXIT_UNSTABLE})
#: Daemon reads at most this many stdout bytes; more is treated as a breach.
MAX_STDOUT_BYTES = 4 * 1024 * 1024

#: Relative category as on the diplomacy screen: -2 pathetic .. 0 equivalent .. +2 overwhelming.
RelCat = Annotated[int, Field(ge=-2, le=2)]


class Cost(Frozen):
    """Worker self-measured cost (resource proposal section 3)."""

    rss_peak_bytes: int = Field(ge=0)
    cpu_s: float = Field(ge=0)
    wall_s: float = Field(ge=0)


class SaveInfo(Frozen):
    """Identity of the file that was parsed."""

    path: str
    size_bytes: int = Field(ge=0)
    mtime_ns: int = Field(ge=0)


class SaveMeta(Frozen):
    """Save header fields."""

    game_version: str = Field(description="As printed in the save, e.g. 'Cepheus v4.4.4'. UNVERIFIED A-19.")
    date: GameDate
    ironman: bool | None = Field(default=None, description="None if the parser cannot tell (A-20).")


class CountryInfo(Frozen):
    """One country SAE cares about: the player and every country carrying ``sae_sid``."""

    country_id: int = Field(ge=0, description="Save-internal country id.")
    name: str
    sid: Sid | None = Field(default=None, description="Value of the sae_sid variable, if set.")
    is_player: bool
    managed: bool = Field(description="Has the sae_managed country flag.")


class SelfState(Frozen):
    """A managed AI's own state: fully known to that AI, so fog-legal for its own decisions."""

    fleet_power: float = Field(ge=0)
    naval_cap_used: float = Field(ge=0)
    naval_cap: float = Field(ge=0)
    energy: float
    minerals: float
    alloys: float
    alloys_income: float
    systems_owned: int = Field(ge=0)
    wars: int = Field(ge=0)
    applied_seq: SeqOrZero = Field(description="sae_seq_applied (0 = none).")
    pending_seq: SeqOrZero = Field(description="sae_seq_pending (0 = none).")
    applied_econ: int = Field(ge=0, le=4, description="Applied EconPlan code (0 = never set).")
    applied_mil: int = Field(ge=0, le=3, description="Applied Posture code (0 = never set).")


class LegalView(Frozen):
    """What one managed AI may know about the player: the "diplomacy-screen legal" approximation.

    Every field is optional; ``None`` means the observer cannot see it (never imputed).
    """

    observer_sid: Sid
    in_contact: bool
    systems_owned: GameCount | None = Field(
        default=None,
        description="All systems owned by the player (v0 approximation, not intel-masked; A-19 UNVERIFIED).",
    )
    frontier_systems: GameCount | None = Field(default=None, description="Player systems bordering observer.")
    fortified_border_starbases: GameCount | None = None
    fleet_power_cat: RelCat | None = None
    tech_cat: RelCat | None = None
    economy_cat: RelCat | None = None
    at_war_with_observer: bool | None = None
    player_war_count: GameCount | None = Field(default=None, description="Wars the player is in (public).")

    @model_validator(mode="after")
    def _contact(self) -> LegalView:
        if not self.in_contact and any(
            v is not None for k, v in self.model_dump().items() if k not in {"observer_sid", "in_contact"}
        ):
            raise ValueError("no legal features may be set when not in contact")
        return self


class GodView(Frozen):
    """The player's true state. Written only to bus.god.jsonl; never an engine input."""

    fleet_power: float = Field(ge=0)
    systems_owned: int = Field(ge=0)
    tech_researched: int = Field(ge=0)
    alloys_income: float
    fortified_starbases: int = Field(ge=0)


class ParseResult(Frozen):
    """Successful ``sae-parse parse`` output (exit 0)."""

    schema_version: Literal[1] = SCHEMA_VERSION
    status: Literal["ok"] = "ok"
    parser_version: str
    save: SaveInfo
    meta: SaveMeta
    countries: tuple[CountryInfo, ...]
    self_state: dict[str, SelfState] = Field(description="Keyed by str(sid) of each managed AI.")
    legal: tuple[LegalView, ...] = Field(description="One entry per managed AI, about the player.")
    god: GodView | None = Field(description="None when no player country exists (observer mode).")
    sections: tuple[str, ...] = Field(description="Sections actually extracted.")
    cost: Cost

    @model_validator(mode="after")
    def _unique_observers(self) -> ParseResult:
        sids = [v.observer_sid for v in self.legal]
        if len(sids) != len(set(sids)):
            raise ValueError("observer_sid values in legal must be unique")
        return self


class ParseError(Frozen):
    """Clean-failure output (exit 2..6)."""

    schema_version: Literal[1] = SCHEMA_VERSION
    status: Literal["error"] = "error"
    parser_version: str
    exit_code: int = Field(ge=2, le=6)
    error: Literal["usage", "input", "format", "parse", "unstable"]
    detail: str = Field(max_length=2000)
    cost: Cost | None = None


ParseOutput = Annotated[ParseResult | ParseError, Field(discriminator="status")]
PARSE_OUTPUT_ADAPTER: TypeAdapter[ParseOutput] = TypeAdapter(ParseOutput)

#: Sections ``--sections`` accepts. Default: all of them.
SECTIONS = ("meta", "countries", "starbases", "fleets", "wars", "tech")

# Fixture specs are strictly typed for parity with the Rust renderer (serde, exact JSON types):
# no 3.0 for ints, no "true"/1 for bools, no "1.5" for floats. Per-field strict, not model strict,
# so Python callers may still pass lists for tuples. Proposal 002-T3.
SInt = Annotated[int, Strict()]
SBool = Annotated[bool, Strict()]
SFloat = Annotated[float, Strict()]
SStr = Annotated[str, Strict()]
#: Non-negative save id (system or country).
Id = Annotated[int, Strict(), Field(ge=0)]
StrictSid = Annotated[Sid, Strict()]
#: Renderer limit on ``pad_bytes`` (2 GiB).
MAX_PAD_BYTES = 2 * 1024**3


class FixtureCountry(Frozen):
    """One country in a synthetic save."""

    country_id: SInt = Field(ge=0)
    name: SStr = Field(min_length=1)
    sid: StrictSid | None = None
    is_player: SBool = False
    managed: SBool = False
    fleet_power: SFloat = Field(default=0, ge=0)
    naval_cap_used: SFloat = Field(default=0, ge=0)
    naval_cap: SFloat = Field(default=0, ge=0)
    energy: SFloat = 0
    minerals: SFloat = 0
    alloys: SFloat = 0
    alloys_income: SFloat = 0
    tech_researched: SInt = Field(default=0, ge=0)
    applied_seq: SInt = Field(default=0, ge=0, le=CLAUSEWITZ_INT_MAX)
    pending_seq: SInt = Field(default=0, ge=0, le=CLAUSEWITZ_INT_MAX)
    applied_econ: SInt = Field(default=0, ge=0, le=4)
    applied_mil: SInt = Field(default=0, ge=0, le=3)
    systems: tuple[Id, ...] = Field(default=(), description="Ids of owned systems.")


class FixtureStarbase(Frozen):
    """A starbase; ``defense_modules`` + ``platforms`` > 0 makes it 'fortified'."""

    system: SInt = Field(ge=0)
    owner: SInt = Field(ge=0)
    defense_modules: SInt = Field(default=0, ge=0)
    platforms: SInt = Field(default=0, ge=0)


class FixtureWar(Frozen):
    """A war between two sides of country ids."""

    attackers: tuple[Id, ...] = Field(min_length=1)
    defenders: tuple[Id, ...] = Field(min_length=1)


class FixtureSpec(Frozen):
    """Input to ``sae-parse fixture``: a small, fully specified game state.

    ``hyperlanes`` and ``contacts`` give the topology and contact graph the legal view needs.
    """

    schema_version: SInt = Field(default=SCHEMA_VERSION, ge=SCHEMA_VERSION, le=SCHEMA_VERSION)
    name: SStr = Field(pattern=r"^[a-z0-9_]+$")
    game_version: SStr = "Cepheus v4.4.4"
    date: Annotated[GameDate, Strict()]
    ironman: SBool = False
    format: Literal["zip_text", "plain_text"] = "zip_text"
    countries: tuple[FixtureCountry, ...] = Field(min_length=1)
    hyperlanes: tuple[tuple[Id, Id], ...] = ()
    contacts: tuple[tuple[Id, Id], ...] = Field(default=(), description="Pairs of country ids in contact.")
    starbases: tuple[FixtureStarbase, ...] = ()
    wars: tuple[FixtureWar, ...] = ()
    pad_bytes: SInt = Field(default=0, ge=0, le=MAX_PAD_BYTES, description="Inert filler for size/pathological tests.")

    @model_validator(mode="after")
    def _check(self) -> FixtureSpec:
        ids = [c.country_id for c in self.countries]
        if len(ids) != len(set(ids)):
            raise ValueError("country_id values must be unique")
        if sum(c.is_player for c in self.countries) > 1:
            raise ValueError("at most one player country")
        sids = [c.sid for c in self.countries if c.sid is not None]
        if len(sids) != len(set(sids)):
            raise ValueError("sid values must be unique")
        if any(a == b for a, b in self.hyperlanes):
            raise ValueError("self-loop hyperlane")
        if any(a == b for a, b in self.contacts):
            raise ValueError("self-contact")
        return self
