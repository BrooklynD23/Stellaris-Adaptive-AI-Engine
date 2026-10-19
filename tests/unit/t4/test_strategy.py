"""Strategy tests."""

from __future__ import annotations

from dataclasses import replace

from hypothesis import given, settings
from hypothesis import strategies as st

from sae.contracts.c5_bus import SelectReason, StrategySelectedPayload
from sae.contracts.c8_config import EngineConfig
from sae.contracts.c9_control import ForceStrategy
from sae.contracts.common import Archetype, ReasonKey, Strategy
from sae.core.belief import ARCHS
from sae.core.expansion import DEFAULT_EXPANSIONS
from sae.core.strategy import StrategyState, apply_force, reanchor, select

CFG = EngineConfig()
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
    cfg = EngineConfig(belief_threshold=0.26)
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
    s, mi, prev_window = StrategyState(), 0, None
    for i, p, dt in seq:
        s, pl, _ = select(s, post(i, p), mi, CFG, decision_id=D)
        if prev_window is not None and mi < prev_window[1]:
            assert pl.strategy == prev_window[0] and not pl.switched
        if pl.reason != SelectReason.COMMITMENT_HOLD:
            assert s.commit_until_mi is not None
            prev_window = (pl.strategy, s.commit_until_mi)
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
