"""Shrinkage fit of the detector tables toward a parent policy, and the leave-one-campaign-out (LOCO) search.

Pure and deterministic: no clock, no randomness, no I/O. Scoring is delegated to ``sae.bench.gate.metrics``
(``fast_lls``, ``fast_trace``, ``fold_score``); nothing here is a scorer of its own.

Priors (all centred on the parent so ``kappa`` pseudo-months pull toward it):
Normal-Inverse-Gamma for ``border`` and ``expansion``, Dirichlet ``kappa * p0`` for ``fleet`` and ``tech``,
Beta ``(kappa * p0, kappa * (1 - p0))`` for ``war_now``. UNVERIFIED (M0/M2): real campaigns make these fits meaningful.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass

from sae.bench.gate.metrics import FeatureStep, FoldScore, fast_lls, fast_trace, feature_steps, fold_score
from sae.contracts.c6_adapter import Observation
from sae.contracts.c10_profile import CampaignLabel, DetectorParams, LikelihoodParams, PolicyVersion
from sae.contracts.common import Archetype
from sae.core.belief import LikelihoodModel
from sae.core.dates import mi_of
from sae.core.features import FeatureVector

__all__ = [
    "ECE_MAX",
    "Candidate",
    "Fold",
    "GridPoint",
    "Prepared",
    "best_any",
    "choose",
    "counter_min",
    "default_grid",
    "detector_for",
    "fit_model",
    "loco",
    "model_of",
    "params_of",
    "prepare",
    "score_parent",
]

ECE_MAX = 0.10
KAPPAS = (1.0, 3.0, 10.0, 30.0, 100.0)
RHOS = (0.01, 0.02, 0.04, 0.08)
CLIPS = (2.0, 3.0, 4.0)
THRESHOLDS = (0.40, 0.45, 0.55)
_SD_MIN = 0.05
_P_MIN = 2e-4  # categorical floor; C10 needs p >= 1e-4 and sum 1 within 1e-6
_ND = 6  # decimals kept in fitted tables (cross-platform sha stability)
_TIE = 1e-12


@dataclass(frozen=True)
class GridPoint:
    """One hyper-parameter point."""

    kappa: float
    rho: float
    clip: float
    threshold: float


def default_grid(parent_thr: float) -> list[GridPoint]:
    """The 180-point grid; the order is the tie-break (threshold varies fastest).

    The threshold is not scored (``fold_score`` ignores it), so the parent's value is tried first: ties then keep it.
    """
    thrs = sorted(THRESHOLDS, key=lambda t: (abs(t - parent_thr), t))
    return [GridPoint(*p) for p in itertools.product(KAPPAS, RHOS, CLIPS, thrs)]


@dataclass(frozen=True)
class Prepared:
    """A labelled campaign reduced to what the fit and the scorer need (policy independent)."""

    campaign_id: str
    label: CampaignLabel
    steps: tuple[FeatureStep, ...]
    months: tuple[tuple[Archetype, FeatureVector], ...]  # one labelled vector per game month


def _label_at(label: CampaignLabel, mi: int) -> Archetype | None:
    cur: Archetype | None = None
    for seg in label.segments:
        if mi_of(seg.from_date) <= mi:
            cur = seg.archetype
    return cur


def prepare(campaign_id: str, label: CampaignLabel, observations: Sequence[Observation]) -> Prepared:
    """Precompute feature steps once; months are labelled by the label segment active at that date."""
    steps = feature_steps(observations)
    last: dict[int, FeatureVector] = {s.mi: s.vector for s in steps if s.new_evidence}
    months = tuple((a, last[mi]) for mi in sorted(last) if (a := _label_at(label, mi)) is not None)
    return Prepared(campaign_id, label, tuple(steps), months)


# --- shrinkage ----------------------------------------------------------------------------------------------


def _nig(mu0: float, sd0: float, xs: list[float], kappa: float) -> tuple[float, float]:
    """Posterior (mean mu, E[sigma] from E[sigma^2]) of a Normal-Inverse-Gamma centred on (mu0, sd0)."""
    n = len(xs)
    if n == 0:
        return mu0, sd0
    xbar = math.fsum(xs) / n
    ss = math.fsum((x - xbar) ** 2 for x in xs)
    mu = (kappa * mu0 + n * xbar) / (kappa + n)
    beta = kappa / 2 * sd0**2 + ss / 2 + kappa * n * (xbar - mu0) ** 2 / (2 * (kappa + n))
    sd = math.sqrt(beta / ((kappa + n) / 2))
    return round(mu, _ND), round(min(100.0, max(_SD_MIN, sd)), _ND)


def _dirichlet(p0: Sequence[float], values: list[int], kappa: float) -> tuple[float, ...]:
    counts = [sum(1 for v in values if v + 2 == i) for i in range(5)]
    raw = [max(_P_MIN, (kappa * p + c) / (kappa + len(values))) for p, c in zip(p0, counts, strict=True)]
    total = math.fsum(raw)
    head = [round(x / total, _ND) for x in raw[:4]]
    return (*head, round(1.0 - math.fsum(head), _ND))


def _beta(p0: float, wars: list[bool], kappa: float) -> float:
    p = (kappa * p0 + sum(wars)) / (kappa + len(wars))
    return round(min(0.999, max(0.001, p)), _ND)


def fit_model(parent: LikelihoodModel, prepared: Sequence[Prepared], kappa: float) -> LikelihoodModel:
    """Shrink ``parent`` toward the labelled months of ``prepared``; archetypes without data keep the parent."""
    by_arch: dict[Archetype, list[FeatureVector]] = {a: [] for a in Archetype}
    for p in prepared:
        for arch, fv in p.months:
            by_arch[arch].append(fv)
    border, expansion, fleet, tech, war = {}, {}, {}, {}, {}
    for a, fvs in by_arch.items():
        border[a] = _nig(
            *parent.border[a], [v.border_fort_ratio for v in fvs if v.border_fort_ratio is not None], kappa
        )
        expansion[a] = _nig(
            *parent.expansion[a], [v.expansion_rate for v in fvs if v.expansion_rate is not None], kappa
        )
        fleet[a] = _dirichlet(parent.fleet[a], [v.fleet_power_cat for v in fvs if v.fleet_power_cat is not None], kappa)
        tech[a] = _dirichlet(parent.tech[a], [v.tech_cat for v in fvs if v.tech_cat is not None], kappa)
        war[a] = _beta(parent.war_now[a], [v.at_war for v in fvs if v.at_war is not None], kappa)
    return LikelihoodModel(border=border, expansion=expansion, fleet=fleet, tech=tech, war_now=war)


def model_of(params: LikelihoodParams) -> LikelihoodModel:
    """C10 tables to the engine's ``LikelihoodModel``."""
    return LikelihoodModel(
        border={a: (g[0], g[1]) for a, g in params.border.items()},
        expansion={a: (g[0], g[1]) for a, g in params.expansion.items()},
        fleet={a: tuple(c) for a, c in params.fleet.items()},
        tech={a: tuple(c) for a, c in params.tech.items()},
        war_now=dict(params.war_now),
    )


