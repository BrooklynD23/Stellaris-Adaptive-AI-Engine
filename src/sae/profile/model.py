"""Normal-Normal partial pooling, decayed Dirichlet and rolling-origin folds (C10 section 2; stdlib maths only)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from sae.contracts.c10_profile import (
    DRIFT_ALARM_MIN,
    ELIGIBLE_MIN_CAMPAIGNS,
    FEATURE_NAMES,
    HALF_LIFE_CAMPAIGNS,
    CampaignSummary,
    FeaturePosterior,
    FoldScore,
    ProfileDoc,
)
from sae.contracts.common import Archetype
from sae.profile.summary import BUILDER_VERSION

__all__ = ["HYP", "build_profile"]

#: Designer constants (hyper_mu, hyper_sd), copied from the C10 section 2 ProfileDoc example. UNVERIFIED against
#: real play: M2 data should replace them.
HYP: dict[str, tuple[float, float]] = {
    "expansion_rate_early": (1.5, 0.8),
    "expansion_rate_late": (0.8, 0.6),
    "border_fort_ratio": (0.4, 0.25),
    "first_war_year": (25.0, 15.0),
    "war_share": (0.2, 0.15),
    "fleet_power_cat_mean": (0.0, 0.8),
    "tech_cat_mean": (0.0, 0.8),
    "economy_cat_mean": (0.0, 0.8),
}
#: z of the central 90 % interval used by the drift alarm.
Z90 = 1.645


def _sigma(feature: str) -> float:
    # Designer choice: within-player sd = hyper_sd, so player_sd >= hyper_sd and drift_alarm rarely fires.
    # Revisit with M2 data.
    return HYP[feature][1]


def _weights(n: int) -> list[float]:
    """Decay weights, oldest first; the newest of the ``n`` campaigns has age 0."""
    return [0.5 ** ((n - 1 - i) / HALF_LIFE_CAMPAIGNS) for i in range(n)]


def _fit(summaries: Sequence[CampaignSummary]) -> dict[str, FeaturePosterior]:
    w = _weights(len(summaries))
    out: dict[str, FeaturePosterior] = {}
    for name in FEATURE_NAMES:
        hmu, hsd = HYP[name]
        sigma = _sigma(name)
        pairs = [(wi, s.features[name].value) for wi, s in zip(w, summaries, strict=True)]  # type: ignore[index]
        obs = [(wi, x) for wi, x in pairs if x is not None]
        n_eff = math.fsum(wi for wi, _ in obs)
        precision = 1 / hsd**2 + n_eff / sigma**2
        mu = (hmu / hsd**2 + math.fsum(wi * x for wi, x in obs) / sigma**2) / precision
        out[name] = FeaturePosterior(
            hyper_mu=hmu, hyper_sd=hsd, player_mu=mu, player_sd=math.sqrt(1 / precision + sigma**2), n_eff=n_eff
        )
    return out


def _log_normal(x: float, mu: float, sd: float) -> float:
    return -0.5 * math.log(2 * math.pi) - math.log(sd) - 0.5 * ((x - mu) / sd) ** 2


def _value(s: CampaignSummary, name: str) -> float | None:
    return s.features[name].value  # type: ignore[index]


def _folds(summaries: Sequence[CampaignSummary]) -> tuple[FoldScore, ...]:
    folds: list[FoldScore] = []
    for k in range(1, len(summaries)):
        fit = _fit(summaries[:k])
        delta = 0.0
        for name in FEATURE_NAMES:
            x = _value(summaries[k], name)
            if x is None:
                continue
            hmu, hsd = HYP[name]
            p = fit[name]
            delta += _log_normal(x, p.player_mu, p.player_sd) - _log_normal(x, hmu, math.hypot(hsd, _sigma(name)))
        folds.append(FoldScore(k=k, log_score_delta=delta))
    return tuple(folds)


def _drift_outside(summaries: Sequence[CampaignSummary]) -> int:
    if len(summaries) < 2:
        return 0
    fit = _fit(summaries[:-1])
    count = 0
    for name in FEATURE_NAMES:
        x = _value(summaries[-1], name)
        if x is not None and abs(x - fit[name].player_mu) > Z90 * fit[name].player_sd:
            count += 1
    return count


def build_profile(summaries: Sequence[CampaignSummary], player_key: str, built_at: datetime) -> ProfileDoc:
    """Fit the profile over ``summaries`` (oldest to newest); pure and deterministic."""
    w = _weights(len(summaries))
    alpha = {
        a: 1.0 + math.fsum(wi * s.final_belief[a] for wi, s in zip(w, summaries, strict=True) if s.final_belief)
        for a in Archetype
    }
    folds = _folds(summaries)
    drift = _drift_outside(summaries)
    mean = math.fsum(f.log_score_delta for f in folds) / len(folds) if folds else 0.0
    return ProfileDoc(
        player_key=player_key,
        mode="strict",
        builder_version=BUILDER_VERSION,
        built_at=built_at,
        source_campaigns=tuple(s.campaign_id for s in summaries),
        archetype_alpha=alpha,
        features=_fit(summaries),
        folds=folds,
        eligible=len(summaries) >= ELIGIBLE_MIN_CAMPAIGNS and bool(folds) and mean > 0,
        drift_alarm=drift >= DRIFT_ALARM_MIN,
        drift_outside=drift,
    )
