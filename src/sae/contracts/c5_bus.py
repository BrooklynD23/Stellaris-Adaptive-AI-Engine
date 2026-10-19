"""C5: ``bus.jsonl`` envelope and every event type.

Semantics: ``contracts/C5_bus.md``. One JSON object per line; validate with :data:`BUS_EVENT_ADAPTER`.
God-view events live in ``bus.god.jsonl`` only (:class:`ObservationGodEvent`).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from sae.contracts.c3_intent import DecisionId, Intent, IntentBatch, IntentId
from sae.contracts.c6_adapter import AdapterMode, EmitReceipt, GodObservation, Observation
from sae.contracts.c7_governor import GameState, HealthLevel, Trigger
from sae.contracts.c8_config import PresetName
from sae.contracts.c9_control import ControlCommand, ControlResult
from sae.contracts.common import AckCode, AckStatus, Archetype, Frozen, GameDate, ReasonKey, Seq, Sid, Strategy

BUS_VERSION: Final = 1
BUS_FILE = "bus.jsonl"
GOD_FILE = "bus.god.jsonl"
MAX_LINE_BYTES = 1024 * 1024

RunId = Annotated[str, Field(pattern=r"^\d{8}-\d{6}-(live|replay|synth|release)-[0-9a-f]{4}$")]
Prob = Annotated[float, Field(ge=0.0, le=1.0)]


class _Envelope(Frozen):
    """Envelope fields shared by every event (report: v, run_id, seq, t_wall, game_date, type, ...)."""

    v: Literal[1] = BUS_VERSION
    run_id: RunId
    seq: int = Field(ge=0, description="Strictly increasing per file, starting at 0.")
    t_wall: AwareDatetime = Field(description="UTC, ISO 8601 with Z.")
    t_mono_ns: int = Field(ge=0, description="time.monotonic_ns() of the writer; ordering within a run only.")
    game_date: GameDate | None
    decision_id: DecisionId | None = None


class RunStartedPayload(Frozen):
    """First event of every bus file."""

    engine_version: str
    contracts: dict[str, int] = Field(description="Contract id -> version, e.g. {'C1': 1}.")
    adapter_mode: AdapterMode
    preset: PresetName
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    platform: str
    python: str


class RunStartedEvent(_Envelope):
    """``run_started``: identifies the run; config itself is in config.resolved.json (C8)."""

    type: Literal["run_started"] = "run_started"
    fow: Literal["none"] = "none"
    payload: RunStartedPayload


class ObservationEvent(_Envelope):
    """``observation``: exactly what the engine saw (C6 Observation)."""

    type: Literal["observation"] = "observation"
    fow: Literal["legal"] = "legal"
    payload: Observation


class ObservationGodEvent(_Envelope):
    """``observation_god``: GOD-VIEW, only in bus.god.jsonl, never read by the engine."""

    type: Literal["observation_god"] = "observation_god"
    fow: Literal["god"] = "god"
    payload: GodObservation


class Evidence(Frozen):
    """One feature's contribution to the update."""

    feature: str
    archetype: Archetype
    llr: float = Field(description="Clipped log-likelihood ratio vs the mean of the other archetypes.")


class BeliefUpdatePayload(Frozen):
    """Forgetful Bayes step (report section 'engine core')."""

    prior: dict[Archetype, Prob]
    loglik: dict[Archetype, float] = Field(description="Sum of clipped per-feature log-likelihoods.")
    posterior: dict[Archetype, Prob]
    entropy: float = Field(ge=0)
    features: dict[str, float | None] = Field(description="Feature values used; None = unobserved.")
    top_evidence: tuple[Evidence, ...] = Field(max_length=5)
    change_score: float | None = Field(default=None, description="Diagnostic change statistic; never acted on.")


class BeliefUpdateEvent(_Envelope):
    """``belief_update``."""

    type: Literal["belief_update"] = "belief_update"
    decision_id: DecisionId
    fow: Literal["legal"] = "legal"
    payload: BeliefUpdatePayload


class SelectReason(StrEnum):
    """Why this strategy is in force this tick."""

    TABLE = "table"
    COMMITMENT_HOLD = "commitment_hold"
    BELOW_THRESHOLD = "below_threshold"
    FORCED = "forced"
    SAFETY_FLOOR = "safety_floor"


class StrategySelectedPayload(Frozen):
    """Table lookup + commitment result."""

    strategy: Strategy
    previous: Strategy | None
    switched: bool
    reason: SelectReason
    top_archetype: Archetype | None
    confidence: Prob | None
    commit_from: GameDate | None
    commit_until: GameDate | None
    reason_key: ReasonKey | None


class StrategySelectedEvent(_Envelope):
    """``strategy_selected``."""

    type: Literal["strategy_selected"] = "strategy_selected"
    decision_id: DecisionId
    fow: Literal["legal"] = "legal"
    payload: StrategySelectedPayload


class IntentEmittedPayload(Frozen):
    """A batch handed to the adapter, and the write result."""

    batch: IntentBatch
    receipt: EmitReceipt


class IntentEmittedEvent(_Envelope):
    """``intent_emitted``."""

    type: Literal["intent_emitted"] = "intent_emitted"
    decision_id: DecisionId
    fow: Literal["none"] = "none"
    payload: IntentEmittedPayload