def params_of(model: LikelihoodModel) -> LikelihoodParams:
    """Engine tables to C10 ``LikelihoodParams`` (validates ranges)."""
    return LikelihoodParams(
        border=dict(model.border), expansion=dict(model.expansion), fleet=dict(model.fleet),
        tech=dict(model.tech), war_now=dict(model.war_now),
    )  # fmt: skip


# --- LOCO ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fold:
    """One held-out campaign's score."""

    campaign_id: str
    score: FoldScore


@dataclass(frozen=True)
class Candidate:
    """A grid point with its per-fold scores."""

    point: GridPoint
    folds: tuple[Fold, ...]
    mean_log_loss: float
    feasible: bool


def counter_min(n_folds: int) -> int:
    """Folds that must have the right counter: ceil(4/5 n), i.e. 4 of 5 (V-1)."""
    return (4 * n_folds + 4) // 5


def _score(p: Prepared, model: LikelihoodModel, rho: float, clip: float) -> FoldScore:
    rows = fast_lls([s.vector for s in p.steps], model)
    return fold_score(fast_trace(p.steps, rows, rho, clip), p.label)


def score_parent(prepared: Sequence[Prepared], parent: PolicyVersion) -> tuple[Fold, ...]:
    """Parent policy scored on every campaign (no fit): the baseline the tuned LOCO is compared with."""
    model = model_of(parent.detector.likelihood)
    return tuple(
        Fold(p.campaign_id, _score(p, model, parent.detector.rho, parent.detector.likelihood_clip)) for p in prepared
    )


def _mean_loss(folds: Sequence[Fold]) -> float:
    return math.fsum(f.score.log_loss for f in folds) / len(folds)


def loco(
    prepared: Sequence[Prepared], parent: PolicyVersion, grid: Sequence[GridPoint] | None = None
) -> list[Candidate]:
    """Leave-one-campaign-out over ``grid`` (default 180 points): fit on n-1, score the held-out one, rotate.

    Feasible = ECE <= ``ECE_MAX`` on every fold and the counter right on >= ``counter_min(n)`` folds.
    """
    pts = list(grid) if grid is not None else default_grid(parent.detector.belief_threshold)
    base = model_of(parent.detector.likelihood)
    cache: dict[tuple[int, float], LikelihoodModel] = {}
    out: list[Candidate] = []
    need = counter_min(len(prepared))
    for pt in pts:
        folds: list[Fold] = []
        for i, held in enumerate(prepared):
            model = cache.get((i, pt.kappa))
            if model is None:
                model = cache[(i, pt.kappa)] = fit_model(base, [p for j, p in enumerate(prepared) if j != i], pt.kappa)
            folds.append(Fold(held.campaign_id, _score(held, model, pt.rho, pt.clip)))
        feasible = all(f.score.ece <= ECE_MAX for f in folds) and sum(f.score.counter_ok for f in folds) >= need
        out.append(Candidate(pt, tuple(folds), _mean_loss(folds), feasible))
    return out


def choose(cands: Sequence[Candidate]) -> Candidate | None:
    """Feasible candidate with the lowest mean log loss; ties (within 1e-12) go to the earlier grid point."""
    best: Candidate | None = None
    for c in cands:
        if c.feasible and (best is None or c.mean_log_loss < best.mean_log_loss - _TIE):
            best = c
    return best


def best_any(cands: Sequence[Candidate]) -> Candidate:
    """Lowest-loss candidate regardless of constraints (for the no-feasible-point table)."""
    best = cands[0]
    for c in cands[1:]:
        if not math.isnan(c.mean_log_loss) and (
            math.isnan(best.mean_log_loss) or c.mean_log_loss < best.mean_log_loss - _TIE
        ):
            best = c
    return best


def detector_for(prepared: Sequence[Prepared], parent: PolicyVersion, point: GridPoint) -> DetectorParams:
    """Final fit on all ``prepared`` campaigns at ``point``."""
    model = fit_model(model_of(parent.detector.likelihood), prepared, point.kappa)
    return DetectorParams(rho=point.rho, likelihood_clip=point.clip, belief_threshold=point.threshold,
                          likelihood=params_of(model))  # fmt: skip
