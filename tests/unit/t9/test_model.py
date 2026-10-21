"""Profile maths: pooling, decay, folds, eligibility, drift."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from sae.contracts.c10_profile import FEATURE_NAMES, CampaignSummary, SummaryFeature
from sae.contracts.common import Archetype
from sae.profile.model import HYP, build_profile

AT = datetime(2026, 10, 1, tzinfo=UTC)


def summ(n: int, values: dict[str, float | None] | None = None, belief: Archetype | None = Archetype.TURTLE,
         base: float | None = None) -> CampaignSummary:
    vals = {name: (HYP[name][0] if base is None else base) for name in FEATURE_NAMES}
    vals.update(values or {})  # type: ignore[arg-type]
    fb = None
    if belief is not None:
        fb = {a: (0.7 if a == belief else 0.1) for a in Archetype}
    return CampaignSummary(
        campaign_id=f"c-{n:016x}", builder_version="0.1.0", game_versions=(), policy_sha256=(),
        start_date="2200.01.01", last_date="2250.01.01",
        features={k: SummaryFeature(value=v, n_obs=10) for k, v in vals.items()},  # type: ignore[misc]
        final_belief=fb,
    )  # fmt: skip


def test_one_campaign_alpha_is_one_plus_belief() -> None:
    p = build_profile([summ(1)], "default", AT)
    assert p.archetype_alpha[Archetype.TURTLE] == pytest.approx(1.7)
    assert p.archetype_alpha[Archetype.BALANCED] == pytest.approx(1.1)
    assert p.folds == () and not p.eligible and not p.drift_alarm and p.drift_outside == 0
    assert p.source_campaigns == (summ(1).campaign_id,) and p.built_at == AT and p.mode == "strict"


def test_no_campaigns_is_the_hyperprior() -> None:
    p = build_profile([], "default", AT)
    assert all(a == 1.0 for a in p.archetype_alpha.values())
    f = p.features["war_share"]
    assert f.player_mu == f.hyper_mu and f.n_eff == 0
    assert f.player_sd == pytest.approx(math.sqrt(0.15**2 + 0.15**2))


def test_shrinkage_toward_hyperprior() -> None:
    hmu, hsd = HYP["war_share"]
    p = build_profile([summ(1, {"war_share": 0.8})], "default", AT)
    f = p.features["war_share"]
    prec = 1 / hsd**2 + 1 / hsd**2
    assert f.player_mu == pytest.approx((hmu / hsd**2 + 0.8 / hsd**2) / prec) == pytest.approx((hmu + 0.8) / 2)
    assert hmu < f.player_mu < 0.8 and f.n_eff == 1.0 and f.player_sd >= hsd


def test_missing_values_do_not_count() -> None:
    p = build_profile([summ(1, {"first_war_year": None})], "default", AT)
    f = p.features["first_war_year"]
    assert f.n_eff == 0 and f.player_mu == f.hyper_mu


def test_decay_weights_and_none_belief() -> None:
    s = [summ(1, {"war_share": 0.0}, Archetype.TURTLE), summ(2, {"war_share": 0.6}, None)]
    p = build_profile(s, "default", AT)
    w_old = 0.5 ** (1 / 5)
    assert p.features["war_share"].n_eff == pytest.approx(1 + w_old)
    assert p.archetype_alpha[Archetype.TURTLE] == pytest.approx(1 + w_old * 0.7)  # newest has no belief
    hmu, hsd = HYP["war_share"]
    prec = 1 / hsd**2 + (1 + w_old) / hsd**2
    assert p.features["war_share"].player_mu == pytest.approx((hmu / hsd**2 + 0.6 / hsd**2) / prec)


def test_fold_relative_age_and_fold_count() -> None:
    s = [summ(1, {"war_share": 0.0}), summ(2, {"war_share": 0.5}), summ(3, {"war_share": 0.2})]
    p = build_profile(s, "default", AT)
    assert [f.k for f in p.folds] == [1, 2]
    hmu, hsd = HYP["war_share"]
    sig = hsd

    def logn(x: float, mu: float, sd: float) -> float:
        return -0.5 * math.log(2 * math.pi) - math.log(sd) - 0.5 * ((x - mu) / sd) ** 2

    # fold 2: fit on campaigns 1..2, campaign 2 has age 0, campaign 1 age 1; score campaign 3 on war_share only
    w = [0.5 ** (1 / 5), 1.0]
    prec = 1 / hsd**2 + sum(w) / sig**2
    mu = (hmu / hsd**2 + (w[0] * 0.0 + w[1] * 0.5) / sig**2) / prec
    sd = math.sqrt(1 / prec + sig**2)
    only_ws = logn(0.2, mu, sd) - logn(0.2, hmu, math.sqrt(hsd**2 + sig**2))
    others = sum(
        logn(HYP[n][0], *_fit_const(n, w)) - logn(HYP[n][0], HYP[n][0], math.hypot(HYP[n][1], HYP[n][1]))
        for n in FEATURE_NAMES
        if n != "war_share"
    )
    assert p.folds[1].log_score_delta == pytest.approx(only_ws + others)


def _fit_const(name: str, w: list[float]) -> tuple[float, float]:
    """Posterior (mu, sd) when every observation equals the hyper mean: mu stays hyper_mu."""
    hmu, hsd = HYP[name]
    prec = 1 / hsd**2 + sum(w) / hsd**2
    return hmu, math.sqrt(1 / prec + hsd**2)


def test_eligible_needs_eight_campaigns_and_positive_mean() -> None:
    # Every campaign sits at the hyper mean: the fitted profile beats the hyperprior on every fold (narrower sd).
    seven = build_profile([summ(i) for i in range(7)], "default", AT)
    eight = build_profile([summ(i) for i in range(8)], "default", AT)
    assert all(f.log_score_delta > 0 for f in eight.folds) and len(eight.folds) == 7
    assert not seven.eligible and eight.eligible


def test_not_eligible_when_profile_worse() -> None:
    far = [summ(i, base=HYP["war_share"][0]) for i in range(8)]
    bad = [*far[:-1], summ(99, {n: HYP[n][0] + 50 * HYP[n][1] for n in FEATURE_NAMES})]
    p = build_profile(bad, "default", AT)
    assert len(p.source_campaigns) == 8 and not p.eligible


def test_drift_alarm_when_newest_is_far_off_on_three_features() -> None:
    old = [summ(i) for i in range(5)]
    off = {n: HYP[n][0] + 6 * HYP[n][1] for n in ("expansion_rate_early", "border_fort_ratio", "war_share")}
    p = build_profile([*old, summ(9, off)], "default", AT)
    assert p.drift_outside == 3 and p.drift_alarm
    two = {n: v for n, v in list(off.items())[:2]}
    q = build_profile([*old, summ(9, two)], "default", AT)
    assert q.drift_outside == 2 and not q.drift_alarm


def test_player_sd_at_least_hyper_sd() -> None:
    p = build_profile([summ(i) for i in range(30)], "default", AT)
    assert all(f.player_sd >= f.hyper_sd for f in p.features.values())
