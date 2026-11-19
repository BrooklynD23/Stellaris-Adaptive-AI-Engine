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
    mem, fv, ev, _ = extract(FeatureMemory(), obs("2201.01.01", v, "2201.01.01"))
    assert fv.fleet_power_cat == 2 and ev  # Wave 1.5 rev 3.1 B1: the first evidence of the campaign counts
    _, fv, ev, _ = extract(mem, obs("2202.01.01", v, "2202.01.01"))
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
        lv(fortified_border_starbases=1, frontier_systems=3, fleet_power_cat=-1, tech_cat=2),
        lv(fortified_border_starbases=2, frontier_systems=3, fleet_power_cat=2, tech_cat=0),
        lv(fortified_border_starbases=5),
    )
    mem, fv, _, _ = extract(FeatureMemory(), obs("2201.01.01", v, "2201.01.01"))
    assert fv.border_fort_ratio == 0.5
    _, fv, _, _ = extract(mem, obs("2202.01.01", v, "2202.01.01"))  # F2: categoricals need a stable set
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


def test_observer_loss_gives_no_expansion_rate_and_no_evidence_row() -> None:
    from sae.contracts.common import Archetype  # noqa: F401
    from sae.core.belief import BeliefState, update

    from .fakes_cfg import eff

    both = (lv(systems_owned=10), LegalView(observer_sid=2, in_contact=True, systems_owned=10))
    lost = (lv(systems_owned=4),)  # observer 2 gone; the remaining view sees fewer systems
    mem, _, _, _ = extract(FeatureMemory(), obs("2201.01.01", both, "2201.01.01"))
    _, fv, ev, k = extract(mem, obs("2202.01.01", lost, "2202.01.01"))
    assert ev and fv.expansion_rate is None
    _, p = update(BeliefState(), fv, ev, k, eff())
    assert not any(e.feature == "expansion_rate" for e in p.top_evidence)


def test_fog_gap_gives_no_rate_until_window_refills() -> None:
    from sae.core.belief import BeliefState, update

    from .fakes_cfg import eff

    full = (lv(systems_owned=10), LegalView(observer_sid=2, in_contact=True, systems_owned=10))
    lost = (lv(systems_owned=4),)
    mem = FeatureMemory()
    st = BeliefState()
    rates: list[float | None] = []
    tops: list[float] = []
    grown = (lv(systems_owned=13), LegalView(observer_sid=2, in_contact=True, systems_owned=13))
    for date, views in (("2201.01.01", full), ("2202.01.01", lost), ("2203.01.01", full), ("2204.01.01", grown)):
        mem, fv, ev, k = extract(mem, obs(date, views, date))
        st, p = update(st, fv, ev, k, eff())
        rates.append(fv.expansion_rate)
        tops.append(max(p.posterior.values()))
    # Month 24 (return): None and no swing. Month 36: rate from post-return samples only (13-10 over 12 months).
    assert rates == [None, None, None, 3.0]
    assert tops[2] == tops[0] == 0.25


def test_continuous_observer_rate_unchanged() -> None:
    mem = FeatureMemory()
    for date, n in (("2201.01.01", 4), ("2202.01.01", 10), ("2203.01.01", 22)):
        mem, fv, _, _ = extract(mem, obs(date, (lv(systems_owned=n),), date))
    assert fv.expansion_rate == 9.0  # oldest in-window sample: (22 - 4) / 2 years, as before the fog fix


def test_none_report_while_in_contact_breaks_visibility_run() -> None:
    from sae.core.belief import BeliefState, update

    from .fakes_cfg import eff

    mem = FeatureMemory()
    st = BeliefState()
    rates: list[float | None] = []
    seq = (("2201.01.01", 4), ("2202.01.01", None), ("2203.01.01", None), ("2204.01.01", 10), ("2205.01.01", 13))
    for date, n in seq:
        mem, fv, ev, k = extract(mem, obs(date, (lv(systems_owned=n),), date))
        st, p = update(st, fv, ev, k, eff())
        rates.append(fv.expansion_rate)
        if date == "2204.01.01":  # return save: no rate, no evidence row
            assert not any(e.feature == "expansion_rate" for e in p.top_evidence)
    assert rates == [None, None, None, None, 3.0]  # 13-10 over 12 months, post-return samples only


