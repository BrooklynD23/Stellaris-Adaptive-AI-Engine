"""Fog-legal feature extraction from ``Observation.player_legal`` (in-contact views only)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, replace

from sae.contracts.c4_parse import LegalView
from sae.contracts.c6_adapter import Observation
from sae.contracts.common import CLAUSEWITZ_INT_MAX
from sae.core.dates import mi_of

FEATURES: tuple[str, ...] = (
    "border_fort_ratio",
    "expansion_rate",
    "fleet_power_cat",
    "first_war_month",
    "tech_cat",
)
MAX_SAMPLES = 48
RATE_MIN_AGE, RATE_MAX_AGE = 12, 36
#: k reported for the first evidence step (full weight). Mirrors belief.EVIDENCE_PERIOD_MONTHS.
FIRST_EVIDENCE_K = 12
#: HYP (Wave 1.5 F1): one observer must alone see this many frontier systems before the pooled border ratio is
#: evidence. A single player system bordering several observers is counted once per observer, so a pooled
#: count would let one starbase decide the ratio. Code constant, not part of any contract.
MIN_FRONTIER = 3


@dataclass(frozen=True)
class FeatureMemory:
    """Cross-tick feature state. ``samples`` are (legal_as_of mi, max visible systems)."""

    samples: tuple[tuple[int, int], ...] = ()
    first_war_mi: int | None = None
    first_war_offset: float | None = None  # observed (first_war - anchor), kept across resets
    anchor_mi: int | None = None
    last_asof_mi: int | None = None
    last_evidence_mi: int | None = None
    war_applied: bool = False  # the observed war already entered the belief
    # Observer set seen at the last evidence tick and the as-of month its continuous run began. The expansion
    # rate is never computed across a visibility gap (observer lost or regained), see ``extract``.
    vis: frozenset[int] | None = None
    run_start_mi: int | None = None
    # Observers reporting fleet_power_cat / tech_cat at the last evidence tick (Wave 1.5 rev 3.1, see
    # ``_stable_cat``). None = never seen; frozenset() = a real contact gap.
    fleet_vis: frozenset[int] | None = None
    tech_vis: frozenset[int] | None = None
    # Last counted value of each categorical (rev 3.1 value guard A1); None = nothing counted yet.
    fleet_last: int | None = None
    tech_last: int | None = None


@dataclass(frozen=True)
class FeatureVector:
    """Feature values; ``None`` = unobserved. ``first_war_*`` fields are report-only (not used by belief)."""

    border_fort_ratio: float | None = None
    expansion_rate: float | None = None
    fleet_power_cat: int | None = None
    first_war_month: float | None = None
    first_war_observed: bool = False
    first_war_new: bool = False  # True only on the evidence tick that first carries the war
    tech_cat: int | None = None
    at_war: bool | None = None  # player is the aggressor in a war now; None = unseen, defensive-only or unknown


def _c(v: int | None) -> int | None:
    """Clamp an untrusted number to the Clausewitz range so downstream arithmetic cannot overflow."""
    return None if v is None else max(-CLAUSEWITZ_INT_MAX, min(CLAUSEWITZ_INT_MAX, int(v)))


def _lower_median(values: list[int]) -> int | None:
    return statistics.median_low(values) if values else None


def _border_ratio(views: list[LegalView]) -> float | None:
    pairs = [
        (_c(v.fortified_border_starbases) or 0, _c(v.frontier_systems) or 0)
        for v in views
        if v.fortified_border_starbases is not None and v.frontier_systems is not None
        and (_c(v.frontier_systems) or 0) > 0
    ]
    if not pairs or max(n for _, n in pairs) < MIN_FRONTIER:
        return None
    return sum(f for f, _ in pairs) / sum(n for _, n in pairs)


def _rate(samples: tuple[tuple[int, int], ...], now_mi: int, now_sys: int) -> float | None:
    ages = [(now_mi - m, s) for m, s in samples if RATE_MIN_AGE <= now_mi - m <= RATE_MAX_AGE]
    if not ages:
        return None
    age, then = max(ages)
    rate = (now_sys - then) / (age / 12)
    # A negative rate is an observer-loss artefact (a smaller view set), not a real contraction: no evidence.
    return rate if rate >= 0 else None


def _at_war(views: list[LegalView]) -> bool | None:
    """True if any view sees an offensive war of the player; False only when no view sees any war; else None.

    Defensive-only wars, wars led by a managed AI and an unknown role (``player_offensive_war_count is None``)
    give None: they are no evidence about the player's own aggression (C4 v1.3, proposal 038). No fallback to
    the plain war count. UNVERIFIED (A-55): third-party war sides are as public as ``player_war_count``; M0-11
    will check it (kit step pending, T8): war screen vs save ``attackers`` / ``defenders``.
    """
    if any((_c(v.player_offensive_war_count) or 0) > 0 for v in views):
        return True
    # at_war_with_observer=False only means 'not at war with this observer', not 'at peace'.
    if any(v.at_war_with_observer is True or (_c(v.player_war_count) or 0) > 0 for v in views):
        return None
    return False if any(v.player_war_count is not None for v in views) else None


def _cat_reports(views: list[LegalView], attr: str) -> list[tuple[int, int]]:
    """Clamped (observer, value) reports of one categorical."""
    return [(v.observer_sid, _c(getattr(v, attr)) or 0) for v in views if getattr(v, attr) is not None]


def _stable_cat(
    reports: list[tuple[int, int]], prev: frozenset[int] | None, last: int | None, first_evidence: bool
) -> int | None:
    """Lower median of a categorical, or None when the reporting-set change makes the tick an artefact.

    Wave 1.5 rev 3.1 rules: ``prev`` None counts only on the first evidence of the campaign (a pre-1.5
    state.json with ``last_evidence_mi`` set is dropped once); an unchanged set counts; after a contact gap
    (``prev`` empty) one tick is dropped; on any other set change the median over ``prev`` and the current set
    counts only if it equals ``last``, the last counted value (A1), else the tick is dropped.
    """
    if not reports:
        return None
    who = frozenset(sid for sid, _ in reports)
    if prev is None:
        return _lower_median([x for _, x in reports]) if first_evidence else None
    if who == prev:
        return _lower_median([x for _, x in reports])
    shared = [x for sid, x in reports if sid in prev]
    med = _lower_median(shared)  # empty prev (after a gap) or empty intersection -> None
    return med if med is not None and med == last else None


def _war_seen(views: list[LegalView]) -> bool:
    return any((_c(v.player_war_count) or 0) > 0 or v.at_war_with_observer is True for v in views)


def reset_session(memory: FeatureMemory, obs_mi: int, regressed: bool = True) -> FeatureMemory:
    """Drop memory after ``obs_mi`` and reset the consumed-save and censor anchors (v2 rule 2).

    Consumed-save anchors are capped at ``obs_mi`` (never dropped), so a restart at the same save does not
    recount its evidence; ``regressed`` is kept for API compatibility (both cases behave the same).
    An observed first-war offset survives; only the censor anchor of an unobserved war resets.
    """
    war = memory.first_war_mi if memory.first_war_mi is not None and memory.first_war_mi <= obs_mi else None
    last_ev = None if memory.last_evidence_mi is None else min(memory.last_evidence_mi, obs_mi)
    last_asof = None if memory.last_asof_mi is None else min(memory.last_asof_mi, obs_mi)
    return FeatureMemory(
        samples=tuple(s for s in memory.samples if s[0] <= obs_mi),
        first_war_mi=war,
        first_war_offset=memory.first_war_offset if war is not None else None,
        war_applied=memory.war_applied and war is not None,
        anchor_mi=None,
        vis=memory.vis,
        fleet_vis=memory.fleet_vis,
        tech_vis=memory.tech_vis,
        fleet_last=memory.fleet_last,
        tech_last=memory.tech_last,
        run_start_mi=None if memory.run_start_mi is None else min(memory.run_start_mi, obs_mi),
        last_asof_mi=last_asof,
        last_evidence_mi=last_ev,
    )


def extract(memory: FeatureMemory, obs: Observation) -> tuple[FeatureMemory, FeatureVector, bool, int]:
    """Aggregate in-contact views. Returns (memory, features, new_evidence, k months since last evidence)."""
    obs_mi = mi_of(obs.game_date)
    views = [v for v in obs.player_legal if v.in_contact]
    if not views:
        empty = frozenset[int]()
        # Rule 1: a never-seen categorical set stays None, so log ticks before the first save are no gap.
        gone = replace(
            memory,
            vis=empty,
            fleet_vis=None if memory.fleet_vis is None else empty,
            tech_vis=None if memory.tech_vis is None else empty,
        )
        return (memory if gone == memory else gone), FeatureVector(), False, 0
    anchor = memory.anchor_mi if memory.anchor_mi is not None else obs_mi
    asof = mi_of(obs.legal_as_of) if obs.legal_as_of is not None else None
    new_ev = asof is not None and (memory.last_asof_mi is None or asof > memory.last_asof_mi)
    systems = [_c(v.systems_owned) or 0 for v in views if v.systems_owned is not None]
    now_sys = max(systems) if systems else None
    samples = memory.samples
    rate = None
    # A None systems_owned report breaks the continuous run exactly like a lost observer.
    vis = frozenset(v.observer_sid for v in views if v.systems_owned is not None)
    run_start = memory.run_start_mi
    if new_ev and asof is not None and (memory.vis != vis or run_start is None):
        run_start = asof  # observer set changed: a new continuous visibility run starts here
    if asof is not None and now_sys is not None:
        rate = _rate(tuple(s for s in samples if run_start is None or s[0] >= run_start), asof, now_sys)
        if new_ev:
            samples = (*(s for s in samples if s[0] != asof), (asof, now_sys))[-MAX_SAMPLES:]
    first_war, offset = memory.first_war_mi, memory.first_war_offset
    if first_war is None and _war_seen(views):
        first_war = asof if asof is not None else obs_mi
        offset = float(first_war - anchor)
    observed = first_war is not None
    first_ev = memory.last_evidence_mi is None
    fleet = _cat_reports(views, "fleet_power_cat")
    tech = _cat_reports(views, "tech_cat")
    fleet_cat = _stable_cat(fleet, memory.fleet_vis, memory.fleet_last, first_ev)
    tech_cat = _stable_cat(tech, memory.tech_vis, memory.tech_last, first_ev)
    month = offset if offset is not None else float((first_war if first_war is not None else obs_mi) - anchor)
    fv = FeatureVector(
        border_fort_ratio=_border_ratio(views),
        expansion_rate=rate,
        fleet_power_cat=fleet_cat,
        first_war_month=max(0.0, month),
        first_war_observed=observed,
        first_war_new=observed and new_ev and not memory.war_applied,
        tech_cat=tech_cat,
        at_war=_at_war(views),
    )
    last_ev = memory.last_evidence_mi
    k = FIRST_EVIDENCE_K if last_ev is None else max(0, obs_mi - last_ev)
    new_mem = replace(
        memory,
        samples=samples,
        first_war_mi=first_war,
        first_war_offset=offset,
        war_applied=memory.war_applied or (observed and new_ev),
        anchor_mi=anchor,
        vis=vis if new_ev else memory.vis,
        fleet_vis=frozenset(sid for sid, _ in fleet) if new_ev else memory.fleet_vis,
        tech_vis=frozenset(sid for sid, _ in tech) if new_ev else memory.tech_vis,
        fleet_last=fleet_cat if new_ev and fleet_cat is not None else memory.fleet_last,
        tech_last=tech_cat if new_ev and tech_cat is not None else memory.tech_last,
        run_start_mi=run_start,
        last_asof_mi=asof if new_ev else memory.last_asof_mi,
        last_evidence_mi=obs_mi if new_ev else last_ev,
    )
    return new_mem, fv, new_ev, (k if new_ev else 0)
