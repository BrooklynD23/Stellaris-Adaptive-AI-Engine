"""Engine state snapshot (the ``state.json`` payload) and the engine's I/O value types."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import ConfigDict, Field

from sae.contracts.c2_ini import KnobCommand
from sae.contracts.c3_intent import IntentBatch
from sae.contracts.c6_adapter import AdapterMode
from sae.contracts.c7_governor import GameState, HealthLevel
from sae.contracts.c8_config import EngineConfig
from sae.contracts.c9_control import ForceStrategy
from sae.contracts.common import Frozen, Strategy
from sae.core.belief import BeliefState, LikelihoodModel
from sae.core.expansion import Template
from sae.core.features import FeatureMemory
from sae.core.lifecycle import LifecycleState
from sae.core.outcome import WindowSnapshot
from sae.core.strategy import StrategyState

#: Per-country desired state with ids stripped: ``((country, (canonical intent json, ...)), ...)``.
DesiredSig = tuple[tuple[int, tuple[str, ...]], ...]


class EngineState(Frozen):
    """Everything the pure engine carries between calls. Round-trips via JSON."""

    state_version: Literal[1] = 1
    features: FeatureMemory = FeatureMemory()
    belief: BeliefState = BeliefState()
    strategy: StrategyState = StrategyState()
    lifecycle: LifecycleState = LifecycleState()
    next_intent_id: int = Field(default=1, ge=1)
    decision_mi: int | None = None
    decision_n: int = Field(default=0, ge=0)
    decision_gen: int = Field(default=0, ge=0)  # session-reset counter; the NN of decision ids
    last_decision_id: str | None = None
    last_decided_mi: int | None = None
    last_batch_mi: int | None = None
    batches_this_month: int = Field(default=0, ge=0)
    suspended: bool = False
    queued_force: ForceStrategy | None = None
    last_desired: DesiredSig = ()
    contact: tuple[int, ...] = ()
    session_epoch: int | None = None
    last_obs_date: str | None = None
    last_obs_m: int | None = None
    window_open: WindowSnapshot | None = None
    gov_state: GameState = GameState.PLAYING
    gov_level: HealthLevel = HealthLevel.H0

    @property
    def last_obs_mi(self) -> int | None:
        """Month index of the last observation, or None before the first."""
        if self.last_obs_date is None:
            return None
        y, m, _ = self.last_obs_date.split(".")
        return int(y) * 12 + int(m) - 1


class EventDraft(Frozen):
    """A bus event minus its envelope; the writer stamps run_id/seq/times."""

    type: str
    payload: Frozen
    game_date: str | None = None
    decision_id: str | None = None


class Outputs(Frozen):
    """What one engine call produced."""

    drafts: tuple[EventDraft, ...] = ()
    batch: IntentBatch | None = None


class EngineCtx(Frozen):
    """Per-observation context from the daemon."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    mode: AdapterMode
    cfg: EngineConfig
    knob_request: KnobCommand | None = None
    emit_period: int = 1
    game_state: GameState = GameState.PLAYING
    level: HealthLevel = HealthLevel.H0
    #: Wave 3 N6 (maintainer): Ironman reported by the adapter -> observe only (beliefs update, nothing emitted,
    #: no ack_alarm; proposed intents are audited as intent_rejected rule ``mode``, detail ``ironman``).
    observe_only: bool = False
    #: C10 section 4: tables from the resolved policy; None = code defaults (built-in 0.1.0).
    likelihood: LikelihoodModel | None = None
    templates: Mapping[Strategy, Template] | None = None
    outcome_weights: Mapping[str, float] | None = None