# -- Wave 1.5 (plan rev 2 §6): single-tick artefact fixes ------------------------------------------------------
def ov(sid: int, **kw: object) -> LegalView:
    return LegalView(observer_sid=sid, in_contact=True, **kw)


def at(d: str, *views: LegalView) -> Observation:
    return obs(d, views, d)


def test_t1_border_ratio_needs_one_observer_with_min_frontier() -> None:
    for n in (1, 2):
        one = ov(2, frontier_systems=n, fortified_border_starbases=n)
        _, fv, _, _ = extract(FeatureMemory(), at("2201.01.01", one))
        assert fv.border_fort_ratio is None
    _, fv, _, _ = extract(FeatureMemory(), at("2201.01.01", ov(2, frontier_systems=3, fortified_border_starbases=1)))
    assert fv.border_fort_ratio == 1 / 3
    # One player system bordering 3 observers: pooled 3, but no observer alone sees 3 systems.
    shared = [ov(s, frontier_systems=1, fortified_border_starbases=1) for s in (2, 3, 4)]
    _, fv, _, _ = extract(FeatureMemory(), at("2201.01.01", *shared))
    assert fv.border_fort_ratio is None
    # Gate passes on one view; the ratio stays pooled over every view.
    mixed = (ov(2, frontier_systems=3, fortified_border_starbases=0), ov(3, frontier_systems=1,
             fortified_border_starbases=1))
    _, fv, _, _ = extract(FeatureMemory(), at("2201.01.01", *mixed))
    assert fv.border_fort_ratio == 0.25


def test_t2_categorical_set_change_counts_only_the_last_value() -> None:
    def o(sid: int, x: int) -> LegalView:
        return ov(sid, fleet_power_cat=x, tech_cat=x)

    a, b, c = o(2, 2), o(3, 2), o(4, 2)
    mem, fv, ev, _ = extract(FeatureMemory(), at("2201.01.01", a, b))
    assert ev and fv.fleet_power_cat == 2 and fv.tech_cat == 2  # rev 3.1 B1: first evidence counts
    assert (mem.fleet_last, mem.tech_last) == (2, 2)
    for d, views in (("2203.01.01", (a, b, c)), ("2204.01.01", (a, b)), ("2205.01.01", (a, c))):  # gain, loss, swap
        mem, fv, ev, _ = extract(mem, at(d, *views))
        assert ev and fv.fleet_power_cat == 2 and fv.tech_cat == 2, d  # same values: the intersection counts
    # Gain of a disagreeing observer: the intersection {a, c} still gives 2 = last, counts.
    mem, fv, _, _ = extract(mem, at("2206.01.01", a, c, o(5, -2)))
    assert fv.fleet_power_cat == 2
    # Loss that shifts the median ({2, 2, -2} -> {-2}): dropped, last stays 2.
    mem, fv, ev, _ = extract(mem, at("2207.01.01", o(5, -2)))
    assert ev and fv.fleet_power_cat is None and fv.tech_cat is None and mem.fleet_last == 2
    # Stable set: the new value counts and becomes the last counted value.
    mem, fv, _, _ = extract(mem, at("2208.01.01", o(5, -2)))
    assert fv.fleet_power_cat == -2 and mem.fleet_last == -2
    # Empty intersection (swap {5} -> {6}): dropped.
    mem, fv, _, _ = extract(mem, at("2209.01.01", o(6, -2)))
    assert fv.fleet_power_cat is None
    # Same observer set, only tech changes reporter: tech uses the intersection, fleet the unchanged set.
    mem, fv, _, _ = extract(mem, at("2210.01.01", o(6, -2)))
    mem, fv, _, _ = extract(mem, at("2211.01.01", ov(6, fleet_power_cat=1), ov(7, fleet_power_cat=1, tech_cat=1)))
    assert fv.fleet_power_cat is None and fv.tech_cat is None  # fleet {6} -> {6,7} median 1 != -2; tech no share


def test_t2a_log_ticks_before_first_save_are_no_gap() -> None:
    mem = FeatureMemory()
    for d in ("2200.06.01", "2200.07.01"):
        mem, _, ev, _ = extract(mem, at(d))
        assert not ev
    assert mem.fleet_vis is None and mem.tech_vis is None
    _, fv, ev, _ = extract(mem, at("2201.01.01", ov(2, fleet_power_cat=1, tech_cat=-1)))
    assert ev and fv.fleet_power_cat == 1 and fv.tech_cat == -1


