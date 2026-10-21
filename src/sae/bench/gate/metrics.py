"""The gate's judge: detector replay and scoring (X4 plan 1.2). Pure, stdlib only, deterministic.

Inputs are plain ``sae.contracts`` ``Observation`` sequences (``LabelledCampaign.observations``).
Nothing here depends on the game. UNVERIFIED (M0): real saves yield LegalViews that make these numbers meaningful.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sae.contracts.c5_bus import PolicyRef
from sae.contracts.c6_adapter import AdapterMode, Observation
from sae.contracts.c7_governor import GameState, HealthLevel
from sae.contracts.c8_config import EngineConfig
from sae.contracts.c10_profile import CampaignLabel, PolicyVersion, policy_sha256
from sae.contracts.common import COUNTER_TABLE, Archetype, Strategy
from sae.core import engine, features
from sae.core.belief import ARCHS, BeliefState, LikelihoodModel, _centre_clip, _norm, _raw_lls, update
from sae.core.dates import mi_of
from sae.core.features import FeatureMemory, FeatureVector
from sae.core.policy import ActivePolicy, from_doc
from sae.core.state import EngineCtx

__all__ = ["ECE_BINS", "FoldScore", "MonthPosterior", "detector_trace", "fast_lls", "fast_trace", "feature_steps",
           "fold_score", "round2", "strategy_trace"]

ECE_BINS = 10
Posterior = Mapping[Archetype, float]


@dataclass(frozen=True)
class MonthPosterior:
    """Detector posterior after the last observation of month ``mi``."""

    mi: int
    posterior: Posterior


@dataclass(frozen=True)
class FoldScore:
    """Score of one campaign trace against its label."""

    log_loss: float
    ece: float
    counter_ok: bool
    n_months: int


@dataclass(frozen=True)
class FeatureStep:
    """One observation reduced to belief inputs (policy independent, so the tuner computes it once)."""

    mi: int
    vector: FeatureVector
    new_evidence: bool
    k: int


def round2(x: float) -> float:
    """Round to 2 significant digits (sealed reporting). Non-finite values pass through."""
    if not math.isfinite(x) or x == 0.0:
        return x
    return float(f"{x:.2g}")


def _effective_cfg(policy: PolicyVersion) -> EngineConfig:
    d = policy.detector  # same 3-field fill as sae.daemon.policy_resolve.effective_engine_config
    return EngineConfig(rho=d.rho, likelihood_clip=d.likelihood_clip, belief_threshold=d.belief_threshold)


def _active(policy: PolicyVersion) -> ActivePolicy:
    ref = PolicyRef(version=policy.version, sha256=policy_sha256(policy), source="pinned")
    return from_doc(policy, ref)


def feature_steps(observations: Sequence[Observation]) -> list[FeatureStep]:
    """Run ``features.extract`` over the observations in order."""
    mem = FeatureMemory()
    out: list[FeatureStep] = []
    for obs in observations:
        mem, fv, new_ev, k = features.extract(mem, obs)
        out.append(FeatureStep(mi_of(obs.game_date), fv, new_ev, k))
    return out


def _last_per_month(rows: list[MonthPosterior]) -> list[MonthPosterior]:
    by_mi = {r.mi: r for r in rows}
    return [by_mi[m] for m in sorted(by_mi)]


def detector_trace(observations: Sequence[Observation], policy: PolicyVersion) -> list[MonthPosterior]:
    """Replay the detector with ``policy``'s tables and rho/clip; one posterior per game month."""
    model = _active(policy).likelihood
    cfg = _effective_cfg(policy)
    state = BeliefState()
    rows: list[MonthPosterior] = []
    for st in feature_steps(observations):
        state, _ = update(state, st.vector, st.new_evidence, st.k, cfg, model, st.mi)
        rows.append(MonthPosterior(st.mi, dict(zip(ARCHS, state.posterior, strict=True))))
    return _last_per_month(rows)


def fast_lls(vectors: Sequence[FeatureVector], model: LikelihoodModel) -> list[dict[str, list[float]]]:
    """Precomputed raw per-feature log-likelihood rows (``belief._raw_lls``) for the tuner grid."""
    return [_raw_lls(v, model) for v in vectors]


