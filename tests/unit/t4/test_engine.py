"""Pure engine: synth archetypes, CI-1 sparsity, suspend/release/defer/regression, chain, round-trip."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import pytest

from sae.contracts.c2_ini import KnobCommand
from sae.contracts.c3_intent import IntentBatch
from sae.contracts.c5_bus import BUS_EVENT_ADAPTER, HealthKind, SelectReason
from sae.contracts.c6_adapter import AdapterMode, EmitReceipt, GameEvent
from sae.contracts.c7_governor import GameState, HealthLevel
from sae.contracts.c8_config import EngineConfig
from sae.contracts.c9_control import ControlCommand, ForceStrategy, Pause, Resume, Status
from sae.contracts.common import COUNTER_TABLE, Archetype, Strategy
from sae.core import belief
from sae.core.engine import EventDraft, Outputs, initial_state, next_boundary, on_control, on_emitted
from sae.core.state import EngineState

from .fake_synth import Sim, date_for

CFG = EngineConfig()
MONTHS = 372  # through 2230.12
SEEDS = (1, 2, 3, 4)


def _drafts(outs: list[Outputs], kind: str) -> list[EventDraft]:
    return [d for o in outs for d in o.drafts if d.type == kind]


def _strategies(outs: list[Outputs]) -> list[tuple[str, Strategy]]:
    return [(d.game_date or "", d.payload.strategy) for d in _drafts(outs, "strategy_selected")  # type: ignore[attr-defined]
            ]


def _cmd(command: Any) -> ControlCommand:
    return ControlCommand(command_id="c-20260101T000000Z-abcdef", issued_at="2026-01-01T00:00:00Z",
                          operator="t", command=command)


def _hits(arch: Archetype) -> tuple[int, int, list[list[float]]]:
    """(counter-strategy hits, argmax(posterior)==arch hits, posteriors) over SEEDS at year 30."""
    ars = list(Archetype)
    counter = top = 0
    posts: list[list[float]] = []
    for seed in SEEDS:
        state, _ = Sim(arch, seed).run(MONTHS)
        p = state.belief.posterior
        counter += state.strategy.strategy == COUNTER_TABLE[arch]
        top += ars[max(range(len(p)), key=lambda i: p[i])] == arch
        posts.append([round(x, 2) for x in p])
    return counter, top, posts


@pytest.mark.parametrize("arch", [a for a in Archetype if a is not Archetype.BALANCED])
def test_archetype_reaches_counter_by_year_30(arch: Archetype) -> None:
    counter, top, _ = _hits(arch)
    assert counter >= 3 and top >= 3


# Balanced must be the argmax (erratic synth, SPREAD=3): BASELINE because the belief is unsure is NOT a pass.
def test_balanced_argmax_and_counter() -> None:
    counter, top, _ = _hits(Archetype.BALANCED)
    assert top >= 3 and counter >= 3


def test_balanced_no_war_argmax(monkeypatch: pytest.MonkeyPatch) -> None:
    from . import fake_synth
    monkeypatch.setitem(fake_synth.PARAMS, Archetype.BALANCED, (*fake_synth.PARAMS[Archetype.BALANCED][:4], None))
    _, top, _ = _hits(Archetype.BALANCED)
    assert top >= 3


def test_sparse_saves_same_strategy_at_boundaries() -> None:
    """CI-1: dropping every 2nd save keeps a stable archetype's late strategy."""
    full = _strategies(Sim(Archetype.TURTLE, 1).run(MONTHS)[1])
    sparse = _strategies(Sim(Archetype.TURTLE, 1, keep_every=2).run(MONTHS)[1])
    late = lambda xs: {s for d, s in xs if d >= "2220.01.01"}  # noqa: E731
    assert late(full) == late(sparse) == {Strategy.OUTSCALE_SIEGE}


def test_change_score_is_diagnostic_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _, real = Sim(Archetype.EARLY_AGGRESSION, 2).run(200)
    orig = belief.update

    def stub(state: belief.BeliefState, *a: Any, **k: Any) -> Any:
        s2, p = orig(state, *a, **k)
        return replace(s2, change_score=7.0), p.model_copy(update={"change_score": 7.0})

    monkeypatch.setattr(belief, "update", stub)
    _, stubbed = Sim(Archetype.EARLY_AGGRESSION, 2).run(200)
    assert [d.payload for d in _drafts(real, "strategy_selected")] == [
        d.payload for d in _drafts(stubbed, "strategy_selected")]
    assert [o.batch for o in real] == [o.batch for o in stubbed]


