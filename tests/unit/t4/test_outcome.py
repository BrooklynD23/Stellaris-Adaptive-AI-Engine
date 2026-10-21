"""Outcome metrics and reward."""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from sae.contracts.c6_adapter import ManagedState
from sae.contracts.common import Strategy
from sae.core.outcome import AiMetrics, WindowSnapshot, build, snapshot
from sae.core.strategy import WindowClosed

CLOSED = WindowClosed(Strategy.EARLY_PUNISH, 2208 * 12 + 4, 2210 * 12 + 4, True, "d-2208.05-00")


def _ms(sid: int, fp: float, cap: float, systems: int, inc: float) -> ManagedState:
    return ManagedState(sid=sid, name="x", applied_seq=0, fleet_power=fp, naval_cap=cap, systems_owned=systems,
                        alloys_income=inc)


def test_snapshot_only_in_contact_sorted() -> None:
    snap = snapshot([_ms(3, 1, 2, 1, 1), _ms(2, 1, 2, 1, 1)], frozenset({2}))
    assert [a.sid for a in snap.ais] == [2]
    assert snap.ais[0].fleet_share == 0.5


def test_reward_positive_on_growth_and_flags() -> None:
    a = snapshot([_ms(2, 10, 100, 5, 10)], frozenset({2}))
    b = snapshot([_ms(2, 50, 100, 8, 30)], frozenset({2}))
    p = build(CLOSED, a, b)
    assert p.forced is True and p.window_from == "2208.05.01" and p.window_to == "2210.05.01"
    assert math.isclose(p.metrics["d_systems_owned"], 3.0)
    assert 0 < p.reward <= 1.0


def test_no_open_snapshot_zero_reward() -> None:
    p = build(CLOSED, None, WindowSnapshot())
    assert p.reward == 0.0 and p.metrics == {}


def test_unobserved_metric_omitted() -> None:
    a = WindowSnapshot(ais=(AiMetrics(sid=2),))
    p = build(CLOSED, a, a)
    assert "d_fleet_share" not in p.metrics and p.reward == 0.0


@given(st.floats(0, 1e6), st.floats(0, 1e6), st.integers(0, 500), st.integers(0, 500))
def test_reward_bounded(x: float, y: float, s0: int, s1: int) -> None:
    a = snapshot([_ms(2, 0, 1, s0, x)], frozenset({2}))
    b = snapshot([_ms(2, 1e6, 1, s1, y)], frozenset({2}))
    assert -1.0 <= build(CLOSED, a, b).reward <= 1.0
