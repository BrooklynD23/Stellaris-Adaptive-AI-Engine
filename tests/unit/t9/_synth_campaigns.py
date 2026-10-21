"""Seeded labelled multi-campaign fixture (X4): 6 labelled campaigns (the 6th is sealed) plus one unlabelled.

Months are sampled from the built-in likelihood tables of the label archetype, with the player's feature means
shifted (a turtle's border ratio is 0.6, not 0.85) so a fitted policy has something to learn. Importable from
other test dirs as ``from t9._synth_campaigns import build_campaigns`` (``tests/`` has no ``__init__.py``).
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal

from sae.contracts.c10_profile import CampaignEvent, CampaignLabel, LabelSegment
from sae.contracts.common import Archetype
from sae.core.belief import LikelihoodModel
from sae.profile import store
from sae.profile.index import new_campaign_id

from ._fixtures import POLICY_SHA, Month, T, live_key, make_run, run_id, synth_key

__all__ = ["BuiltCampaign", "build_campaigns"]

MONTHS = 120  # ~10 game years
SWITCH_MONTH = 60
START_YEAR = 2200
GAME_VERSION = "v4.4.4"
_CYCLE = (Archetype.TURTLE, Archetype.TECH_RUSH, Archetype.EARLY_AGGRESSION, Archetype.BALANCED)
# Player-specific shift of the border mean away from the built-in table (turtle 0.85 -> 0.6).
_BORDER_SHIFT = {
    Archetype.TURTLE: -0.25, Archetype.TECH_RUSH: 0.1, Archetype.EARLY_AGGRESSION: -0.1, Archetype.BALANCED: 0.0,
}  # fmt: skip
_EXPANSION_SCALE = 0.8
_FRONTIER = 10


@dataclass(frozen=True)
class BuiltCampaign:
    """One campaign written by :func:`build_campaigns`."""

    campaign_id: str
    run_id: str
    folder_key: str
    segments: tuple[tuple[int, Archetype], ...]  # (first month index, archetype)
    labelled: bool


def _date(m: int) -> str:
    return f"{START_YEAR + m // 12}.{m % 12 + 1:02d}.01"


def _archetype_at(segments: tuple[tuple[int, Archetype], ...], m: int) -> Archetype:
    return [a for first, a in segments if first <= m][-1]


def _months(rng: random.Random, segments: tuple[tuple[int, Archetype], ...], shift: bool) -> list[Month]:
    lk = LikelihoodModel()
    out: list[Month] = []
    systems = 5.0
    for m in range(MONTHS):
        a = _archetype_at(segments, m)
        mu_b, sd_b = lk.border[a]
        mu_e, sd_e = lk.expansion[a]
        if shift:
            mu_b += _BORDER_SHIFT[a]
            mu_e *= _EXPANSION_SCALE
        border = min(1.0, max(0.0, rng.gauss(mu_b, sd_b)))
        systems = max(1.0, systems + max(0.0, rng.gauss(mu_e, sd_e)) / 12)
        fleet = rng.choices(range(-2, 3), weights=lk.fleet[a])[0]
        tech = rng.choices(range(-2, 3), weights=lk.tech[a])[0]
        war = rng.random() < lk.war_now[a]
        cats = (fleet, tech, 0, round(border * _FRONTIER), _FRONTIER)
        out.append((_date(m), int(systems), war, cats, True))
    return out


def _schedules(n_total: int) -> list[tuple[tuple[int, Archetype], ...]]:
    """Archetype schedule per campaign; campaign 1 changes archetype mid-way."""
    out: list[tuple[tuple[int, Archetype], ...]] = []
    for i in range(n_total):
        base = _CYCLE[i % len(_CYCLE)]
        if i == 1:
            out.append(((0, base), (SWITCH_MONTH, _CYCLE[(i + 2) % len(_CYCLE)])))
        else:
            out.append(((0, base),))
    return out


def build_campaigns(
    sae_dir: Path,
    runs_dir: Path,
    mode: Literal["synth", "live"] = "synth",
    n_labelled: int = 6,
    shift: bool = True,
    *,
    seed: int = 0,
    player_key: str = "default",
) -> list[BuiltCampaign]:
    """Write ``n_labelled`` closed, labelled campaigns plus one closed, unlabelled campaign.

    Runs go to ``runs_dir/<run_id>/bus.jsonl``; index rows (seen, closed, labelled) go through
    ``sae.profile.store`` and label files to ``<sae_dir>/profile/<player_key>/labels/``. Labels are appended in
    campaign order, so labelled campaign #6 is sealed by C10 rule 5 and #1-#5 are tune.
    """
    rng = random.Random(seed)
    schedules = _schedules(n_labelled + 1)
    built: list[BuiltCampaign] = []
    rows: list[CampaignEvent] = []
    clock = T(2026, 10, 1)
    for i, segments in enumerate(schedules):
        labelled = i < n_labelled
        rid = run_id(i + 1, mode)
        fkey = live_key(i + 1) if mode == "live" else synth_key(i + 1)
        make_run(runs_dir, rid, mode, clock + timedelta(hours=i), fkey, _months(rng, segments, shift))
        cid = new_campaign_id(fkey, rid)
        built.append(BuiltCampaign(cid, rid, fkey, segments, labelled))
        rows.append(CampaignEvent(
            seq=0, at=clock, campaign_id=cid, event="seen", folder_key=fkey, start_date=_date(0),
            last_date=_date(MONTHS - 1), run_ids=(rid,), game_versions=(GAME_VERSION,), policy_sha256=(POLICY_SHA,),
        ))  # fmt: skip
        rows.append(CampaignEvent(seq=0, at=clock, campaign_id=cid, event="closed"))
        if labelled:
            label = CampaignLabel(
                campaign_id=cid, source="human", labelled_at=clock, blind=True,
                segments=tuple(LabelSegment(from_date=_date(first), archetype=a) for first, a in segments),
            )  # fmt: skip
            data = store.canonical_bytes(label)
            store.write_atomic(store.profile_dir(sae_dir, player_key) / "labels" / f"{cid}.json", data)
            rows.append(CampaignEvent(
                seq=0, at=clock, campaign_id=cid, event="labelled", label_sha256=hashlib.sha256(data).hexdigest()
            ))  # fmt: skip
    store.append_events(sae_dir / store.INDEX_FILE, rows)
    return built
