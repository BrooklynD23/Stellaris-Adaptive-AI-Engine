"""Feature extraction tests."""

from __future__ import annotations

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from sae.contracts.c4_parse import LegalView
from sae.contracts.c6_adapter import Observation
from sae.core.dates import date_of, mi_of
from sae.core.features import FeatureMemory, extract, reset_session


def lv(**kw: object) -> LegalView:
    return LegalView(observer_sid=1, in_contact=True, **kw)  # type: ignore[arg-type]


def obs(date: str, views: tuple[LegalView, ...], asof: str | None) -> Observation:
    return Observation(
        game_date=date, m=0, session_epoch=0, source="save", player_sid=None,
        managed=(), player_legal=views, legal_as_of=asof,
    )


def test_dates_roundtrip() -> None:
    assert mi_of("2200.01.15") == 2200 * 12
    assert date_of(mi_of("2230.07.09")) == "2230.07.01"


def test_out_of_contact_ignored() -> None:
    v = (LegalView(observer_sid=1, in_contact=False), lv(fleet_power_cat=2))
    _, fv, ev, _ = extract(FeatureMemory(), obs("2201.01.01", v, "2201.01.01"))
    assert fv.fleet_power_cat == 2 and ev
    out = (LegalView(observer_sid=1, in_contact=False),)
    _, fv, ev, _ = extract(FeatureMemory(), obs("2201.01.01", out, "2201.01.01"))
    assert fv.fleet_power_cat is None and not ev


def test_same_asof_once_and_older_no_evidence() -> None:
    v = (lv(systems_owned=5),)
    mem, _, ev, k = extract(FeatureMemory(), obs("2201.01.01", v, "2201.01.01"))
    assert ev and k == 12
    mem, _, ev, _ = extract(mem, obs("2201.02.01", v, "2201.01.01"))
    assert not ev
    mem, _, ev, _ = extract(mem, obs("2201.03.01", v, "2200.12.01"))
    assert not ev
    _, _, ev, k = extract(mem, obs("2201.04.01", v, "2201.04.01"))
    assert ev and k == 3


def test_border_ratio_and_median() -> None:
    v = (
        lv(fortified_border_starbases=1, frontier_systems=2, fleet_power_cat=-1, tech_cat=2),
        lv(fortified_border_starbases=2, frontier_systems=2, fleet_power_cat=2, tech_cat=0),
        lv(fortified_border_starbases=5),
    )
    _, fv, _, _ = extract(FeatureMemory(), obs("2201.01.01", v, "2201.01.01"))
    assert fv.border_fort_ratio == 0.75
    assert fv.fleet_power_cat == -1 and fv.tech_cat == 0  # lower medians


def test_expansion_rate_window() -> None:
    mem = FeatureMemory()
    mem, fv, _, _ = extract(mem, obs("2201.01.01", (lv(systems_owned=4),), "2201.01.01"))
    assert fv.expansion_rate is None
    _, fv, _, _ = extract(mem, obs("2202.01.01", (lv(systems_owned=10),), "2202.01.01"))
    assert fv.expansion_rate == 6.0


def test_first_war_censoring_anchor() -> None:
    mem, fv, _, _ = extract(FeatureMemory(), obs("2210.01.01", (lv(),), "2210.01.01"))
    assert fv.first_war_month == 0.0 and not fv.first_war_observed
    mem, fv, _, _ = extract(mem, obs("2211.01.01", (lv(),), "2211.01.01"))
    assert fv.first_war_month == 12.0 and not fv.first_war_observed
    mem, fv, _, _ = extract(mem, obs("2211.07.01", (lv(player_war_count=1),), "2211.07.01"))
    assert fv.first_war_month == 18.0 and fv.first_war_observed
    _, fv, _, _ = extract(mem, obs("2212.07.01", (lv(player_war_count=1),), "2212.07.01"))
    assert fv.first_war_month == 18.0