def fast_trace(steps: Sequence[FeatureStep], rows: Sequence[Mapping[str, list[float]]], rho: float,
               clip: float) -> list[MonthPosterior]:
    """Same posteriors as :func:`detector_trace` from precomputed ``fast_lls`` rows (mirrors ``belief.update``)."""
    n = len(ARCHS)
    post: tuple[float, ...] = BeliefState().posterior
    out: list[MonthPosterior] = []
    for st, raw in zip(steps, rows, strict=True):
        if st.new_evidence:
            kk = max(0, st.k)
            decay = (1.0 - rho) ** kk
            prior = _norm([1 / n + decay * (x - 1 / n) for x in post])
            w = min(1.0, kk / 12)  # belief.EVIDENCE_PERIOD_MONTHS
            per_f = [_centre_clip(v, clip) for v in raw.values()]
            total = [w * sum(c[i] for c in per_f) for i in range(n)]
            post = _norm([p * math.exp(t) for p, t in zip(prior, total, strict=True)])
        out.append(MonthPosterior(st.mi, dict(zip(ARCHS, post, strict=True))))
    return _last_per_month(out)


def strategy_trace(observations: Sequence[Observation], policy: PolicyVersion) -> list[tuple[int, Strategy]]:
    """Strategy in force after each month's last observation, from the real pure engine (commitment windows)."""
    act = _active(policy)
    cfg = _effective_cfg(policy)
    ctx = EngineCtx(mode=AdapterMode.REPLAY, cfg=cfg, game_state=GameState.PLAYING, level=HealthLevel.H0,
                    likelihood=act.likelihood, templates=act.templates, outcome_weights=act.weights)
    state = engine.initial_state(cfg)
    by_mi: dict[int, Strategy] = {}
    for obs in observations:
        state, _ = engine.on_observation(state, obs, ctx)
        if state.strategy.strategy is not None:
            by_mi[mi_of(obs.game_date)] = state.strategy.strategy
    return sorted(by_mi.items())


def _label_at(label: CampaignLabel, mi: int) -> Archetype | None:
    cur: Archetype | None = None
    for seg in label.segments:
        if mi_of(seg.from_date) <= mi:
            cur = seg.archetype
    return cur


def _dominant(label: CampaignLabel, end_mi: int) -> Archetype:
    span: Counter[Archetype] = Counter()
    segs = label.segments
    for i, seg in enumerate(segs):
        stop = mi_of(segs[i + 1].from_date) if i + 1 < len(segs) else max(end_mi, mi_of(seg.from_date))
        span[seg.archetype] += stop - mi_of(seg.from_date)
    best = max(span.values())
    return next(s.archetype for s in segs if span[s.archetype] == best)


def _argmax(p: Posterior) -> Archetype:
    return max(ARCHS, key=lambda a: (p[a], -ARCHS.index(a)))


def _ece(conf_ok: list[tuple[float, bool]]) -> float:
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(ECE_BINS)]
    for c, ok in conf_ok:
        bins[min(ECE_BINS - 1, int(c * ECE_BINS))].append((c, ok))
    n = len(conf_ok)
    return sum(len(b) / n * abs(sum(c for c, _ in b) / len(b) - sum(ok for _, ok in b) / len(b)) for b in bins if b)


def fold_score(trace: Sequence[MonthPosterior], label: CampaignLabel, burn_in_months: int = 12,
               counter_table: Mapping[Archetype, Strategy] | None = None) -> FoldScore:
    """Score a trace: mean monthly -log p(label), top-label ECE (10 equal-width bins), C3 section 6 counter check.

    ``counter_ok``: the counter of the modal argmax after ``burn_in_months`` equals the counter of the label's
    longest-segment archetype. ``counter_table`` defaults to the C3 table (``policy.fixed.counter_table`` is
    pinned equal to it). Months before the first label segment are not scored.
    """
    table = counter_table or COUNTER_TABLE
    scored = [(m, lab) for m in trace if (lab := _label_at(label, m.mi)) is not None]
    if not scored:
        return FoldScore(math.nan, math.nan, False, 0)
    loss = sum(-math.log(max(m.posterior[lab], 1e-12)) for m, lab in scored) / len(scored)
    ece = _ece([(max(m.posterior.values()), _argmax(m.posterior) == lab) for m, lab in scored])
    start = trace[0].mi
    late = [_argmax(m.posterior) for m, _ in scored if m.mi - start >= burn_in_months]
    if late:
        votes = Counter(late)
        top = max(votes.values())
        modal = next(a for a in ARCHS if votes[a] == top)
        ok = table[modal] == table[_dominant(label, trace[-1].mi)]
    else:
        ok = False
    return FoldScore(loss, ece, ok, len(scored))
