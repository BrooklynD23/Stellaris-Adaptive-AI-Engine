"""Strategy tests."""

from __future__ import annotations

import json
from dataclasses import replace

from hypothesis import given, settings
from hypothesis import strategies as st

from sae.contracts.c5_bus import SelectReason, StrategySelectedPayload
from sae.contracts.c9_control import ForceStrategy
from sae.contracts.common import Archetype, ReasonKey, Strategy
from sae.core import outcome
from sae.core.belief import ARCHS
from sae.core.expansion import DEFAULT_EXPANSIONS
from sae.core.state import EngineState
from sae.core.strategy import StrategyState, apply_force, reanchor, select

from .fakes_cfg import eff

CFG = eff()
T = ARCHS.index(Archetype.TURTLE)
TR = ARCHS.index(Archetype.TECH_RUSH)


def post(i: int, p: float) -> tuple[float, ...]:
    rest = (1 - p) / 3
    return tuple(p if j == i else rest for j in range(4))


UNIFORM = (0.25,) * 4
D = "d-2200.01-01"


def test_threshold_boundary() -> None:
    _, p, _ = select(StrategyState(), post(T, 0.45), 0, CFG, decision_id=D)
    assert p.strategy == Strategy.OUTSCALE_SIEGE and p.reason == SelectReason.TABLE
    assert p.reason_key == ReasonKey.TURTLE_SIEGE
    _, p, _ = select(StrategyState(), post(T, 0.449), 0, CFG, decision_id=D)
    assert p.strategy == Strategy.BASELINE and p.reason == SelectReason.BELOW_THRESHOLD


def test_tie_declaration_order() -> None:
    cfg = eff(belief_threshold=0.26)
    _, p, _ = select(StrategyState(), UNIFORM, 0, cfg, decision_id=D)
    assert p.top_archetype == ARCHS[0]


def test_hold_then_expiry_reopens() -> None:
    s, p, closed = select(StrategyState(), post(T, 0.9), 0, CFG, decision_id=D)
    assert closed is None and p.commit_until == "0002.01.01"
    s, p, closed = select(s, post(TR, 0.9), 5, CFG, decision_id=D)
    assert p.reason == SelectReason.COMMITMENT_HOLD and p.strategy == Strategy.OUTSCALE_SIEGE and not p.switched
    s, p, closed = select(s, post(TR, 0.9), 24, CFG, decision_id=D)
    assert p.strategy == Strategy.EARLY_PUNISH and p.switched
    assert closed is not None and (closed.from_mi, closed.to_mi) == (0, 24)
    s, p, closed = select(s, post(TR, 0.9), 48, CFG, decision_id=D)  # same strategy, window expired
    assert not p.switched and closed is not None and s.commit_from_mi == 48


def test_forced_and_clear() -> None:
    s, _, _ = select(StrategyState(), post(T, 0.9), 0, CFG, decision_id=D)
    s = apply_force(s, ForceStrategy(strategy=Strategy.EARLY_PUNISH, months=6), 3)
    s, p, closed = select(s, post(T, 0.9), 3, CFG, decision_id=D)
    assert p.strategy == Strategy.EARLY_PUNISH and p.reason == SelectReason.FORCED and p.switched
    assert p.reason_key == ReasonKey.OPERATOR_FORCED and closed is not None and s.commit_from_mi == 3
    s = apply_force(s, ForceStrategy(strategy=None), 4)
    s, p, closed = select(s, post(T, 0.9), 4, CFG, decision_id=D)
    assert p.strategy == Strategy.OUTSCALE_SIEGE and p.reason == SelectReason.TABLE
    assert s.commit_from_mi == 4 and closed is not None and closed.forced


def test_force_same_strategy_opens_new_window() -> None:
    s, _, _ = select(StrategyState(), post(T, 0.9), 0, CFG, decision_id=D)
    s = apply_force(s, ForceStrategy(strategy=Strategy.OUTSCALE_SIEGE), 2)
    s, p, closed = select(s, post(T, 0.9), 2, CFG, decision_id=D)
    assert closed is not None and s.commit_from_mi == 2 and not p.switched


def test_safety_floor_template() -> None:
    # C3 v1.1: no fleet weights; the static floor check is "a template exists for the strategy".
    missing = {k: v for k, v in DEFAULT_EXPANSIONS.items() if k != Strategy.OUTSCALE_SIEGE}
    _, p, _ = select(StrategyState(), post(T, 0.9), 0, CFG, decision_id=D, templates=missing)
    assert p.strategy == Strategy.BASELINE and p.reason == SelectReason.SAFETY_FLOOR
    assert p.reason_key == ReasonKey.SAFETY_FLOOR
    _, p, _ = select(StrategyState(), post(T, 0.9), 0, CFG, decision_id=D)
    assert p.strategy == Strategy.OUTSCALE_SIEGE and p.reason == SelectReason.TABLE