def test_session_reset_drops_memory() -> None:
    mem = FeatureMemory()
    for d, s in (("2201.01.01", 3), ("2202.01.01", 5), ("2203.01.01", 7)):
        mem, _, _, _ = extract(mem, obs(d, (lv(systems_owned=s, player_war_count=1),), d))
    r = reset_session(mem, mi_of("2201.06.01"))
    assert [s[0] for s in r.samples] == [mi_of("2201.01.01")]
    assert r.last_asof_mi == mi_of("2201.06.01") and r.anchor_mi is None and r.first_war_mi == mi_of("2201.01.01")
    r2 = reset_session(mem, mi_of("2200.06.01"))
    assert r2.samples == () and r2.first_war_mi is None
    _, _, ev, _ = extract(r, obs("2201.06.01", (lv(systems_owned=3),), "2201.01.01"))
    assert not ev  # same save again after a restart: not recounted
    _, _, ev, _ = extract(r, obs("2201.07.01", (lv(systems_owned=3),), "2201.07.01"))
    assert ev


lvs = st.builds(
    LegalView,
    observer_sid=st.integers(1, 16),
    in_contact=st.just(True),
    systems_owned=st.none() | st.integers(0, 500),
    frontier_systems=st.none() | st.integers(0, 50),
    fortified_border_starbases=st.none() | st.integers(0, 50),
    fleet_power_cat=st.none() | st.integers(-2, 2),
    tech_cat=st.none() | st.integers(-2, 2),
    at_war_with_observer=st.none() | st.booleans(),
    player_war_count=st.none() | st.integers(0, 9),
)


@settings(max_examples=100, deadline=None, derandomize=True)
@given(st.lists(st.lists(lvs, max_size=4), min_size=1, max_size=6))
def test_arbitrary_views_never_raise_or_nan(seq: list[list[LegalView]]) -> None:
    mem = FeatureMemory()
    for i, views in enumerate(seq):
        d = date_of(mi_of("2200.01.01") + i * 5)
        mem, fv, _, k = extract(mem, obs(d, tuple(views), d))
        assert k >= 0
        for x in (fv.border_fort_ratio, fv.expansion_rate, fv.first_war_month):
            assert x is None or math.isfinite(x)


def test_reset_keeps_observed_first_war_offset() -> None:
    mem, _, _, _ = extract(FeatureMemory(), obs("2200.01.01", (lv(),), "2200.01.01"))
    mem, _, _, _ = extract(mem, obs("2205.01.01", (lv(player_war_count=1),), "2205.01.01"))
    r = reset_session(mem, mi_of("2206.01.01"))
    _, fv, _, _ = extract(r, obs("2206.01.01", (lv(player_war_count=1),), "2206.01.01"))
    assert fv.first_war_observed and fv.first_war_month == 60.0


def test_reset_k_regressed_and_epoch_change_consistent() -> None:
    mem, _, _, _ = extract(FeatureMemory(), obs("2203.01.01", (lv(systems_owned=3),), "2203.01.01"))
    o = obs("2201.07.01", (lv(systems_owned=3),), "2201.07.01")
    _, _, ev, k = extract(reset_session(mem, mi_of("2201.06.01"), regressed=True), o)
    assert ev and k == 1
    o2 = obs("2203.07.01", (lv(systems_owned=3),), "2203.07.01")
    _, _, ev, k = extract(reset_session(mem, mi_of("2203.06.01"), regressed=False), o2)
    assert ev and k == 6  # anchors kept on every reset: no first-evidence weight


def test_first_war_new_only_once() -> None:
    mem, fv, _, _ = extract(FeatureMemory(), obs("2210.01.01", (lv(),), "2210.01.01"))
    assert not fv.first_war_new
    mem, fv, _, _ = extract(mem, obs("2211.07.01", (lv(player_war_count=1),), "2211.07.01"))
    assert fv.first_war_new
    _, fv, _, _ = extract(mem, obs("2212.07.01", (lv(player_war_count=1),), "2212.07.01"))
    assert fv.first_war_observed and not fv.first_war_new
