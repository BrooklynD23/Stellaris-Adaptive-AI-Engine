"""Campaign summaries from fog-legal bus lines only (C10 section 2).

Reads ``runs/<id>/bus.jsonl`` and nothing else; the god-view file is never opened or named here.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

from sae.bus.reader import iter_events
from sae.contracts.c5_bus import BUS_FILE, BeliefUpdateEvent, ObservationEvent, RunStartedEvent
from sae.contracts.c6_adapter import Observation
from sae.contracts.c10_profile import FEATURE_NAMES, CampaignSummary, SummaryFeature
from sae.contracts.common import Archetype
from sae.core.dates import mi_of
from sae.core.features import FeatureMemory, extract
from sae.profile.index import CampaignState

log = logging.getLogger(__name__)

__all__ = ["BUILDER_VERSION", "build_summary"]

BUILDER_VERSION = "0.1.0"
EARLY_YEARS = 30  # C10 section 2: first 30 game years
MIN_SPAN_YEARS = 1.0
_BELIEF_TOL = 1e-6


@dataclass
class _Kept:
    obs: Observation
    belief: dict[Archetype, float] | None = None


@dataclass(frozen=True)
class _Sample:
    mi: int
    systems: int | None
    border: float | None
    fleet: int | None
    tech: int | None
    econ: int | None
    at_war: bool | None


def _read_run(path: Path) -> tuple[RunStartedEvent, list[ObservationEvent | BeliefUpdateEvent]] | None:
    started: RunStartedEvent | None = None
    rows: list[ObservationEvent | BeliefUpdateEvent] = []
    for ev in iter_events(path):
        if isinstance(ev, RunStartedEvent) and started is None:
            started = ev
        elif isinstance(ev, ObservationEvent | BeliefUpdateEvent):
            rows.append(ev)
    return None if started is None else (started, rows)


def _kept_observations(state: CampaignState, runs_dir: Path) -> list[_Kept]:
    """Observations of the campaign in recorded order with reloads resolved (C10 section 2 'Reloads')."""
    runs: list[tuple[RunStartedEvent, list[ObservationEvent | BeliefUpdateEvent]]] = []
    for run_id in state.run_ids:
        bus = runs_dir / run_id / BUS_FILE
        if not bus.exists():
            log.warning("run %s listed by campaign %s has no %s", run_id, state.campaign_id, BUS_FILE)
            continue
        got = _read_run(bus)
        if got is not None:
            runs.append(got)
    runs.sort(key=lambda r: (r[0].t_wall, r[0].run_id))
    kept: list[_Kept] = []
    for _started, rows in runs:
        current: _Kept | None = None
        for ev in rows:
            if isinstance(ev, ObservationEvent):
                o = ev.payload
                if o.save_folder_key != state.folder_key:
                    current = None
                    continue
                if kept and o.game_date < kept[-1].obs.game_date:
                    kept = [k for k in kept if k.obs.game_date <= o.game_date]
                current = _Kept(o)
                kept.append(current)
            elif current is not None:
                post = ev.payload.posterior
                if set(post) == set(Archetype) and abs(math.fsum(post.values()) - 1.0) <= _BELIEF_TOL:
                    current.belief = dict(post)
    return kept


def _samples(kept: list[_Kept]) -> list[_Sample]:
    """One sample per game month (last wins), ordered by month."""
    memory = FeatureMemory()
    by_month: dict[int, _Sample] = {}
    for k in kept:
        memory, fv, _new, _k = extract(memory, k.obs)
        views = [v for v in k.obs.player_legal if v.in_contact]
        econ = [v.economy_cat for v in views if v.economy_cat is not None]
        systems = [v.systems_owned for v in views if v.systems_owned is not None]
        mi = mi_of(k.obs.game_date)
        by_month[mi] = _Sample(
            mi, max(systems) if systems else None, fv.border_fort_ratio, fv.fleet_power_cat, fv.tech_cat,
            statistics.median_low(econ) if econ else None, fv.at_war,
        )  # fmt: skip
    return [by_month[m] for m in sorted(by_month)]


def _mean(values: list[float]) -> SummaryFeature:
    return SummaryFeature(value=math.fsum(values) / len(values) if values else None, n_obs=len(values))


def _rate(samples: list[_Sample]) -> SummaryFeature:
    pts = [(s.mi, s.systems) for s in samples if s.systems is not None]
    first, last = (pts[0], pts[-1]) if pts else ((0, 0), (0, 0))
    if len(pts) < 2:
        return SummaryFeature(value=None, n_obs=len(pts))
    span = (last[0] - first[0]) / 12
    if span < MIN_SPAN_YEARS:
        return SummaryFeature(value=None, n_obs=len(pts))
    return SummaryFeature(value=(last[1] - first[1]) / span, n_obs=len(pts))


def _features(samples: list[_Sample], start_mi: int) -> dict[str, SummaryFeature]:
    cut = EARLY_YEARS * 12
    early = [s for s in samples if s.mi - start_mi <= cut]
    late = [s for s in samples if s.mi - start_mi > cut]
    war = [s for s in samples if s.at_war is not None]
    first_war = next((s for s in war if s.at_war), None)
    return {
        "expansion_rate_early": _rate(early),
        "expansion_rate_late": _rate(late),
        "border_fort_ratio": _mean([s.border for s in samples if s.border is not None]),
        "first_war_year": SummaryFeature(
            value=None if first_war is None else (first_war.mi - start_mi) / 12, n_obs=len(war)
        ),
        "war_share": SummaryFeature(
            value=sum(1 for s in war if s.at_war) / len(war) if war else None, n_obs=len(war)
        ),
        "fleet_power_cat_mean": _mean([float(s.fleet) for s in samples if s.fleet is not None]),
        "tech_cat_mean": _mean([float(s.tech) for s in samples if s.tech is not None]),
        "economy_cat_mean": _mean([float(s.econ) for s in samples if s.econ is not None]),
    }


def build_summary(state: CampaignState, runs_dir: Path) -> CampaignSummary | None:
    """Summarise one campaign from its runs' ``bus.jsonl``; ``None`` when no observation survives."""
    kept = _kept_observations(state, runs_dir)
    samples = _samples(kept)
    if not samples:
        return None
    belief = next((k.belief for k in reversed(kept) if k.belief is not None), None)
    feats = _features(samples, mi_of(state.start_date))
    assert set(feats) == set(FEATURE_NAMES)
    return CampaignSummary(
        campaign_id=state.campaign_id,
        builder_version=BUILDER_VERSION,
        game_versions=state.game_versions,
        policy_sha256=state.policy_sha256,
        start_date=state.start_date,
        last_date=state.last_date,
        features=feats,
        final_belief=belief,
    )