def test_t2b_sole_observer_gap_drops_return_tick() -> None:
    from sae.core.belief import BeliefState, update

    from .fakes_cfg import eff

    a = ov(2, fleet_power_cat=2, tech_cat=2)
    mem, _, _, _ = extract(FeatureMemory(), at("2201.01.01", a))
    mem, fv, _, _ = extract(mem, at("2202.01.01", a))
    assert fv.fleet_power_cat == 2
    for m in range(1, 25):  # observer gone for 24 months
        mem, fv, ev, _ = extract(mem, at(date_of(mi_of("2202.01.01") + m)))
        assert not ev
    assert mem.fleet_vis == mem.tech_vis == frozenset()
    st0 = BeliefState(posterior=(0.1, 0.2, 0.3, 0.4))
    mem, fv, ev, k = extract(mem, at("2204.02.01", a))
    assert ev and k == 25 and fv.fleet_power_cat is None and fv.tech_cat is None
    _, p = update(st0, fv, ev, k, eff())
    assert p.posterior == p.prior


def test_t3_tick_after_change_counts_with_k_from_dropped_tick() -> None:
    mem, _, _, _ = extract(FeatureMemory(), at("2201.01.01", ov(2, fleet_power_cat=-2)))
    a, b = ov(2, fleet_power_cat=2), ov(3, fleet_power_cat=2)
    mem, fv, ev, k = extract(mem, at("2201.07.01", a, b))
    assert ev and k == 6 and fv.fleet_power_cat is None  # gain with a shifted intersection value: dropped
    mem, fv, ev, k = extract(mem, at("2202.04.01", a, b))
    assert ev and k == 9 and fv.fleet_power_cat == 2  # k counts from the dropped tick, which advanced it
    assert mem.last_evidence_mi == mi_of("2202.04.01")


def test_t4_old_memory_without_sets_drops_once() -> None:
    a = ov(2, fleet_power_cat=1, tech_cat=1, systems_owned=5)
    old = FeatureMemory(vis=frozenset({2}), last_asof_mi=mi_of("2201.01.01"), last_evidence_mi=mi_of("2201.01.01"))
    assert old.fleet_vis is None and old.tech_vis is None
    mem, fv, _, _ = extract(old, at("2202.01.01", a))
    assert fv.fleet_power_cat is None and fv.tech_cat is None
    _, fv, _, _ = extract(mem, at("2203.01.01", a))
    assert fv.fleet_power_cat == 1 and fv.tech_cat == 1


def test_t5_to_t8_at_war_counts_offensive_wars_only() -> None:
    cases: list[tuple[tuple[LegalView, ...], bool | None]] = [
        # T-5: managed AI attacks, player defends.
        ((ov(2, player_war_count=1, player_offensive_war_count=0, at_war_with_observer=True),), None),
        # T-6: player attacks (one view enough).
        ((ov(2, player_war_count=1, player_offensive_war_count=1), ov(3, player_war_count=0)), True),
        # T-7: no wars anywhere.
        ((ov(2, player_war_count=0, player_offensive_war_count=0), ov(3, player_war_count=0)), False),
        # T-8: role unknown, or known defensive.
        ((ov(2, player_war_count=1),), None),
        ((ov(2, player_war_count=2, player_offensive_war_count=0),), None),
        ((ov(2, player_war_count=0), ov(3, at_war_with_observer=True)), None),
        ((ov(2, at_war_with_observer=False),), None),  # no war info at all
    ]
    for views, want in cases:
        _, fv, _, _ = extract(FeatureMemory(), at("2201.01.01", *views))
        assert fv.at_war is want, views


def test_first_war_month_keeps_any_war_meaning() -> None:
    _, fv, _, _ = extract(FeatureMemory(), at("2201.01.01", ov(2, player_war_count=1, player_offensive_war_count=0)))
    assert fv.first_war_observed and fv.at_war is None


def test_t9_reset_session_keeps_both_sets() -> None:
    a = ov(2, fleet_power_cat=0, tech_cat=None)
    mem, _, _, _ = extract(FeatureMemory(), at("2201.01.01", a))
    r = reset_session(mem, mi_of("2201.06.01"))
    assert r.fleet_vis == frozenset({2}) and r.tech_vis == frozenset()
    _, fv, _, _ = extract(r, at("2201.07.01", a))
    assert fv.fleet_power_cat == 0
