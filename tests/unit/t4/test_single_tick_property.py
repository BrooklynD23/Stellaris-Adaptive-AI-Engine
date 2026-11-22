"""Wave 1.5 property: no single artefact tick moves the belief (plan rev 2 §6, review N6; rev 3.1 rules).

Every case runs warm-up ticks through ``extract`` only, then the last tick through ``extract`` and
``belief.update`` with the built-in policy's likelihoods and detector numbers, at ``k >= 12``. An artefact tick
(``Case.want`` empty) must give no belief feature and leave posterior == prior, from a uniform prior and from a
prior decayed from a non-uniform posterior. A counted tick (``Case.want`` set, rev 3.1 first evidence and value
guard) must carry exactly the true categorical values and move the belief only as those values alone would.
The negative control (real evidence, stable set) must cross the 0.45 threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from sae.contracts.c4_parse import LegalView
from sae.contracts.c6_adapter import Observation
from sae.core.belief import BeliefState, update
from sae.core.dates import date_of, mi_of
from sae.core.features import FeatureMemory, FeatureVector, extract
from sae.core.policy import load_builtin

from .fakes_cfg import eff

POLICY = load_builtin()
CFG = eff(rho=POLICY.doc.detector.rho, likelihood_clip=POLICY.doc.detector.likelihood_clip,
          belief_threshold=POLICY.doc.detector.belief_threshold)
THRESHOLD = 0.45
START = mi_of("2201.01.01")
UNIFORM = BeliefState()
DECAYED = BeliefState(posterior=(0.05, 0.05, 0.85, 0.05))  # non-uniform; the k >= 12 gap decays it
PRIORS = {"uniform": UNIFORM, "decayed": DECAYED}
BELIEF_FEATURES = ("border_fort_ratio", "expansion_rate", "fleet_power_cat", "tech_cat", "at_war")

Views = tuple[LegalView, ...]
A, B, C = 2, 3, 4


@dataclass(frozen=True)
class Case:
    """Warm-up ticks (an empty tuple = no in-contact view) then the last tick, 12 months apart.

    ``want`` maps each categorical the last tick must count to its value; empty = an artefact tick.
    ``init`` is the memory loaded before the warm-up (a pre-1.5 state.json).
    """

    warm: tuple[Views, ...]
    art: Views
    want: dict[str, int] = field(default_factory=dict)
    init: FeatureMemory = field(default_factory=FeatureMemory)


def v(sid: int, **kw: object) -> LegalView:
    return LegalView(observer_sid=sid, in_contact=True, **kw)


# Field sets of the artefacts (merged per observer to combine them).
WAR_AI = {"player_war_count": 1, "player_offensive_war_count": 0, "at_war_with_observer": True}
WAR_COATTACK = {"player_war_count": 1, "player_offensive_war_count": 0, "at_war_with_observer": False}
WAR_THIRD = {"player_war_count": 1, "player_offensive_war_count": 0, "at_war_with_observer": False}
WAR_UNKNOWN = {"player_war_count": 1}
WARS = {"war_ai": WAR_AI, "war_ai_coattacker": WAR_COATTACK, "war_third_party": WAR_THIRD,
        "war_role_unknown": WAR_UNKNOWN}
CATS = range(-2, 3)
SET_CHANGES = {"gain": ((A,), (A, B)), "loss": ((A, B), (A,)), "swap": ((A, B), (A, C))}


def border(n: int, full: bool) -> dict[str, int]:
    return {"frontier_systems": n, "fortified_border_starbases": n if full else 0}


def _cases() -> dict[str, Case]:
    out: dict[str, Case] = {}
    for cat in ("fleet_power_cat", "tech_cat"):
        for x in CATS:
            # Rev 3.1 B1: the first evidence of the campaign counts; a set change with the same values counts on
            # the intersection (its median equals the last counted value).
            out[f"{cat}={x}/first_tick"] = Case((), (v(A, **{cat: x}), v(B, **{cat: x})), {cat: x})
            for name, (before, after) in SET_CHANGES.items():
                warm = tuple(v(s, **{cat: x}) for s in before)
                out[f"{cat}={x}/{name}"] = Case((warm, warm), tuple(v(s, **{cat: x}) for s in after), {cat: x})
        for x in (-2, 2):
            solo = (v(A, **{cat: x}),)
            out[f"{cat}={x}/sole_observer_gap"] = Case((solo, solo, (), ()), solo)
            pair = (v(A, **{cat: x}), v(B, **{cat: x}))
            out[f"{cat}={x}/first_save_after_log_ticks"] = Case(((), ()), pair, {cat: x})
            out[f"{cat}={x}/alternating_same_values"] = Case((pair, (v(A, **{cat: x}),)), pair, {cat: x})
            # Differing values (rev 3.1 §4).
            out[f"{cat}={x}/gain_intersection_equals_last"] = Case((solo, solo), (*solo, v(B, **{cat: -x})), {cat: x})
            out[f"{cat}={x}/partial_swap_intersection_equals_last"] = Case(
                (pair, pair), (v(A, **{cat: x}), v(C, **{cat: -x})), {cat: x})
            mixed = (v(A, **{cat: x}), v(B, **{cat: -x}))  # lower median = -2
            if x == 2:
                out[f"{cat}={x}/loss_shifts_median"] = Case((mixed, mixed), solo)
                out[f"{cat}={x}/flicker_differing_values"] = Case((mixed, mixed, solo), mixed)
            out[f"{cat}={x}/resumed_session"] = Case((pair, pair, ()), pair)
            out[f"{cat}={x}/pre_1_5_state_json"] = Case(
                (), pair, init=FeatureMemory(last_asof_mi=START - 12, last_evidence_mi=START - 12))
    for full in (False, True):
        for n in (1, 2):
            out[f"border_full={full}/one_view_frontier_{n}"] = Case((), (v(A, **border(n, full)),))
        out[f"border_full={full}/three_observers_one_system"] = Case(
            (), tuple(v(s, **border(1, full)) for s in (A, B, C)))
    for name, war in WARS.items():
        out[name] = Case((), (v(A, **war), v(B, player_war_count=1)))
    # Combined artefacts; the categoricals of a first tick or a same-value set change count (rev 3.1).
    out["combo/first_tick_all"] = Case((), (
        v(A, fleet_power_cat=2, tech_cat=-2, **border(2, False), **WAR_AI),
        v(B, fleet_power_cat=2, tech_cat=-2, **border(1, False), **WAR_COATTACK),
        v(C, fleet_power_cat=2, tech_cat=-2, **border(1, False), **WAR_UNKNOWN)),
        {"fleet_power_cat": 2, "tech_cat": -2})
    stable = (v(A, fleet_power_cat=2, tech_cat=2), v(B, fleet_power_cat=2, tech_cat=2))
    out["combo/swap_border_coattacker"] = Case((stable, stable), (
        v(A, fleet_power_cat=2, tech_cat=2, **border(2, True), **WAR_COATTACK),
        v(C, fleet_power_cat=2, tech_cat=2, **border(1, True), **WAR_COATTACK)),
        {"fleet_power_cat": 2, "tech_cat": 2})
    solo = (v(A, fleet_power_cat=-2, tech_cat=2),)
    out["combo/sole_gap_third_party_border"] = Case((solo, solo, (), ()), (
        v(A, fleet_power_cat=-2, tech_cat=2, **border(2, True), **WAR_THIRD),))
    out["combo/gain_ai_war_shared_system"] = Case((solo, solo), (
        v(A, fleet_power_cat=-2, tech_cat=2, **border(1, False), **WAR_AI),
        v(B, fleet_power_cat=-2, tech_cat=2, **border(1, False), **WAR_AI)),
        {"fleet_power_cat": -2, "tech_cat": 2})
    return out


CASES = _cases()
ARTEFACTS = {n: c for n, c in CASES.items() if not c.want}
COUNTED = {n: c for n, c in CASES.items() if c.want}


def _obs(i: int, views: Views) -> Observation:
    d = date_of(START + 12 * i)
    return Observation(game_date=d, m=0, session_epoch=0, source="save", player_sid=1, managed=(),
                       player_legal=views, legal_as_of=d if views else None)


def run_case(case: Case, prior: BeliefState) -> tuple[dict[str, float | None], dict[str, float],
                                                        dict[str, float], int]:
    """Artefact-tick belief payload: (features, prior, posterior, k)."""
    mem = case.init
    for i, views in enumerate(case.warm):
        mem, _, _, _ = extract(mem, _obs(i, views))
    _, fv, ev, k = extract(mem, _obs(len(case.warm), case.art))
    assert ev
    _, p = update(prior, fv, ev, k, CFG, POLICY.likelihood)
    return p.features, {a.value: x for a, x in p.prior.items()}, {a.value: x for a, x in p.posterior.items()}, k


@pytest.mark.parametrize("prior_name", sorted(PRIORS))
@pytest.mark.parametrize("name", sorted(ARTEFACTS))
def test_artefact_tick_leaves_belief_at_prior(name: str, prior_name: str) -> None:
    feats, pri, post, k = run_case(ARTEFACTS[name], PRIORS[prior_name])
    assert k >= 12
    assert all(feats[f] is None for f in BELIEF_FEATURES), feats
    assert post == pytest.approx(pri, rel=1e-12, abs=1e-15)
    if prior_name == "uniform":
        assert max(post.values()) < THRESHOLD


@pytest.mark.parametrize("prior_name", sorted(PRIORS))
@pytest.mark.parametrize("name", sorted(COUNTED))
def test_counted_tick_carries_only_the_true_values(name: str, prior_name: str) -> None:
    case, prior = COUNTED[name], PRIORS[prior_name]
    feats, _, post, k = run_case(case, prior)
    assert k >= 12
    assert {f: feats[f] for f in BELIEF_FEATURES} == {f: case.want.get(f) for f in BELIEF_FEATURES}
    # The belief moves exactly as the true categorical values alone move it (posterior == prior iff they are
    # uninformative under the built-in likelihoods).
    _, ref = update(prior, FeatureVector(**case.want), True, k, CFG, POLICY.likelihood)
    assert post == pytest.approx({a.value: x for a, x in ref.posterior.items()}, rel=1e-12, abs=1e-15)


def test_case_list_covers_every_route() -> None:
    per_cat = 5 * 4 + 2 * (1 + 6) + 2  # first tick and set changes, the x = +-2 routes, the x = 2-only routes
    assert len(CASES) == 2 * per_cat + 2 * 3 + len(WARS) + 4
    assert len(COUNTED) == 2 * (5 * 4 + 2 * 4) + 3


def test_negative_control_crosses_threshold() -> None:
    stable = (v(A, fleet_power_cat=2), v(B, fleet_power_cat=2))
    art = (v(A, fleet_power_cat=2, **border(3, False), player_war_count=1, player_offensive_war_count=1),
           v(B, fleet_power_cat=2))
    feats, _, post, k = run_case(Case((stable,), art), UNIFORM)
    assert k >= 12 and feats["fleet_power_cat"] == 2.0 and feats["border_fort_ratio"] == 0.0
    assert feats["at_war"] == 1.0
    assert max(post.values()) > THRESHOLD
