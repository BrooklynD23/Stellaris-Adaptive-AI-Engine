"""C3: Intent schema v1 and its deterministic mapping to C2 commands.

Semantics: ``contracts/C3_intents.md``. No planner ever writes console text: intents are validated
(T4 validator, rules in C3 section 4) and then compiled by :func:`compile_batch`, whose output is
rendered by :func:`sae.contracts.c2_ini.render_ini`.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field, TypeAdapter, model_validator

from sae.contracts.c2_ini import CountryCommand, IniFile, KnobCommand
from sae.contracts.common import EconPlan, Frozen, GameDate, MonthIndex, Posture, ReasonKey, Seq, Sid, Stance

SCHEMA_VERSION: Final = 1

#: Months without an ACK before an emitted batch becomes LOST (report: K = 2).
ACK_TIMEOUT_MONTHS = 2
#: Re-emissions allowed after LOST before the engine raises an alarm (report: once).
MAX_ATTEMPTS = 2

IntentId = Annotated[str, Field(pattern=r"^i-\d{6}$")]
DecisionId = Annotated[str, Field(pattern=r"^d-\d{4}\.\d{2}-\d{2}$")]


class _IntentBase(Frozen):
    """Fields every intent carries."""

    schema_version: Literal[1] = SCHEMA_VERSION
    intent_id: IntentId = Field(description="Engine-assigned, unique within a run.")
    decision_id: DecisionId = Field(description="The decision chain this intent belongs to (C5).")
    country: Sid = Field(description="The managed AI country the intent steers.")
    ttl_months: int = Field(default=3, ge=1, le=36, description="Mod rejects the batch after this many months.")
    reason_key: ReasonKey | None = Field(default=None, description="Flavour event key; None = no flavour event.")
    source: Literal["engine", "operator"] = "engine"


class SetEconPlan(_IntentBase):
    """Select the economic plan flag that gates SAE's additive budget/subplan entries."""

    kind: Literal["SET_ECON_PLAN"] = "SET_ECON_PLAN"
    plan: EconPlan


class SetMilitaryPosture(_IntentBase):
    """Select the military posture budget weights, aimed at one target country."""

    kind: Literal["SET_MILITARY_POSTURE"] = "SET_MILITARY_POSTURE"
    posture: Posture
    target: Sid = Field(description="Required: the opinion modifier needs a target (advisor).")


class DiploStance(_IntentBase):
    """Set the diplomatic stance flag and opinion modifier toward one target."""

    kind: Literal["DIPLO_STANCE"] = "DIPLO_STANCE"
    stance: Stance
    target: Sid


class NoopHeartbeat(_IntentBase):
    """Changes no knob; advances ``seq`` so the round trip can be tested end to end."""

    kind: Literal["NOOP_HEARTBEAT"] = "NOOP_HEARTBEAT"


Intent = Annotated[
    SetEconPlan | SetMilitaryPosture | DiploStance | NoopHeartbeat,
    Field(discriminator="kind"),
]
INTENT_ADAPTER: TypeAdapter[Intent] = TypeAdapter(Intent)

#: Intent kinds reserved for a later schema version; the validator rejects them in v1.
#: ``FLEET_COMPOSITION_BIAS`` was dropped in C3 v1.1 (maintainer 2026-10-19) until M0-7 proves an AI
#: hook for the per-size knob (A-22); its C2 slots ``sae_p_ship_<size>`` stay on the wire, always -1.
RESERVED_KINDS = frozenset({"PRIORITIZE_TECH", "FLEET_COMPOSITION_BIAS"})


class IntentBatch(Frozen):
    """Everything emitted in one ``sae_in.ini`` write. One batch = one ``seq``."""

    schema_version: Literal[1] = SCHEMA_VERSION
    seq: Seq
    attempt: int = Field(default=1, ge=1, le=MAX_ATTEMPTS, description="2 = the single re-emission after LOST.")
    issued_date: GameDate
    issued_m: MonthIndex = Field(description="Latest HB month counter seen when the batch was compiled.")
    intents: tuple[Intent, ...] = ()
    knobs: KnobCommand | None = None

    @model_validator(mode="after")
    def _check(self) -> IntentBatch:
        if not self.intents and self.knobs is None:
            raise ValueError("a batch needs at least one intent or a knob change")
        seen: set[tuple[int, str]] = set()
        for intent in self.intents:
            key = (intent.country, intent.kind)
            if key in seen:
                raise ValueError(f"country {intent.country} has two {intent.kind} intents")
            seen.add(key)
        ids = [i.intent_id for i in self.intents]
        if len(ids) != len(set(ids)):
            raise ValueError("intent_id values must be unique in a batch")
        return self


def _country_command(batch: IntentBatch, country: int, intents: list[Intent]) -> CountryCommand:
    fields: dict[str, object] = {
        "sid": country,
        "seq": batch.seq,
        "expires": batch.issued_m + min(i.ttl_months for i in intents),
        "reason": next((i.reason_key.code for i in intents if i.reason_key is not None), 0),
    }
    for intent in intents:
        if isinstance(intent, SetEconPlan):
            fields["econ"] = intent.plan.code
        elif isinstance(intent, SetMilitaryPosture):
            fields["mil"], fields["mil_target"] = intent.posture.code, intent.target
        elif isinstance(intent, DiploStance):
            fields["dip"], fields["dip_target"] = intent.stance.code, intent.target
    return CountryCommand.model_validate(fields)


def compile_batch(batch: IntentBatch) -> IniFile:
    """Map a validated batch to the C2 file model (C3 section 3). Pure and deterministic.

    Raises:
        pydantic.ValidationError: the batch maps to an illegal C2 line (e.g. a self-target).
    """
    by_country: dict[int, list[Intent]] = {}
    for intent in batch.intents:
        by_country.setdefault(intent.country, []).append(intent)
    countries = tuple(_country_command(batch, sid, by_country[sid]) for sid in sorted(by_country))
    return IniFile(knobs=batch.knobs, countries=countries)
