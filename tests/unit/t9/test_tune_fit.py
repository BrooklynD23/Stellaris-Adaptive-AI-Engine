"""``sae.tune.fit``: shrinkage maths, grid, LOCO selection (seeded, no game)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sae.bench.gate.metrics import FoldScore
from sae.contracts.c10_profile import LikelihoodParams
from sae.contracts.common import Archetype
from sae.core.belief import LikelihoodModel
from sae.core.policy import builtin_policy_doc
from sae.profile import ledger
from sae.tune import fit

from ._synth_campaigns import build_campaigns

PARENT = builtin_policy_doc()


def test_grid_is_180_and_threshold_fastest() -> None:
    g = fit.default_grid(0.40)
    assert len(g) == 180 == len({(p.kappa, p.rho, p.clip, p.threshold) for p in g})
    assert [p.threshold for p in g[:3]] == [0.40, 0.45, 0.55]
    assert g[0].kappa == 1 and g[-1].kappa == 100


def test_grid_puts_parent_threshold_first() -> None:
    g = fit.default_grid(PARENT.detector.belief_threshold)
    assert [p.threshold for p in g[:3]] == [0.45, 0.40, 0.55] and len(g) == 180


def test_counter_min() -> None:
    assert [fit.counter_min(n) for n in (5, 6, 10)] == [4, 5, 8]


def test_nig_prior_and_shrinkage() -> None:
    assert fit._nig(0.5, 0.2, [], 10) == (0.5, 0.2)
    mu_lo, _ = fit._nig(0.5, 0.2, [0.1] * 20, 1)
    mu_hi, _ = fit._nig(0.5, 0.2, [0.1] * 20, 100)
    assert mu_lo < mu_hi < 0.5 and mu_lo == pytest.approx(0.1 + 0.4 / 21, abs=1e-5)


def test_nig_sd_floor_and_data_spread() -> None:
    _, sd = fit._nig(0.5, 0.01, [0.3] * 50, 1)
    assert sd >= fit._SD_MIN
    _, wide = fit._nig(0.0, 0.1, [-5.0, 5.0] * 20, 1)
    assert wide > 4


def test_dirichlet_moves_to_data() -> None:
    p0 = (0.2,) * 5
    p = fit._dirichlet(p0, [2] * 20, 5)
    assert p[4] > 0.7 and abs(sum(p) - 1) < 1e-6
    assert fit._dirichlet(p0, [], 5) == p0


def test_beta_clamped() -> None:
    assert fit._beta(0.1, [True] * 1000, 1) == 0.999
    assert fit._beta(0.1, [False] * 1000, 1) == 0.001
    assert fit._beta(0.5, [], 3) == 0.5


@given(
    st.lists(st.integers(-2, 2), max_size=60),
    st.sampled_from(fit.KAPPAS),
    st.lists(st.floats(0.0005, 1.0), min_size=5, max_size=5),
)
def test_dirichlet_always_valid_cat5(values: list[int], kappa: float, raw: list[float]) -> None:
    p0 = [x / sum(raw) for x in raw]
    p = fit._dirichlet(p0, values, kappa)
    assert len(p) == 5 and min(p) >= 1e-4 and abs(math.fsum(p) - 1) <= 1e-6


@given(st.lists(st.floats(-3, 3), max_size=40), st.sampled_from(fit.KAPPAS))
def test_nig_bounded(xs: list[float], kappa: float) -> None:
    mu, sd = fit._nig(0.4, 0.25, xs, kappa)
    assert math.isfinite(mu) and fit._SD_MIN <= sd <= 100


def test_model_round_trip_and_unseen_archetype_keeps_parent() -> None:
    parent = LikelihoodModel()
    assert fit.fit_model(parent, [], 10) == parent
    assert fit.model_of(fit.params_of(parent)) == parent
    assert isinstance(fit.params_of(parent), LikelihoodParams)


def _cand(kappa: float, ll: float, feasible: bool = True) -> fit.Candidate:
    return fit.Candidate(fit.GridPoint(kappa, 0.02, 3.0, 0.45), (), ll, feasible)


def test_choose_min_loss_feasible_and_tie_order() -> None:
    assert fit.choose([]) is None
    assert fit.choose([_cand(1, 0.1, False)]) is None
    pick = fit.choose([_cand(1, 0.9), _cand(3, 0.5, False), _cand(10, 0.7), _cand(30, 0.7 + 1e-14)])
    assert pick is not None and pick.point.kappa == 10
    tie = fit.choose([_cand(3, 0.7), _cand(10, 0.7 + 1e-14)])
    assert tie is not None and tie.point.kappa == 3


def test_best_any_ignores_nan_and_constraints() -> None:
    cands = [_cand(1, math.nan, False), _cand(3, 0.8, False), _cand(10, 0.4, False)]
    assert fit.best_any(cands).point.kappa == 10
    assert fit.best_any([_cand(1, 0.5)]).point.kappa == 1
    assert fit.best_any([_cand(1, math.nan), _cand(3, 0.2)]).point.kappa == 3


SMALL = [fit.GridPoint(k, 0.02, 3.0, 0.45) for k in (1.0, 10.0, 100.0)]


def _prepared(tmp_path: Path, shift: bool = True) -> list[fit.Prepared]:
    sae, runs = tmp_path / "sae", tmp_path / "runs"
    runs.mkdir()
    build_campaigns(sae, runs, "synth", 6, shift, seed=3)
    lcs = ledger.labelled_campaigns(sae, "default", runs, "tune", True)
    return [fit.prepare(c.state.campaign_id, c.label, c.observations) for c in lcs]


def test_prepare_labels_months(tmp_path: Path) -> None:
    prep = _prepared(tmp_path)
    assert len(prep) == 5
    switcher = prep[1]
    assert {a for a, _ in switcher.months} <= set(Archetype) and len({a for a, _ in switcher.months}) == 2
    assert len(switcher.months) > 100


def test_shifted_fixture_improves_loco_over_builtin(tmp_path: Path) -> None:
    prep = _prepared(tmp_path)
    cands = fit.loco(prep, PARENT, SMALL)
    base = fit.score_parent(prep, PARENT)
    base_ll = sum(f.score.log_loss for f in base) / len(base)
    assert min(c.mean_log_loss for c in cands) < base_ll
    assert all(len(c.folds) == 5 for c in cands)


def test_loco_deterministic_and_feasibility_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prep = _prepared(tmp_path)
    a = fit.loco(prep, PARENT, SMALL)
    assert a == fit.loco(prep, PARENT, SMALL)
    monkeypatch.setattr(fit, "ECE_MAX", -1.0)
    assert not any(c.feasible for c in fit.loco(prep, PARENT, SMALL))


def test_detector_for_valid(tmp_path: Path) -> None:
    prep = _prepared(tmp_path)
    det = fit.detector_for(prep, PARENT, SMALL[1])
    assert det.rho == 0.02 and det.belief_threshold == 0.45
    assert det.likelihood != PARENT.detector.likelihood


def test_fold_nan_is_infeasible() -> None:
    f = fit.Fold("c", FoldScore(math.nan, math.nan, False, 0))
    assert not f.score.ece <= fit.ECE_MAX
