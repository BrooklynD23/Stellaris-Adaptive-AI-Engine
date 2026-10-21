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
    at_war: bool | None = None  # player at war now (public war count / war with an observer); None = unseen


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
    if not pairs:
        return None
    return sum(f for f, _ in pairs) / sum(n for _, n in pairs)


def _rate(samples: tuple[tuple[int, int], ...], now_mi: int, now_sys: int) -> float | None:
    ages = [(now_mi - m, s) for m, s in samples if RATE_MIN_AGE <= now_mi - m <= RATE_MAX_AGE]
    if not ages:
        return None
    age, then = max(ages)
    return (now_sys - then) / (age / 12)


def _at_war(views: list[LegalView]) -> bool | None:
    """Current war state from in-contact views; None when no view carries war information."""
    # at_war_with_observer=False only means 'not at war with this observer', not 'at peace'.
    known = [v for v in views if v.player_war_count is not None or v.at_war_with_observer is True]
    return _war_seen(known) if known else None


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
        last_asof_mi=last_asof,
        last_evidence_mi=last_ev,
    )


def extract(memory: FeatureMemory, obs: Observation) -> tuple[FeatureMemory, FeatureVector, bool, int]:
    """Aggregate in-contact views. Returns (memory, features, new_evidence, k months since last evidence)."""
    obs_mi = mi_of(obs.game_date)
    views = [v for v in obs.player_legal if v.in_contact]
    if not views:
        return memory, FeatureVector(), False, 0
    anchor = memory.anchor_mi if memory.anchor_mi is not None else obs_mi
    asof = mi_of(obs.legal_as_of) if obs.legal_as_of is not None else None
    new_ev = asof is not None and (memory.last_asof_mi is None or asof > memory.last_asof_mi)
    systems = [_c(v.systems_owned) or 0 for v in views if v.systems_owned is not None]
    now_sys = max(systems) if systems else None
    samples = memory.samples
    rate = None
    if asof is not None and now_sys is not None:
        rate = _rate(samples, asof, now_sys)
        if new_ev:
            samples = (*(s for s in samples if s[0] != asof), (asof, now_sys))[-MAX_SAMPLES:]
    first_war, offset = memory.first_war_mi, memory.first_war_offset
    if first_war is None and _war_seen(views):
        first_war = asof if asof is not None else obs_mi
        offset = float(first_war - anchor)
    observed = first_war is not None
    month = offset if offset is not None else float((first_war if first_war is not None else obs_mi) - anchor)
    fv = FeatureVector(
        border_fort_ratio=_border_ratio(views),
        expansion_rate=rate,
        fleet_power_cat=_lower_median([_c(v.fleet_power_cat) or 0 for v in views if v.fleet_power_cat is not None]),
        first_war_month=max(0.0, month),
        first_war_observed=observed,
        first_war_new=observed and new_ev and not memory.war_applied,
        tech_cat=_lower_median([_c(v.tech_cat) or 0 for v in views if v.tech_cat is not None]),
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
        last_asof_mi=asof if new_ev else memory.last_asof_mi,
        last_evidence_mi=obs_mi if new_ev else last_ev,
    )
    return new_mem, fv, new_ev, (k if new_ev else 0)