class RejectRule(StrEnum):
    """Validator rule ids (C3 section 4)."""

    SCHEMA = "schema"
    RESERVED_KIND = "reserved_kind"
    NOT_MANAGED = "not_managed"
    BAD_TARGET = "bad_target"
    NOT_IN_CONTACT = "not_in_contact"
    RATE_LIMIT = "rate_limit"
    SAFETY_FLOOR = "safety_floor"
    MODE = "mode"
    DEFERRED = "deferred"  # not a refusal: engine-side hold, re-proposed later (proposal 018-T4, C3 v1.2)


class IntentRejectedPayload(Frozen):
    """An intent the validator refused; it was never written (measures the plan/action gap)."""

    intent: Intent
    rule: RejectRule
    detail: str = Field(max_length=500)


class IntentRejectedEvent(_Envelope):
    """``intent_rejected``."""

    type: Literal["intent_rejected"] = "intent_rejected"
    decision_id: DecisionId
    fow: Literal["none"] = "none"
    payload: IntentRejectedPayload


class IntentAckPayload(Frozen):
    """Lifecycle end for one (country, seq): from the mod, or LOST/SUPERSEDED from the engine."""

    seq: Seq
    country: Sid
    status: AckStatus
    code: AckCode
    intent_ids: tuple[IntentId, ...]
    attempt: int = Field(ge=1, le=2)
    ack_lag_months: int | None = Field(ge=0)


class IntentAckEvent(_Envelope):
    """``intent_ack``."""

    type: Literal["intent_ack"] = "intent_ack"
    fow: Literal["none"] = "none"
    payload: IntentAckPayload


class OutcomePayload(Frozen):
    """Reward for one commitment window (reward window = commitment window)."""

    strategy: Strategy
    window_from: GameDate
    window_to: GameDate
    forced: bool
    metrics: dict[str, float]
    reward: float = Field(ge=-1.0, le=1.0)


class OutcomeEvent(_Envelope):
    """``outcome``."""

    type: Literal["outcome"] = "outcome"
    fow: Literal["legal"] = "legal"
    payload: OutcomePayload


_UNREADABLE = frozenset({"invalid", "too_large", "unknown_cmd"})


class ControlPayload(Frozen):
    """An operator command and its result (C9).

    ``command`` is ``None`` when the inbox file was unreadable, too large or invalid (C9 section 3);
    ``result`` still carries the error. Proposal 005-T4.
    """

    command: ControlCommand | None
    result: ControlResult

    @model_validator(mode="after")
    def _null_command_is_error(self) -> ControlPayload:
        if self.command is None and (self.result.accepted or self.result.error not in _UNREADABLE):
            raise ValueError("command may be null only for a rejected invalid/too_large/unknown_cmd result")
        return self


class ControlEvent(_Envelope):
    """``control``."""

    type: Literal["control"] = "control"
    fow: Literal["none"] = "none"
    payload: ControlPayload


class HealthKind(StrEnum):
    """Health event kinds."""

    LEVEL_CHANGE = "level_change"
    STATE_CHANGE = "state_change"
    MONTHLY = "monthly"
    BUDGET_BREACH = "budget_breach"
    PARSE_FAILED = "parse_failed"
    SAVE_SKIPPED = "save_skipped"
    WORKER_KILLED = "worker_killed"
    LOG_PARSE_ERROR = "log_parse_error"
    VERSION_SKEW = "version_skew"
    RECONNECT = "reconnect"
    INI_WRITE_FAILED = "ini_write_failed"
    ACK_ALARM = "ack_alarm"
    DAEMON_EXIT = "daemon_exit"
    INVALID_EVENT = "invalid_event"
    INI_NEUTRALISED = "ini_neutralised"  # C2 section 2.1, C6 section 1.9 (v1.2)
    IRONMAN_OBSERVE_ONLY = "ironman_observe_only"  # C6 section 1.10 (v1.2)


class HealthPayload(Frozen):
    """Governor and component health (resource proposal T0 tier)."""

    kind: HealthKind
    component: Literal["daemon", "governor", "adapter", "parser", "bus", "telemetry", "engine"]
    state: GameState
    level: HealthLevel
    trigger: Trigger | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    detail: str | None = Field(default=None, max_length=500)


class HealthEvent(_Envelope):
    """``health``."""

    type: Literal["health"] = "health"
    fow: Literal["none"] = "none"
    payload: HealthPayload


BusEvent = Annotated[
    RunStartedEvent
    | ObservationEvent
    | BeliefUpdateEvent
    | StrategySelectedEvent
    | IntentEmittedEvent
    | IntentRejectedEvent
    | IntentAckEvent
    | OutcomeEvent
    | ControlEvent
    | HealthEvent,
    Field(discriminator="type"),
]
GodBusEvent = Annotated[RunStartedEvent | ObservationGodEvent, Field(discriminator="type")]

BUS_EVENT_ADAPTER: TypeAdapter[BusEvent] = TypeAdapter(BusEvent)
GOD_BUS_EVENT_ADAPTER: TypeAdapter[GodBusEvent] = TypeAdapter(GodBusEvent)

#: Event types that must carry a decision_id.
DECISION_TYPES = frozenset({"belief_update", "strategy_selected", "intent_emitted", "intent_rejected"})
