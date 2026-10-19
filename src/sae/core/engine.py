"""Pure engine step: ``f(state, input, ctx) -> (state, outputs)``. No I/O, clock or randomness.

Public API: ``initial_state``, ``on_observation``, ``on_ack``, ``on_emitted``, ``on_control``,
``next_boundary``; value types ``EventDraft``, ``Outputs``, ``EngineCtx`` (from ``sae.core.state``).
"""

from __future__ import annotations

import logging

from sae.contracts.c3_intent import IntentBatch
from sae.contracts.c5_bus import (
    ControlPayload,
    HealthKind,
    HealthPayload,
    IntentEmittedPayload,
    IntentRejectedPayload,
    RejectRule,
)
from sae.contracts.c6_adapter import EmitReceipt, GameEvent, IntentAck, Observation
from sae.contracts.c7_governor import GameState, HealthLevel
from sae.contracts.c8_config import EngineConfig
from sae.contracts.c9_control import (
    ControlCommand,
    ControlResult,
    EngineStatus,
    ForceStrategy,
    Pause,
    Resume,
)
from sae.contracts.common import GameDate, Strategy
from sae.core import features, lifecycle, strategy
from sae.core.belief import ARCHS
from sae.core.dates import date_of, mi_of
from sae.core.engine_tick import invalid_health, make_decision_id, run_tick
from sae.core.state import EngineCtx, EngineState, EventDraft, Outputs

__all__ = ["EngineCtx", "EventDraft", "Outputs", "initial_state", "next_boundary", "on_ack", "on_control",
           "on_emitted", "on_observation"]
log = logging.getLogger(__name__)


def initial_state(cfg: EngineConfig) -> EngineState:
    """Fresh engine state. ``cfg`` is accepted for symmetry (state holds no config-derived values)."""
    del cfg
    return EngineState()


def _reset(s: EngineState, mi: int, regressed: bool) -> EngineState:
    """Session reset (v2 rule 2)."""
    lc = lifecycle.reanchor(s.lifecycle, mi).model_copy(update={"resync_needed": True})
    log.info("session reset at mi=%d regressed=%s", mi, regressed)
    return s.model_copy(update={
        "features": features.reset_session(s.features, mi, regressed=regressed),
        "strategy": strategy.reanchor(s.strategy, mi),
        "lifecycle": lc,
        "last_decided_mi": mi - 1,
        "decision_gen": s.decision_gen + 1,
    })


def on_observation(state: EngineState, obs: Observation, ctx: EngineCtx) -> tuple[EngineState, Outputs]:
    """Consume one observation; run a decision tick if it opens a new game month."""
    mi = mi_of(obs.game_date)
    s = state
    regressed = GameEvent.DATE_REGRESSED in obs.events or (s.last_obs_mi is not None and mi < s.last_obs_mi)
    if regressed or (s.session_epoch is not None and obs.session_epoch != s.session_epoch):
        s = _reset(s, mi, regressed)
    s = s.model_copy(update={
        "session_epoch": obs.session_epoch, "last_obs_date": obs.game_date, "last_obs_m": obs.m,
        "lifecycle": lifecycle.observe_managed(s.lifecycle, obs.managed),
        "gov_state": ctx.game_state, "gov_level": ctx.level,
    })
    if s.queued_force is not None:
        s = s.model_copy(update={"strategy": strategy.apply_force(s.strategy, s.queued_force, mi),
                                 "queued_force": None})
    tick = s.last_decided_mi is None or mi > s.last_decided_mi
    did: str | None = None
    if tick:
        # NN = decision_gen % 100: unique per (month, generation) except that it repeats every 100 resets.
        n = s.decision_gen
        did = make_decision_id(mi, n)
        s = s.model_copy(update={"decision_mi": mi, "decision_n": n})
    drafts = [EventDraft(type="observation", payload=obs, game_date=obs.game_date, decision_id=did)]
    lc0, bad = lifecycle.take_invalid(s.lifecycle)
    if bad:
        s = s.model_copy(update={"lifecycle": lc0})
        drafts.append(invalid_health(bad, obs.game_date, ctx.game_state, ctx.level))
    batch = None
    if tick and did is not None:
        s, more, batch = run_tick(s, obs, ctx, mi, did)
        drafts += more
    return s, Outputs(drafts=tuple(drafts), batch=batch)


