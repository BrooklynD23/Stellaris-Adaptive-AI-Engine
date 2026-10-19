"""Forgetful Bayes belief over archetypes (T4 plan U1 belief + v2 rules 4, 5, 7, 16)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from sae.contracts.c5_bus import BeliefUpdatePayload, Evidence
from sae.contracts.c8_config import EngineConfig
from sae.contracts.common import Archetype
from sae.core.features import FeatureVector

EVIDENCE_PERIOD_MONTHS = 12
PROB_FLOOR = 1e-6
CUSUM_KAPPA = 0.5
ARCHS: tuple[Archetype, ...] = tuple(Archetype)
_A = Archetype


def _t(**kw: tuple[float, ...] | tuple[float, float] | float) -> dict[Archetype, tuple[float, ...] | float]:
    out: dict[Archetype, tuple[float, ...] | float] = {}
    for a in ARCHS:
        v = kw[a.value]
        out[a] = (float(v),) if isinstance(v, float | int) else v
    return out


@dataclass(frozen=True)
class LikelihoodModel:
    """Per-archetype likelihood parameters. Every default is HYP (calibrated in M2).

    ``balanced`` is the middle ground: moderate expansion, flat-centred categoricals, rare wars.
    Categorical tuples are p(-2..2).
    """

    # HYP (all tables): v0 defaults aligned with the archetype profiles of T2's synth generator
    # (src/sae/adapters/synth.py PROFILES, itself HYP) so the game-free loop is self-consistent;
    # M2 replaces them with values fitted on labelled real campaigns.
    # (mu, sigma) of border_fort_ratio.
    border: dict[Archetype, tuple[float, float]] = field(
        default_factory=lambda: {_A.TURTLE: (0.85, 0.25), _A.TECH_RUSH: (0.1, 0.2),
                                 _A.EARLY_AGGRESSION: (0.5, 0.25), _A.BALANCED: (0.35, 0.25)}
    )
    # (mu, sigma) of expansion_rate in visible systems/year.
    expansion: dict[Archetype, tuple[float, float]] = field(
        default_factory=lambda: {_A.TURTLE: (0.5, 0.6), _A.TECH_RUSH: (1.2, 0.7),
                                 _A.EARLY_AGGRESSION: (3.0, 1.5), _A.BALANCED: (1.8, 0.9)}
    )
    # p(fleet_power_cat = -2..2).
    fleet: dict[Archetype, tuple[float, ...]] = field(
        default_factory=lambda: {_A.TURTLE: (0.05, 0.15, 0.50, 0.20, 0.10),
                                 _A.TECH_RUSH: (0.30, 0.35, 0.25, 0.07, 0.03),
                                 _A.EARLY_AGGRESSION: (0.02, 0.05, 0.13, 0.30, 0.50),
                                 _A.BALANCED: (0.10, 0.20, 0.40, 0.20, 0.10)}
    )
    # p(tech_cat = -2..2).
    tech: dict[Archetype, tuple[float, ...]] = field(
        default_factory=lambda: {_A.TURTLE: (0.05, 0.15, 0.50, 0.20, 0.10),
                                 _A.TECH_RUSH: (0.02, 0.05, 0.13, 0.30, 0.50),
                                 _A.EARLY_AGGRESSION: (0.10, 0.25, 0.40, 0.15, 0.10),
                                 _A.BALANCED: (0.10, 0.20, 0.40, 0.20, 0.10)}
    )
    # p(player at war now). A state feature restated each evidence tick (memoryless), so a player who turns
    # aggressive late is detected after the switch; a lifetime "months to first war" pdf would keep voting
    # for the old archetype forever (T7 CI-1 mixed schedule, 2026-10-19). first_war_month stays reported.
    war_now: dict[Archetype, float] = field(
        default_factory=lambda: {_A.TURTLE: 0.10, _A.TECH_RUSH: 0.10,
                                 _A.EARLY_AGGRESSION: 0.65, _A.BALANCED: 0.15}
    )


@dataclass(frozen=True)
class BeliefState:
    """Posterior in ``ARCHS`` order plus the diagnostic CUSUM (never read by strategy)."""

    posterior: tuple[float, ...] = (0.25, 0.25, 0.25, 0.25)
    last_evidence_mi: int | None = None
    change_score: float = 0.0
    surprise_mean: float = 0.0
    surprise_n: int = 0


def _norm(p: list[float]) -> tuple[float, ...]:
    p = [max(x, PROB_FLOOR) for x in p]
    s = sum(p)
    return tuple(x / s for x in p)


def _gauss(x: float, mu: float, sd: float) -> float:
    return -0.5 * ((x - mu) / sd) ** 2 - math.log(sd)


def _raw_lls(fv: FeatureVector, m: LikelihoodModel) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    if fv.border_fort_ratio is not None:
        # Ratios beyond the most turtle-like mean carry no extra signal (keeps P(turtle) monotone).
        # HYP: clamp ratio to the largest archetype mean so saturated forts do not over-favour turtle.
        x = min(fv.border_fort_ratio, max(mu for mu, _ in m.border.values()))
        out["border_fort_ratio"] = [_gauss(x, *m.border[a]) for a in ARCHS]
    if fv.expansion_rate is not None:
        out["expansion_rate"] = [_gauss(fv.expansion_rate, *m.expansion[a]) for a in ARCHS]
    if fv.fleet_power_cat is not None:
        out["fleet_power_cat"] = [math.log(m.fleet[a][fv.fleet_power_cat + 2]) for a in ARCHS]
    if fv.tech_cat is not None:
        out["tech_cat"] = [math.log(m.tech[a][fv.tech_cat + 2]) for a in ARCHS]
    if fv.at_war is not None:
        out["at_war"] = [math.log(m.war_now[a] if fv.at_war else 1.0 - m.war_now[a]) for a in ARCHS]
    return out


def _centre_clip(ll: list[float], clip: float) -> list[float]:
    mean = sum(ll) / len(ll)
    return [max(-clip, min(clip, v - mean)) for v in ll]


def _entropy(p: tuple[float, ...]) -> float:
    return max(0.0, -sum(x * math.log(x) for x in p if x > 0))


def _top_evidence(per_f: dict[str, list[float]], w: float) -> tuple[Evidence, ...]:
    rows: list[tuple[float, str, int]] = []
    for name, c in per_f.items():
        for i in range(len(ARCHS)):
            others = (sum(c) - c[i]) / (len(c) - 1)
            rows.append((w * (c[i] - others), name, i))
    rows = [r for r in rows if r[0] != 0.0]
    rows.sort(key=lambda r: (-abs(r[0]), r[1], r[2]))
    return tuple(Evidence(feature=n, archetype=ARCHS[i], llr=v) for v, n, i in rows[:5])


def update(
    state: BeliefState,
    features: FeatureVector,
    new_evidence: bool,
    k: int,
    cfg: EngineConfig,
    model: LikelihoodModel | None = None,
    mi: int | None = None,
) -> tuple[BeliefState, BeliefUpdatePayload]:
    """One belief step. Forgetting and evidence apply only when ``new_evidence``; else belief is unchanged."""
    model = model or LikelihoodModel()
    b = state.posterior
    n = len(ARCHS)
    fdict: dict[str, float | None] = {
        "border_fort_ratio": features.border_fort_ratio,
        "expansion_rate": features.expansion_rate,
        "fleet_power_cat": None if features.fleet_power_cat is None else float(features.fleet_power_cat),
        "first_war_month": features.first_war_month,
        "tech_cat": None if features.tech_cat is None else float(features.tech_cat),
        "at_war": None if features.at_war is None else float(features.at_war),
    }
    if not new_evidence:
        pri = dict(zip(ARCHS, b, strict=True))
        return state, BeliefUpdatePayload(
            prior=pri, loglik=dict.fromkeys(ARCHS, 0.0), posterior=pri, entropy=_entropy(b),
            features=fdict, top_evidence=(), change_score=state.change_score,
        )
    kk = max(0, k)
    decay = (1.0 - cfg.rho) ** kk
    prior = _norm([1 / n + decay * (x - 1 / n) for x in b])
    w = min(1.0, kk / EVIDENCE_PERIOD_MONTHS)
    per_f = {f: _centre_clip(v, cfg.likelihood_clip) for f, v in _raw_lls(features, model).items()}
    total = [w * sum(c[i] for c in per_f.values()) for i in range(n)]
    post = _norm([p * math.exp(t) for p, t in zip(prior, total, strict=True)])
    surprise = -math.log(sum(p * math.exp(t) for p, t in zip(prior, total, strict=True)))
    if w == 0.0:  # k=0: already-seen evidence carries no information; do not advance CUSUM
        score, mean, n1 = state.change_score, state.surprise_mean, state.surprise_n
    else:
        dev = surprise - state.surprise_mean if state.surprise_n else 0.0
        score = max(0.0, state.change_score + dev - CUSUM_KAPPA)
        n1 = state.surprise_n + 1
        mean = state.surprise_mean + (surprise - state.surprise_mean) / n1
    new = BeliefState(
        posterior=post,
        last_evidence_mi=mi if mi is not None else state.last_evidence_mi,
        change_score=score,
        surprise_mean=mean,
        surprise_n=n1,
    )
    payload = BeliefUpdatePayload(
        prior=dict(zip(ARCHS, prior, strict=True)),
        loglik=dict(zip(ARCHS, total, strict=True)),
        posterior=dict(zip(ARCHS, post, strict=True)),
        entropy=_entropy(post),
        features=fdict,
        top_evidence=_top_evidence(per_f, w),
        change_score=score,
    )
    return new, payload
