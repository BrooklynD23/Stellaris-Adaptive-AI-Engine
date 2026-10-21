"""Belief tests."""

from __future__ import annotations

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from sae.contracts.c5_bus import BeliefUpdatePayload
from sae.contracts.common import Archetype
from sae.core.belief import ARCHS, BeliefState, LikelihoodModel, update
from sae.core.features import FeatureVector

from .fakes_cfg import eff

CFG = eff()
T = ARCHS.index(Archetype.TURTLE)
E = ARCHS.index(Archetype.EARLY_AGGRESSION)

feats = st.builds(
    FeatureVector,
    border_fort_ratio=st.none() | st.floats(0, 5, allow_nan=False),
    expansion_rate=st.none() | st.floats(-50, 50, allow_nan=False),
    fleet_power_cat=st.none() | st.integers(-2, 2),
    first_war_month=st.none() | st.floats(0, 2000, allow_nan=False),
    first_war_observed=st.booleans(),
    at_war=st.none() | st.booleans(),
    tech_cat=st.none() | st.integers(-2, 2),
)
priors = st.lists(st.floats(0.01, 1), min_size=4, max_size=4).map(
    lambda xs: BeliefState(posterior=tuple(x / sum(xs) for x in xs))
)


@settings(max_examples=100, deadline=None, derandomize=True)
@given(priors, feats, st.integers(0, 60))
def test_posterior_sums_to_one_and_payload_valid(s: BeliefState, f: FeatureVector, k: int) -> None:
    new, payload = update(s, f, True, k, CFG)
    assert math.isclose(sum(new.posterior), 1.0, abs_tol=1e-9)
    assert all(math.isfinite(x) and x > 0 for x in new.posterior)
    BeliefUpdatePayload.model_validate(payload.model_dump())
    assert len(payload.top_evidence) <= 5


@settings(max_examples=100, deadline=None, derandomize=True)
@given(priors, st.floats(0, 1), st.floats(0, 1))
def test_monotone_in_turtle_border(s: BeliefState, a: float, b: float) -> None:
    lo, hi = sorted((a, b))
    _, p_lo = update(s, FeatureVector(border_fort_ratio=lo), True, 12, CFG)
    _, p_hi = update(s, FeatureVector(border_fort_ratio=hi), True, 12, CFG)
    assert p_hi.posterior[Archetype.TURTLE] >= p_lo.posterior[Archetype.TURTLE] - 1e-12


def test_clipping_bounds_log_odds() -> None:
    s = BeliefState()
    _, p = update(s, FeatureVector(border_fort_ratio=1e6), True, 12, CFG)
    for a in Archetype:
        for b in Archetype:
            lo = math.log(p.posterior[a] / p.posterior[b]) - math.log(p.prior[a] / p.prior[b])
            assert lo <= 2 * CFG.likelihood_clip + 1e-9


def test_all_none_posterior_equals_prior() -> None:
    s = BeliefState(posterior=(0.4, 0.3, 0.2, 0.1))
    new, p = update(s, FeatureVector(), True, 0, CFG)
    assert p.posterior == p.prior
    assert all(math.isclose(a, b) for a, b in zip(new.posterior, s.posterior, strict=True))


def test_no_evidence_leaves_state_unchanged() -> None:
    s = BeliefState(posterior=(0.7, 0.1, 0.1, 0.1))
    new, p = update(s, FeatureVector(border_fort_ratio=0.9), False, 30, CFG)
    assert new == s and p.posterior == p.prior


def test_forgetting_only_on_evidence_ticks() -> None:
    s = BeliefState(posterior=(0.7, 0.1, 0.1, 0.1))
    _, p = update(s, FeatureVector(), True, 24, CFG)
    assert abs(p.posterior[Archetype.TURTLE] - 0.25) < abs(0.7 - 0.25)
    assert math.isclose(p.prior[Archetype.TURTLE], 0.25 + 0.98**24 * 0.45)
    _, q = update(s, FeatureVector(), False, 24, CFG)
    assert q.prior[Archetype.TURTLE] == 0.7


def test_evidence_weight_scales_with_k() -> None:
    f = FeatureVector(border_fort_ratio=0.9)
    _, short = update(BeliefState(), f, True, 3, CFG)
    _, full = update(BeliefState(), f, True, 12, CFG)
    assert short.posterior[Archetype.TURTLE] < full.posterior[Archetype.TURTLE]
    assert math.isclose(short.loglik[Archetype.TURTLE] * 4, full.loglik[Archetype.TURTLE])


def test_at_war_is_state_evidence_and_first_war_month_is_report_only() -> None:
    m = LikelihoodModel()
    war, _ = update(BeliefState(), FeatureVector(at_war=True), True, 12, CFG, m)
    peace, _ = update(BeliefState(), FeatureVector(at_war=False), True, 12, CFG, m)
    assert war.posterior[E] > 0.25 > peace.posterior[E]
    same, p = update(BeliefState(), FeatureVector(first_war_month=5.0, first_war_observed=True), True, 12, CFG, m)
    assert same.posterior == (0.25,) * 4 and p.features["first_war_month"] == 5.0


def test_change_score_nonneg_and_isolated() -> None:
    s = BeliefState()
    for i in range(5):
        s, p = update(s, FeatureVector(border_fort_ratio=0.1 * i, fleet_power_cat=2), True, 12, CFG)
        assert s.change_score >= 0 and p.change_score == s.change_score


def test_k_zero_does_not_advance_cusum() -> None:
    s0 = BeliefState(change_score=1.5, surprise_mean=0.7, surprise_n=3)
    s1, _ = update(s0, FeatureVector(fleet_power_cat=2), True, 0, eff(), mi=5)
    assert (s1.change_score, s1.surprise_mean, s1.surprise_n) == (1.5, 0.7, 3)
    assert s1.posterior == s0.posterior


def test_late_war_after_long_peace_is_not_penalised() -> None:
    """A late switcher's war counts as much as an early one (no lifetime first-war pdf)."""
    from sae.core.belief import _raw_lls
    a = _raw_lls(FeatureVector(first_war_month=6.0, first_war_observed=True, at_war=True), LikelihoodModel())
    b = _raw_lls(FeatureVector(first_war_month=150.0, first_war_observed=True, at_war=True), LikelihoodModel())
    assert a == b and "first_war_month" not in a

