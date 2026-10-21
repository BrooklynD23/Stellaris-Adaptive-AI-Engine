"""Gate judge: determinism, hand-made scores, round2, fast path equals slow path."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sae.bench.gate import GATE_VERSION
from sae.bench.gate.metrics import (
    MonthPosterior,
    detector_trace,
    fast_lls,
    fast_trace,
    feature_steps,
    fold_score,
    round2,
    strategy_trace,
)
from sae.contracts.c4_parse import LegalView
from sae.contracts.c6_adapter import Observation
from sae.contracts.c10_profile import CampaignLabel, LabelSegment
from sae.contracts.common import Archetype, Strategy
from sae.core.belief import ARCHS
from sae.core.dates import date_of
from sae.core.policy import builtin_policy_doc, load_builtin

A = Archetype
POLICY = builtin_policy_doc()


def _obs(mi: int, aggressive: bool) -> Observation:
    view = LegalView(
        observer_sid=1, in_contact=True, systems_owned=10 + mi // 3, frontier_systems=4,
        fortified_border_starbases=0 if aggressive else 4, fleet_power_cat=2 if aggressive else 0,
        tech_cat=0, at_war_with_observer=aggressive, player_war_count=1 if aggressive else 0)
    return Observation(game_date=date_of(2200 * 12 + mi), m=mi, session_epoch=0, source="save", player_sid=1,
                       managed=(), player_legal=(view,), legal_as_of=date_of(2200 * 12 + mi))


def _campaign(n: int = 40, aggressive: bool = True) -> list[Observation]:
    return [_obs(i, aggressive) for i in range(n)]


def _label(*segs: tuple[int, Archetype]) -> CampaignLabel:
    return CampaignLabel(campaign_id=None, source="synth_schedule", labelled_at=datetime(2026, 1, 1, tzinfo=UTC),
                         blind=True, segments=tuple(LabelSegment(from_date=date_of(2200 * 12 + m), archetype=a)
                                                    for m, a in segs))


def _trace(probs: list[tuple[int, Archetype, float]]) -> list[MonthPosterior]:
    out = []
    for mi, a, p in probs:
        rest = (1 - p) / 3
        out.append(MonthPosterior(2200 * 12 + mi, {x: (p if x == a else rest) for x in ARCHS}))
    return out


def test_gate_version() -> None:
    assert GATE_VERSION == "0.1.0"


def test_determinism_and_shape() -> None:
    obs = _campaign()
    t1, t2 = detector_trace(obs, POLICY), detector_trace(obs, POLICY)
    assert t1 == t2 and len(t1) == 40
    assert all(abs(sum(m.posterior.values()) - 1) < 1e-9 for m in t1)
    assert strategy_trace(obs, POLICY) == strategy_trace(obs, POLICY)


def test_strategy_trace_runs() -> None:
    s = strategy_trace(_campaign(), POLICY)
    assert s and all(isinstance(x, Strategy) for _, x in s)
    assert [m for m, _ in s] == sorted({m for m, _ in s})


def test_detector_moves_toward_aggression() -> None:
    last = detector_trace(_campaign(60), POLICY)[-1].posterior
    assert max(last, key=lambda a: last[a]) == A.EARLY_AGGRESSION


def test_fast_equals_slow() -> None:
    obs = _campaign(50) + [_obs(50 + i, False) for i in range(30)]
    steps = feature_steps(obs)
    model = load_builtin().likelihood
    rows = fast_lls([s.vector for s in steps], model)
    fast = fast_trace(steps, rows, POLICY.detector.rho, POLICY.detector.likelihood_clip)
    slow = detector_trace(obs, POLICY)
    assert [m.mi for m in fast] == [m.mi for m in slow]
    for f, s in zip(fast, slow, strict=True):
        for a in ARCHS:
            assert f.posterior[a] == pytest.approx(s.posterior[a], abs=1e-12)


def test_log_loss_hand() -> None:
    tr = _trace([(0, A.TURTLE, 0.5), (1, A.TURTLE, 0.25)])
    sc = fold_score(tr, _label((0, A.TURTLE)), burn_in_months=0)
    assert sc.log_loss == pytest.approx((math.log(2) + math.log(4)) / 2)
    assert sc.n_months == 2


def test_months_before_label_unscored() -> None:
    tr = _trace([(0, A.TURTLE, 0.9), (5, A.TURTLE, 0.9)])
    assert fold_score(tr, _label((5, A.TURTLE))).n_months == 1
    empty = fold_score(tr, _label((50, A.TURTLE)))
    assert empty.n_months == 0 and not empty.counter_ok and math.isnan(empty.log_loss)


def test_ece_hand() -> None:
    # confidence 0.9 and right half the time -> |0.9 - 0.5| = 0.4
    tr = _trace([(0, A.TURTLE, 0.9), (1, A.BALANCED, 0.9)])
    assert fold_score(tr, _label((0, A.TURTLE)), burn_in_months=0).ece == pytest.approx(0.4)
    perfect = _trace([(0, A.TURTLE, 1.0), (1, A.TURTLE, 1.0)])
    assert fold_score(perfect, _label((0, A.TURTLE)), burn_in_months=0).ece == pytest.approx(0.0)


def test_counter_ok_and_burn_in() -> None:
    # label: turtle 0-19, aggression 20-59 -> dominant aggression. Detector wrong early, right after burn-in.
    tr = _trace([(i, A.TURTLE, 0.8) for i in range(20)] + [(i, A.EARLY_AGGRESSION, 0.8) for i in range(20, 60)])
    lab = _label((0, A.TURTLE), (20, A.EARLY_AGGRESSION))
    assert fold_score(tr, lab, burn_in_months=12).counter_ok
    wrong = _trace([(i, A.TURTLE, 0.8) for i in range(60)])
    assert not fold_score(wrong, lab, burn_in_months=12).counter_ok
    assert not fold_score(tr, lab, burn_in_months=100).counter_ok  # nothing past burn-in


def test_counter_equivalence_balanced_vs_other() -> None:
    # counter compare is on strategies: two different archetypes never share a counter in C3
    tr = _trace([(i, A.BALANCED, 0.7) for i in range(30)])
    assert fold_score(tr, _label((0, A.BALANCED))).counter_ok
    assert not fold_score(tr, _label((0, A.TURTLE))).counter_ok


def test_modal_tie_breaks_by_arch_order() -> None:
    tr = _trace([(0, A.TURTLE, 0.7), (1, A.BALANCED, 0.7)])
    assert fold_score(tr, _label((0, A.TURTLE)), burn_in_months=0).counter_ok


def test_round2_cases() -> None:
    assert round2(0.123456) == 0.12
    assert round2(1234.5) == 1200.0
    assert round2(0.0) == 0.0
    assert round2(-0.04567) == -0.046
    assert math.isnan(round2(math.nan)) and round2(math.inf) == math.inf


@given(st.floats(min_value=-1e9, max_value=1e9, allow_nan=False))
def test_round2_idempotent(x: float) -> None:
    assert round2(round2(x)) == round2(x)