def test_reanchor_keeps_remaining() -> None:
    s, _, _ = select(StrategyState(), post(T, 0.9), 0, CFG, decision_id=D)
    s = apply_force(s, ForceStrategy(strategy=Strategy.EARLY_PUNISH, months=10), 4)
    s, _, _ = select(s, post(T, 0.9), 4, CFG, decision_id=D)
    r = reanchor(s, 100)
    assert (r.commit_from_mi, r.commit_until_mi) == (100, 110) and r.forced == (Strategy.EARLY_PUNISH, 110)


@settings(max_examples=100, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 3), st.floats(0.25, 0.99), st.integers(1, 6)), min_size=1, max_size=40))
def test_no_switch_inside_window(seq: list[tuple[int, float, int]]) -> None:
    # C3 6.1: only an abstention window may end early, and only via a confident counter (reason table, +24).
    s, mi = StrategyState(), 0
    prev_window: tuple[Strategy, int, bool] | None = None
    for i, p, dt in seq:
        s, pl, _ = select(s, post(i, p), mi, CFG, decision_id=D)
        if prev_window is not None and mi < prev_window[1]:
            if prev_window[2] and pl.switched:
                assert pl.reason == SelectReason.TABLE and pl.strategy != Strategy.BASELINE
                assert s.commit_until_mi == mi + CFG.commitment_months
            else:
                assert pl.strategy == prev_window[0] and not pl.switched
        if pl.reason != SelectReason.COMMITMENT_HOLD:
            assert s.commit_until_mi is not None
            prev_window = (pl.strategy, s.commit_until_mi, s.abstain)
        StrategySelectedPayload.model_validate(pl.model_dump())
        mi += dt


def test_force_opens_one_window_and_table_resumes_at_expiry() -> None:
    s = apply_force(StrategyState(), ForceStrategy(strategy=Strategy.EARLY_PUNISH, months=3), 0)
    opened = 0
    for mi in range(3):
        s, p, closed = select(s, post(T, 0.9), mi, CFG, decision_id=f"d{mi}")
        assert p.strategy == Strategy.EARLY_PUNISH and p.reason == SelectReason.FORCED
        assert p.reason_key == ReasonKey.OPERATOR_FORCED
        opened += s.opened_by == f"d{mi}"
        assert closed is None
    assert opened == 1 and s.commit_until_mi == 3
    s, p, closed = select(s, post(T, 0.9), 3, CFG, decision_id="d3")
    assert p.reason == SelectReason.TABLE and p.strategy == Strategy.OUTSCALE_SIEGE
    assert closed is not None and closed.forced and closed.to_mi == 3
    assert s.commit_until_mi == 3 + CFG.commitment_months


def test_commitment_hold_reports_opening_reason_key() -> None:
    s = apply_force(StrategyState(), ForceStrategy(strategy=Strategy.EARLY_PUNISH, months=3), 0)
    s = select(s, post(T, 0.9), 0, CFG, decision_id=D)[0]
    s = replace(s, forced=None)  # force cleared mid-window; window still held
    _, p, _ = select(s, post(T, 0.9), 1, CFG, decision_id=D)
    assert p.reason == SelectReason.COMMITMENT_HOLD and p.reason_key == ReasonKey.OPERATOR_FORCED


# --- C3 v1.3 section 6.1: baseline-abstention pre-emption (W1-1) ---

B = ARCHS.index(Archetype.BALANCED)
LOW = post(T, 0.30)  # below threshold


def _abstaining(mi: int = 0) -> StrategyState:
    s, p, _ = select(StrategyState(), LOW, mi, CFG, decision_id="d-open")
    assert p.reason == SelectReason.BELOW_THRESHOLD and s.abstain and s.commit_until_mi == mi + 24
    return s


def test_a_abstention_preempted_by_confident_counter() -> None:
    s, p, closed = select(_abstaining(), post(T, 0.6), 5, CFG, decision_id="d5")
    assert p.strategy == Strategy.OUTSCALE_SIEGE and p.reason == SelectReason.TABLE and p.switched
    assert p.reason_key == ReasonKey.TURTLE_SIEGE
    assert (s.commit_from_mi, s.commit_until_mi) == (5, 29) and not s.abstain and s.opened_by == "d5"
    assert closed is not None and (closed.from_mi, closed.to_mi) == (0, 5)


