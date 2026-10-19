"""The month decision tick of the pure engine (split from engine.py; see T4 plan U4 and v2 rules 3, 9, 11)."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from sae.contracts.c3_intent import Intent, IntentBatch
from sae.contracts.c5_bus import HealthKind, HealthPayload, IntentRejectedPayload, RejectRule
from sae.contracts.c6_adapter import AdapterMode, Observation
from sae.contracts.c7_governor import GameState, HealthLevel
from sae.contracts.common import CLAUSEWITZ_INT_MAX, ReasonKey
from sae.core import belief, features, lifecycle, outcome, strategy
from sae.core.dates import date_of
from sae.core.expansion import desired_intents
from sae.core.state import DesiredSig, EngineCtx, EngineState, EventDraft
from sae.core.validator import ValidationContext, validate

log = logging.getLogger(__name__)
_ID_FIELDS = {"intent_id", "decision_id", "reason_key", "source"}


def make_decision_id(mi: int, n: int) -> str:
    """``d-YYYY.MM-NN`` with NN = ``n % 100``.

    Known limitation of the two-digit format: the id repeats every 100 session resets (decision_gen) within
    the same game month; consumers must not assume global uniqueness beyond that.
    """
    return f"d-{date_of(mi)[:7]}-{n % 100:02d}"


def invalid_health(count: int, date: str, state: GameState, level: HealthLevel) -> EventDraft:
    """Health draft for out-of-range seq values that lifecycle ignored (count only, no raw values)."""
    return EventDraft(type="health", game_date=date, payload=HealthPayload(
        kind=HealthKind.INVALID_EVENT, component="engine", state=state, level=level,
        detail=f"ignored {count} out-of-range seq value(s) (> {CLAUSEWITZ_INT_MAX})"))


def signature(intents: Sequence[Intent]) -> DesiredSig:
    """Per-country desired state, ids and flavour stripped, for change detection."""
    by_c: dict[int, list[str]] = {}
    for i in intents:
        by_c.setdefault(i.country, []).append(
            json.dumps(i.model_dump(mode="json", exclude=_ID_FIELDS), sort_keys=True))
    return tuple((c, tuple(sorted(v))) for c, v in sorted(by_c.items()))


#: Audit details for deferred (proposed-but-unemitted) intents, rule ``deferred`` (C3 v1.2, proposal 018).
DEFER_NO_M = "deferred: no HB month counter yet (m=None); re-sent when it arrives"
DEFER_ONE_PER_MONTH = "deferred: one batch per game month; re-sent next month"


def _deferred(intents: Sequence[Intent], detail: str, date: str, did: str) -> list[EventDraft]:
    """Design report: every proposed-but-unemitted intent is audited (here: deferred, not refused)."""
    return _rejections([(i, RejectRule.DEFERRED, detail) for i in intents], date, did)


def _rejections(rejected: Sequence[tuple[Intent | Mapping[str, Any], Any, str]], date: str,
                did: str) -> list[EventDraft]:
    out = []
    for intent, rule, detail in rejected:
        if isinstance(intent, Mapping):
            raise RuntimeError(f"engine produced a raw-dict intent rejected by {rule}: {detail}")
        out.append(EventDraft(type="intent_rejected", game_date=date, decision_id=did,
                              payload=IntentRejectedPayload(intent=intent, rule=rule, detail=detail[:500])))
    return out


def run_tick(
    s: EngineState, obs: Observation, ctx: EngineCtx, mi: int, did: str
) -> tuple[EngineState, list[EventDraft], IntentBatch | None]:
    """One decision tick. Returns the new state, drafts (excluding the observation draft) and a batch."""
    cfg, date = ctx.cfg, obs.game_date
    drafts: list[EventDraft] = []
    lc, acks, reemits, alarm = lifecycle.on_month(s.lifecycle, mi)
    drafts += [EventDraft(type="intent_ack", payload=p, game_date=date) for p in acks]
    if alarm and not ctx.observe_only:  # Ironman: the hotkey cannot help, so never alarm (N6)
        drafts.append(EventDraft(type="health", game_date=date, payload=HealthPayload(
            kind=HealthKind.ACK_ALARM, component="engine", state=ctx.game_state, level=ctx.level,
            detail="no ACK after re-emission; press the run hotkey")))
    mem, fv, new_ev, k = features.extract(s.features, obs)
    bel, bp = belief.update(s.belief, fv, new_ev, k, cfg, mi=mi)
    drafts.append(EventDraft(type="belief_update", payload=bp, game_date=date, decision_id=did))
    s = s.model_copy(update={"lifecycle": lc, "features": mem, "belief": bel, "last_decided_mi": mi,
                             "last_decision_id": did})
    if s.suspended:
        return s, drafts, None
    strat, sp, closed = strategy.select(s.strategy, bel.posterior, mi, cfg, decision_id=did)
    drafts.append(EventDraft(type="strategy_selected", payload=sp, game_date=date, decision_id=did))
    contact_set = frozenset(v.observer_sid for v in obs.player_legal if v.in_contact)
    now = outcome.snapshot(obs.managed, contact_set)
    if closed is not None:
        drafts.append(EventDraft(type="outcome", payload=outcome.build(closed, s.window_open, now),
                                 game_date=date, decision_id=closed.opened_by or did))
    s = s.model_copy(update={"strategy": strat,
                             "window_open": now if strat.opened_by == did else s.window_open})
    managed = [m.sid for m in obs.managed]
    intents, next_id = desired_intents(sp.strategy, sp.reason_key or ReasonKey.BASELINE, managed,
                                       obs.player_legal, obs.player_sid, cfg, did, s.next_intent_id)
    sig, contact = signature(intents), tuple(sorted(contact_set))
    want = sig != s.last_desired or bool(contact_set - set(s.contact)) or lc.resync_needed
    due = want or ctx.knob_request is not None
    release = ctx.mode == AdapterMode.RELEASE or ctx.observe_only
    per_month = s.batches_this_month if s.last_batch_mi == mi else 0
    vctx = ValidationContext(mode=ctx.mode, managed=frozenset(managed), player_sid=obs.player_sid,
                             in_contact=contact_set, cfg=cfg, batches_this_month=per_month,
                             observe_only=ctx.observe_only)
    if (due or reemits) and obs.m is None and not release:
        # Rule 3: defer until HB gives a month counter; desired state is NOT marked decided, and a
        # pending attempt-2 re-emission stays pending via resync (it is re-derived once m is known).
        if want:
            drafts += _deferred(intents, DEFER_NO_M, date, did)
        return s.model_copy(update={"lifecycle": lc.model_copy(update={"resync_needed": True}),
                                    "next_intent_id": next_id if want else s.next_intent_id}), drafts, None
    batch: IntentBatch | None = None
    upd: dict[str, Any] = {}
    if due and per_month >= 1 and not release:
        # One batch per game month (C3): a same-month reload must not consume the desired state. Keep the
        # resync flag (and never touch last_desired) so the batch goes out next month; a pending knob
        # request stays pending with the caller.
        keep = lc.resync_needed or bool(reemits)
        if want:
            drafts += _deferred(intents, DEFER_ONE_PER_MONTH, date, did)
        return s.model_copy(update={"lifecycle": lc.model_copy(update={"resync_needed": keep}),
                                    "next_intent_id": next_id if want else s.next_intent_id}), drafts, None
    if due:
        accepted, rejected = validate(intents if want else (), vctx)
        if want:
            drafts += _rejections(rejected, date, did)
            upd["next_intent_id"] = next_id
        if not release and obs.m is not None and (accepted or ctx.knob_request is not None):
            if lifecycle.can_allocate(lc):
                lc, seq = lifecycle.allocate(lc)
                batch = IntentBatch(seq=seq, issued_date=date, issued_m=obs.m, intents=accepted,
                                    knobs=ctx.knob_request)
            else:
                drafts.append(EventDraft(type="health", game_date=date, payload=HealthPayload(
                    kind=HealthKind.INVALID_EVENT, component="engine", state=ctx.game_state, level=ctx.level,
                    detail=f"seq space exhausted (next_seq > {CLAUSEWITZ_INT_MAX}); batch skipped")))
        # Release / Ironman: nothing can be emitted while it lasts; leaving Ironman means a new session, whose
        # reset sets resync again, so clearing it here loses nothing.
        # A knob-only tick (no desired change) must not swallow attempt-2 re-emissions: keep resync pending.
        upd.update(last_desired=sig, contact=contact,
                   lifecycle=lc.model_copy(update={"resync_needed": bool(reemits) and not want and not release}))
    elif reemits and obs.m is not None and not release:
        # Rule 9: attempt-2 re-emission only when nothing else is due; same intents, new seq.
        old = tuple(i for r in reemits for i in r.intents)
        accepted, rejected = validate(old, replace(vctx, is_reemission=True))
        drafts += _rejections(rejected, date, did)
        if accepted and lifecycle.can_allocate(lc):
            lc, seq = lifecycle.allocate(lc)
            batch = IntentBatch(seq=seq, attempt=2, issued_date=date, issued_m=obs.m, intents=accepted)
            upd["lifecycle"] = lc
        elif accepted:
            drafts.append(EventDraft(type="health", game_date=date, payload=HealthPayload(
                kind=HealthKind.INVALID_EVENT, component="engine", state=ctx.game_state, level=ctx.level,
                detail=f"seq space exhausted (next_seq > {CLAUSEWITZ_INT_MAX}); batch skipped")))
    return s.model_copy(update=upd), drafts, batch
