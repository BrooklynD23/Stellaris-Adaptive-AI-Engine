"""W1-6: memoised ``fit.loco`` equals a naive unmemoised reference for every candidate and fold."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sae.core.belief import LikelihoodModel
from sae.core.policy import builtin_policy_doc
from sae.tune import fit

from .test_tune_fit import _prepared

PARENT = builtin_policy_doc()
GRID = [fit.GridPoint(k, rho, 3.0, thr) for k in (1.0, 10.0) for rho in (0.02, 0.08) for thr in (0.40, 0.45, 0.55)]


def _model(prep: list[fit.Prepared], i: int, base: LikelihoodModel, kappa: float) -> LikelihoodModel:
    return fit.fit_model(base, [p for j, p in enumerate(prep) if j != i], kappa)


def _naive(prep: list[fit.Prepared], grid: list[fit.GridPoint]) -> list[fit.Candidate]:
    base = fit.model_of(PARENT.detector.likelihood)
    need = fit.counter_min(len(prep))
    out: list[fit.Candidate] = []
    for pt in grid:
        folds = tuple(
            fit.Fold(h.campaign_id, fit._score(h, _model(prep, i, base, pt.kappa), pt.rho, pt.clip))
            for i, h in enumerate(prep)
        )
        ok = all(f.score.ece <= fit.ECE_MAX for f in folds) and sum(f.score.counter_ok for f in folds) >= need
        out.append(fit.Candidate(pt, folds, fit._mean_loss(folds), ok))
    return out


def test_memo_loco_equals_naive_every_candidate(tmp_path: Path) -> None:
    prep = _prepared(tmp_path)
    memo, ref = fit.loco(prep, PARENT, GRID), _naive(prep, GRID)
    assert len(memo) == len(GRID) and memo == ref
    best = fit.choose(memo) or fit.best_any(memo)  # tiny synthetic grid may have no feasible point
    assert best == (fit.choose(ref) or fit.best_any(ref))

    def sha(c: fit.Candidate) -> str:
        det = fit.detector_for(prep, PARENT, c.point)
        return hashlib.sha256(det.model_dump_json().encode()).hexdigest()

    assert sha(best) == sha(fit.choose(ref) or fit.best_any(ref))
