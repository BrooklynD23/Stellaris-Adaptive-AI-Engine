"""Labelled-campaign loader and sealed-query ledger (proposal 029 sections 3 and 3b; X4 plan 1.2).

One loader for ``sae tune`` and ``sae gate``. The ledger counts, per sealed campaign, the Stage 2 registry rows
whose GateReport names a ``sealed_noninferior:<campaign_id>`` check; the counts feed ``campaign_state`` (sealed
retirement, C10 section 1 rule 5).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from sae.bus.reader import iter_events
from sae.contracts.c5_bus import BUS_FILE, RunStartedEvent
from sae.contracts.c6_adapter import AdapterMode, Observation
from sae.contracts.c10_profile import CampaignLabel, GateReport, RegistryRow
from sae.profile import store, summary
from sae.profile.index import CampaignState, campaign_state

log = logging.getLogger(__name__)

__all__ = [
    "SEALED_CHECK_PREFIX",
    "LabelledCampaign",
    "SealedQueries",
    "labelled_campaigns",
    "sealed_query_counts",
]

SEALED_CHECK_PREFIX = "sealed_noninferior:"
_STAGE2 = frozenset({"stage2_pass", "stage2_fail"})
_LIVE_MODES = frozenset({AdapterMode.LIVE, AdapterMode.RELEASE})


@dataclass(frozen=True)
class SealedQueries:
    """Sealed-query ledger: named counts plus rows whose evidence could not be read.

    An unreadable Stage 2 evidence file counts against **every** sealed campaign (029 section 3), so the
    caller applies ``unreadable`` to all sealed ids via :meth:`counts_for`.
    """

    per_campaign: dict[str, int]
    unreadable: int = 0

    def counts_for(self, sealed_ids: Iterable[str]) -> dict[str, int]:
        """Query count per id in ``sealed_ids`` (named rows plus the unreadable sentinel)."""
        return {cid: self.per_campaign.get(cid, 0) + self.unreadable for cid in sealed_ids}


@dataclass(frozen=True)
class LabelledCampaign:
    """A closed, labelled campaign with its fog-legal observations (the frozen B-C seam)."""

    state: CampaignState
    label: CampaignLabel
    observations: list[Observation]


def _sealed_names(evidence: Path) -> set[str] | None:
    """Campaign ids named by ``sealed_noninferior:`` checks of a GateReport file; ``None`` if unreadable."""
    try:
        report = GateReport.model_validate_json(evidence.read_bytes())
    except (OSError, ValidationError):
        return None
    return {c.name.removeprefix(SEALED_CHECK_PREFIX) for c in report.checks if c.name.startswith(SEALED_CHECK_PREFIX)}


def sealed_query_counts(policies_dir: Path) -> SealedQueries:
    """Count sealed queries from ``<policies_dir>/registry.jsonl`` and ``<policies_dir>/evidence/*.json``.

    Invalid registry lines are skipped; an unreadable registry file counts as one unreadable row.
    """
    registry = policies_dir / "registry.jsonl"
    per: dict[str, int] = {}
    unreadable = 0
    try:
        text = registry.read_text(encoding="utf-8") if registry.exists() else ""
    except (OSError, UnicodeDecodeError):
        log.warning("registry unreadable; counting one unreadable sealed query")
        return SealedQueries({}, 1)
    for line in text.splitlines():
        try:
            row = RegistryRow.model_validate_json(line)
        except ValueError:
            continue
        if row.action not in _STAGE2 or row.evidence_sha256 is None:
            continue
        names = _sealed_names(policies_dir / "evidence" / f"{row.evidence_sha256}.json")
        if names is None:
            unreadable += 1
            continue
        for cid in names:
            per[cid] = per.get(cid, 0) + 1
    return SealedQueries(per, unreadable)


def _read_label(path: Path) -> CampaignLabel | None:
    try:
        return CampaignLabel.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        log.warning("label file %s missing or invalid; campaign skipped", path.name)
        return None


def _is_live(state: CampaignState, runs_dir: Path) -> bool:
    """True when every readable run of the campaign is a live or release run."""
    for run_id in state.run_ids:
        bus = runs_dir / run_id / BUS_FILE
        if not bus.exists():
            continue
        for ev in iter_events(bus):
            if isinstance(ev, RunStartedEvent):
                if ev.payload.adapter_mode not in _LIVE_MODES:
                    return False
                break
    return True


def labelled_campaigns(
    sae_dir: Path,
    player_key: str,
    runs_dir: Path,
    split: Literal["tune", "sealed"],
    include_synth: bool,
) -> list[LabelledCampaign]:
    """Closed, labelled, non-excluded campaigns of ``split``, in first-seen order.

    Synth and replay campaigns are returned only with ``include_synth``. The split is computed with the
    sealed-query ledger (a heavily queried sealed campaign retires to tune). A campaign whose label file is
    missing or invalid is skipped with a warning.

    Raises:
        store.IndexCorrupt: ``campaigns.jsonl`` has a seq gap or repeat.
    """
    events, invalid, gap = store.read_index(sae_dir / store.INDEX_FILE)
    if gap:
        raise store.IndexCorrupt("campaigns.jsonl corrupted; rename it and re-run update")
    if invalid:
        log.warning("campaigns.jsonl: %d invalid line(s) skipped", invalid)
    queries = sealed_query_counts(sae_dir / "policies")
    states = campaign_state(events)
    sealed = [cid for cid, s in states.items() if s.split == "sealed"]
    states = campaign_state(events, queries.counts_for(sealed))
    labels = store.profile_dir(sae_dir, player_key) / "labels"
    out: list[LabelledCampaign] = []
    for s in states.values():
        if s.status != "closed" or s.label_order is None or s.split != split:
            continue
        label = _read_label(labels / f"{s.campaign_id}.json")
        if label is None or not (include_synth or _is_live(s, runs_dir)):
            continue
        out.append(LabelledCampaign(s, label, [k.obs for k in summary.kept_observations(s, runs_dir)]))
    return out