def test_suspended_emits_no_strategy_or_batch() -> None:
    sim = Sim(Archetype.TURTLE, 1)
    st, _ = sim.run(10)
    st, res, _ = on_control(st, _cmd(Pause()), "r", GameState.PLAYING, HealthLevel.S, date_for(9))
    assert st.suspended and res.accepted
    st, outs = sim.run(30, st)
    assert _drafts(outs, "belief_update") and not _drafts(outs, "strategy_selected")
    assert all(o.batch is None for o in outs)
    st, _, _ = on_control(st, _cmd(Resume()), "r", GameState.PLAYING, HealthLevel.H0, None)
    st, outs = sim.run(2, st)
    assert _drafts(outs, "strategy_selected")


def test_release_mode_rejects_with_mode_only_on_change() -> None:
    _, outs = Sim(Archetype.TURTLE, 1, mode=AdapterMode.RELEASE).run(150)
    rej = _drafts(outs, "intent_rejected")
    assert rej and {d.payload.rule.value for d in rej} == {"mode"}  # type: ignore[attr-defined]
    assert all(o.batch is None for o in outs)
    months = {d.game_date for d in rej}
    changes = 1 + sum(1 for d in _drafts(outs, "strategy_selected")
                      if d.payload.switched)  # type: ignore[attr-defined]
    assert len(months) <= changes + 1  # +1: contact appears one tick after the first strategy


def test_m_none_defers_batch_then_emits() -> None:
    _, outs = Sim(Archetype.BALANCED, 1, m_none=frozenset({7})).run(12)
    first = next(o for o in outs if o.batch is not None)
    assert first.batch is not None and first.batch.issued_m == 8
    obs7 = [d for o in outs for d in o.drafts if d.type == "observation" and d.game_date == date_for(7)]
    assert obs7 and all(o.batch is None for o in outs if any(d in obs7 for d in o.drafts))


def test_date_regression_resumes_ticks() -> None:
    sim = Sim(Archetype.TURTLE, 1)
    st, pre = sim.run(30)
    sim.t, sim.epoch = 10, 1
    obs = sim.obs("log").model_copy(update={"events": (GameEvent.DATE_REGRESSED,)})
    st, outs = sim.feed(st, obs, sim.ctx())
    assert _drafts(outs, "belief_update")
    sim.t += 1
    st, outs2 = sim.run(5, st)
    assert len(_drafts(outs2, "belief_update")) == 5
    assert st.last_decided_mi is not None and st.last_obs_mi == st.last_decided_mi
    # decision ids are unique across the regression (each tick has its own id; chain ids share one)
    ids = [d.decision_id for o in [*outs, *outs2] for d in o.drafts if d.type == "belief_update"]
    ids += [d.decision_id for o in pre for d in o.drafts if d.type == "belief_update"]
    assert len(set(ids)) == len(ids)


def test_chain_shares_decision_id() -> None:
    _, outs = Sim(Archetype.TURTLE, 1).run(30)
    tick = next(i for i, o in enumerate(outs) if o.batch is not None)
    ids = {d.decision_id for d in outs[tick].drafts if d.type in ("observation", "belief_update",
                                                                    "strategy_selected")}
    emitted = outs[tick + 1].drafts[0]
    assert emitted.type == "intent_emitted" and len(ids) == 1 and emitted.decision_id in ids
    did = ids.pop()
    assert did is not None and did.startswith("d-2200.0") and did.endswith("-00")


def test_state_roundtrip_resumes_identically() -> None:
    sim = Sim(Archetype.EARLY_AGGRESSION, 3)
    st, _ = sim.run(100)
    restored = EngineState.model_validate_json(st.model_dump_json())
    assert restored == st
    sim2 = copy.deepcopy(sim)
    _, a = sim.run(60, st)
    _, b = sim2.run(60, restored)
    assert [o.model_dump_json() for o in a] == [o.model_dump_json() for o in b]


def test_every_draft_validates_as_c5_event() -> None:
    sim = Sim(Archetype.EARLY_AGGRESSION, 2)
    st, outs = sim.run(60)
    st, _, co = on_control(st, _cmd(ForceStrategy(strategy=Strategy.EARLY_PUNISH)), "r", GameState.PLAYING,
                           HealthLevel.H0, date_for(59))
    knob = KnobCommand(emit_period=2)
    st, bad = on_emitted(st, IntentBatch(seq=900, issued_date=date_for(60), issued_m=60, knobs=knob),
                         EmitReceipt(seq=900, ok=False, error="write_failed"))
    st, more = sim.run(40, st)
    drafts = [d for o in (*outs, co, bad, *more) for d in o.drafts]
    kinds = {d.type for d in drafts}
    assert {"observation", "belief_update", "strategy_selected", "intent_emitted", "intent_ack", "control",
            "health"} <= kinds
    for d in drafts:
        BUS_EVENT_ADAPTER.validate_python({
            "v": 1, "run_id": "20260101-000000-synth-abcd", "seq": 0, "t_wall": "2026-01-01T00:00:00Z",
            "t_mono_ns": 0, "game_date": d.game_date, "decision_id": d.decision_id, "type": d.type,
            "payload": d.payload.model_dump(mode="json")})
        if d.type in ("belief_update", "strategy_selected", "intent_emitted", "intent_rejected"):
            assert d.decision_id is not None