def on_ack(state: EngineState, ack: IntentAck) -> tuple[EngineState, Outputs]:
    """Record a mod ACK (also floors the seq allocator)."""
    lc, payloads = lifecycle.on_ack(state.lifecycle, ack, mi_of(ack.game_date))
    drafts = [EventDraft(type="intent_ack", payload=p, game_date=ack.game_date) for p in payloads]
    lc, bad = lifecycle.take_invalid(lc)
    if bad:
        drafts.append(invalid_health(bad, ack.game_date, state.gov_state, state.gov_level))
    return state.model_copy(update={"lifecycle": lc}), Outputs(drafts=tuple(drafts))


def on_emitted(state: EngineState, batch: IntentBatch, receipt: EmitReceipt) -> tuple[EngineState, Outputs]:
    """Register an emitted batch; a failed write becomes health ``ini_write_failed``."""
    mi = mi_of(batch.issued_date)
    lc, kind = lifecycle.register(state.lifecycle, batch, receipt, mi)
    drafts = [EventDraft(type="intent_emitted", game_date=batch.issued_date, decision_id=state.last_decision_id,
                         payload=IntentEmittedPayload(batch=batch, receipt=receipt))]
    if kind is HealthKind.INI_WRITE_FAILED:
        drafts.append(EventDraft(type="health", game_date=batch.issued_date, payload=HealthPayload(
            kind=kind, component="engine", state=state.gov_state, level=state.gov_level,
            detail=f"seq {batch.seq}: {receipt.error or 'write failed'}")))
    if receipt.error == "ironman":  # audit proposed-but-unemitted intents with the Ironman reason (N6)
        drafts.extend(EventDraft(type="intent_rejected", game_date=batch.issued_date,
                                 decision_id=state.last_decision_id or i.decision_id,
                                 payload=IntentRejectedPayload(intent=i, rule=RejectRule.MODE,
                                                               detail="ironman: observe only (emit refused)"))
                      for i in batch.intents)
    lc, bad = lifecycle.take_invalid(lc)
    if bad:
        drafts.append(invalid_health(bad, batch.issued_date, state.gov_state, state.gov_level))
    upd: dict[str, object] = {"lifecycle": lc}
    if receipt.ok:
        same = state.last_batch_mi == mi
        upd.update(last_batch_mi=mi, batches_this_month=state.batches_this_month + 1 if same else 1)
    return state.model_copy(update=upd), Outputs(drafts=tuple(drafts))


def next_boundary(state: EngineState) -> GameDate | None:
    """``commit_until`` for ``governor.set_next_boundary``."""
    u = state.strategy.commit_until_mi
    return None if u is None else date_of(u)


def _status(s: EngineState, run_id: str, gstate: GameState, level: HealthLevel, date: str | None) -> EngineStatus:
    forced = s.strategy.forced
    post = s.belief.posterior
    top = ARCHS[max(range(len(ARCHS)), key=lambda i: post[i])] if s.belief.last_evidence_mi is not None else None
    return EngineStatus(
        run_id=run_id, game_date=date, suspended=s.suspended, state=gstate, level=level,
        strategy=s.strategy.strategy or Strategy.BASELINE,
        forced_until=date_of(forced[1]) if forced else None, commit_until=next_boundary(s), top_archetype=top,
        in_flight_batches=lifecycle.in_flight_batches(s.lifecycle))


def on_control(
    state: EngineState, cmd: ControlCommand, run_id: str, governor_state: GameState, level: HealthLevel,
    game_date: str | None,
) -> tuple[EngineState, ControlResult, Outputs]:
    """Apply a C9 command between engine calls (v2 rule 10). ``status`` is read-only."""
    s, c = state, cmd.command
    date = game_date or state.last_obs_date
    if isinstance(c, Pause):
        s = s.model_copy(update={"suspended": True})
    elif isinstance(c, Resume):
        s = s.model_copy(update={"suspended": False})
    elif isinstance(c, ForceStrategy):
        mi = s.last_obs_mi if date is None else mi_of(date)
        if mi is None:
            s = s.model_copy(update={"queued_force": c})  # applied on the first observation
        else:
            s = s.model_copy(update={"strategy": strategy.apply_force(s.strategy, c, mi)})
    status = _status(s, run_id, governor_state, level, date)
    result = ControlResult(command_id=cmd.command_id, accepted=True, applied_at=date, status=status)
    draft = EventDraft(type="control", payload=ControlPayload(command=cmd, result=result), game_date=date)
    return s, result, Outputs(drafts=(draft,))