def test_b_table_baseline_not_preempted() -> None:
    s, p, _ = select(StrategyState(), post(B, 0.9), 0, CFG, decision_id=D)
    assert p.strategy == Strategy.BASELINE and p.reason == SelectReason.TABLE and not s.abstain
    s, p, closed = select(s, post(T, 0.9), 3, CFG, decision_id=D)
    assert p.strategy == Strategy.BASELINE and p.reason == SelectReason.COMMITMENT_HOLD and closed is None
    assert s.commit_until_mi == 24


def test_c_counter_window_never_preempted() -> None:
    s, _, _ = select(StrategyState(), post(T, 0.9), 0, CFG, decision_id=D)
    for mi, p_ in ((1, LOW), (2, post(TR, 0.99)), (3, post(B, 0.99))):
        s, p, closed = select(s, p_, mi, CFG, decision_id=D)
        assert p.strategy == Strategy.OUTSCALE_SIEGE and p.reason == SelectReason.COMMITMENT_HOLD
        assert closed is None and s.commit_until_mi == 24 and not s.abstain


def test_d_floor_failure_leaves_abstention_untouched() -> None:
    missing = {k: v for k, v in DEFAULT_EXPANSIONS.items() if k != Strategy.OUTSCALE_SIEGE}
    s0 = _abstaining()
    s, p, closed = select(s0, post(T, 0.9), 4, CFG, decision_id="d4", templates=missing)
    assert p.strategy == Strategy.BASELINE and p.reason == SelectReason.COMMITMENT_HOLD and not p.switched
    assert closed is None and s.abstain and s.commit_until_mi == s0.commit_until_mi and s.opened_by == "d-open"


def test_e_confident_balanced_opens_nothing_then_turtle_preempts() -> None:
    s0 = _abstaining()
    s, p, closed = select(s0, post(B, 0.9), 3, CFG, decision_id="d3")
    assert p.strategy == Strategy.BASELINE and p.reason == SelectReason.COMMITMENT_HOLD and closed is None
    assert s.commit_until_mi == s0.commit_until_mi and s.abstain
    s, p, closed = select(s, post(T, 0.6), 7, CFG, decision_id="d7")
    assert p.strategy == Strategy.OUTSCALE_SIEGE and p.reason == SelectReason.TABLE and s.commit_until_mi == 31
    assert closed is not None


def test_f_preemption_outcome_is_short_baseline() -> None:
    _, _, closed = select(_abstaining(), post(TR, 0.7), 9, CFG, decision_id="d9")
    assert closed is not None and closed.strategy == Strategy.BASELINE and not closed.forced
    assert closed.opened_by == "d-open" and 1 <= closed.to_mi - closed.from_mi < 24
    out = outcome.build(closed, None, outcome.snapshot((), set()))
    assert out.strategy == Strategy.BASELINE and (out.window_from, out.window_to) == ("0000.01.01", "0000.10.01")


def test_g_safety_floor_window_not_preempted() -> None:
    missing = {k: v for k, v in DEFAULT_EXPANSIONS.items() if k != Strategy.OUTSCALE_SIEGE}
    s, p, _ = select(StrategyState(), post(T, 0.9), 0, CFG, decision_id=D, templates=missing)
    assert p.reason == SelectReason.SAFETY_FLOOR and not s.abstain
    s, p, closed = select(s, post(TR, 0.9), 2, CFG, decision_id=D)
    assert p.strategy == Strategy.BASELINE and p.reason == SelectReason.COMMITMENT_HOLD and closed is None


def test_h_one_confident_tick_locks_24_months() -> None:
    s, p, _ = select(StrategyState(), post(T, 0.60), 0, CFG, decision_id="d0")
    assert p.strategy == Strategy.OUTSCALE_SIEGE and s.commit_until_mi == 24
    for mi in range(1, 24):
        s, p, _ = select(s, UNIFORM, mi, CFG, decision_id=f"d{mi}")
        assert p.strategy == Strategy.OUTSCALE_SIEGE and p.reason == SelectReason.COMMITMENT_HOLD
        assert s.commit_until_mi == 24
    s, p, closed = select(s, UNIFORM, 24, CFG, decision_id="d24")
    assert p.strategy == Strategy.BASELINE and p.reason == SelectReason.BELOW_THRESHOLD and s.abstain
    assert closed is not None and (closed.from_mi, closed.to_mi) == (0, 24)


def test_i_pre_w1_state_json_loads_without_abstain() -> None:
    st_ = EngineState(strategy=_abstaining())
    raw = json.loads(st_.model_dump_json())
    del raw["strategy"]["abstain"]
    restored = EngineState.model_validate_json(json.dumps(raw))
    assert restored.strategy.abstain is False and restored.strategy.commit_until_mi == 24
    assert EngineState.model_validate_json(st_.model_dump_json()).strategy.abstain is True


def test_reanchor_keeps_abstain() -> None:
    assert reanchor(_abstaining(), 50).abstain