def test_force_and_clear_apply_immediately() -> None:
    sim = Sim(Archetype.TURTLE, 1)
    st, _ = sim.run(40)
    before = st
    st, res, _ = on_control(st, _cmd(Status()), "r", GameState.PLAYING, HealthLevel.H0, None)
    assert st == before and res.status is not None and not res.status.suspended
    st, res, _ = on_control(st, _cmd(ForceStrategy(strategy=Strategy.HOLD_COUNTERPUNCH, months=12)), "r",
                            GameState.PLAYING, HealthLevel.H0, None)
    assert res.status is not None and res.status.forced_until is not None
    st, outs = sim.run(1, st)
    sel = _drafts(outs, "strategy_selected")[0].payload
    assert sel.reason == SelectReason.FORCED and sel.strategy == Strategy.HOLD_COUNTERPUNCH  # type: ignore[attr-defined]
    assert next_boundary(st) is not None
    st, _, _ = on_control(st, _cmd(ForceStrategy(strategy=None)), "r", GameState.PLAYING, HealthLevel.H0, None)
    assert st.strategy.forced is None


def test_force_before_first_observation_is_queued() -> None:
    st = initial_state(CFG)
    st, _, _ = on_control(st, _cmd(ForceStrategy(strategy=Strategy.EARLY_PUNISH)), "r", GameState.PLAYING,
                          HealthLevel.H0, None)
    assert st.queued_force is not None
    st, outs = Sim(Archetype.TURTLE, 1).run(1, st)
    assert st.queued_force is None
    assert _drafts(outs, "strategy_selected")[0].payload.reason == SelectReason.FORCED  # type: ignore[attr-defined]


def test_failed_emit_sets_resync_and_health() -> None:
    st = initial_state(CFG)
    b = IntentBatch(seq=5, issued_date=date_for(3), issued_m=3, knobs=KnobCommand(emit_period=2))
    st, out = on_emitted(st, b, EmitReceipt(seq=5, ok=False, error="write_failed"))
    assert st.lifecycle.resync_needed
    health = [d for d in out.drafts if d.type == "health"]
    assert health and health[0].payload.kind == HealthKind.INI_WRITE_FAILED  # type: ignore[attr-defined]


def _overdue_state(sim: Sim) -> EngineState:
    """Settled turtle state at t=40 with one attempt-1 batch long overdue (a LOST on the next tick)."""
    from sae.contracts.c3_intent import NoopHeartbeat
    from sae.core import lifecycle
    from sae.core.dates import mi_of
    st, _ = sim.run(40)
    lc, seq = lifecycle.allocate(st.lifecycle)
    b = IntentBatch(seq=seq, issued_date=date_for(30), issued_m=30, intents=(
        NoopHeartbeat(intent_id="i-009999", decision_id="d-2200.07-00", country=2),))
    lc, _ = lifecycle.register(lc, b, EmitReceipt(seq=seq, ok=True), mi_of(date_for(30)))
    return st.model_copy(update={"lifecycle": lc})


def test_lost_reemit_deferred_when_m_none() -> None:
    from sae.core.engine import on_observation
    sim = Sim(Archetype.TURTLE, 1)
    st = _overdue_state(sim)
    st, out = on_observation(st, sim.obs("log").model_copy(update={"m": None}), sim.ctx())
    assert out.batch is None and st.lifecycle.resync_needed
    sim.t += 1
    st, out = on_observation(st, sim.obs("log"), sim.ctx())
    assert out.batch is not None and out.batch.intents


def test_lost_reemit_survives_knob_only_tick() -> None:
    from sae.core.engine import on_observation
    sim = Sim(Archetype.TURTLE, 1)
    st = _overdue_state(sim)
    ctx = sim.ctx().model_copy(update={"knob_request": KnobCommand(emit_period=2)})
    st, out = on_observation(st, sim.obs("log"), ctx)
    assert out.batch is not None and out.batch.knobs is not None and not out.batch.intents
    assert st.lifecycle.resync_needed  # the lost intents go out on a later tick
    sim.t += 1
    _, out = on_observation(st, sim.obs("log"), sim.ctx())
    assert out.batch is not None and out.batch.intents


def test_outcome_draft_carries_opening_decision_id() -> None:
    _, outs = Sim(Archetype.EARLY_AGGRESSION, 2).run(200)
    seen = 0
    for o in outs:
        tick = {d.decision_id for d in o.drafts if d.type == "belief_update"}
        for d in o.drafts:
            if d.type == "outcome":
                seen += 1
                assert d.decision_id is not None and d.decision_id not in tick  # the opener, not this tick
    assert seen
