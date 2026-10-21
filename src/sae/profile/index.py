"""Campaign fold, run scan and update planning (C10 section 1 rules 1-5; proposal 025 sections 3.1-3.5)."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from sae.bus.reader import iter_events
from sae.contracts.c5_bus import BUS_FILE, ObservationEvent, RunStartedEvent
from sae.contracts.c6_adapter import AdapterMode
from sae.contracts.c10_profile import CampaignEvent
from sae.core.dates import mi_of
from sae.profile import store

log = logging.getLogger(__name__)

__all__ = ["CampaignState", "Segment", "campaign_state", "new_campaign_id", "plan_update", "scan_runs"]

Status = Literal["active", "closed", "excluded", "deleted"]
Split = Literal["tune", "sealed"]
SPLIT_GAP_MONTHS = 60  # 5 game years (C10 section 1 rule 3)
FREE_TUNE_LABELS = 5  # labels #1-#5 are tune (TEAM_PLAN V-1)
SEALED_EVERY = 4  # #6, #10, #14 ...
RETIRE_NEWER_SEALED = 2
RETIRE_QUERIES = 10
_LIVE_MODES = frozenset({AdapterMode.LIVE, AdapterMode.RELEASE})
_SYNTH_MODES = frozenset({AdapterMode.SYNTH, AdapterMode.REPLAY})


@dataclass(frozen=True)
class CampaignState:
    """One campaign after folding the index."""

    campaign_id: str
    folder_key: str
    start_date: str
    last_date: str
    run_ids: tuple[str, ...]
    game_versions: tuple[str, ...]
    policy_sha256: tuple[str, ...]
    status: Status
    label_order: int | None
    split: Split | None
    first_seq: int


@dataclass(frozen=True)
class Segment:
    """Consecutive observations of one run that share one non-null save folder key."""

    run_id: str
    t_wall: datetime
    folder_key: str
    first_date: str
    last_date: str
    game_versions: tuple[str, ...]
    policy_sha256: tuple[str, ...]


def new_campaign_id(folder_key: str, run_id: str) -> str:
    """``c-`` + sha256(folder_key + "|" + run_id)[:16] (C10 section 1 rule 2)."""
    return "c-" + hashlib.sha256((folder_key + "|" + run_id).encode()).hexdigest()[:16]


def _union(a: Iterable[str], b: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys([*a, *b]))


def _apply(states: dict[str, CampaignState], ev: CampaignEvent) -> None:
    """Fold one row into ``states`` (replacing frozen values; ``states`` is the caller's private dict)."""
    cur = states.get(ev.campaign_id)
    if ev.event == "seen":
        assert ev.folder_key and ev.start_date and ev.last_date  # guaranteed by CampaignEvent
        if cur is None:
            states[ev.campaign_id] = CampaignState(
                ev.campaign_id, ev.folder_key, ev.start_date, ev.last_date, ev.run_ids, ev.game_versions,
                ev.policy_sha256, "active", None, None, ev.seq,
            )  # fmt: skip
            return
        if cur.status == "deleted":
            return
        reopen = cur.status == "closed" and ev.last_date > cur.last_date
        states[ev.campaign_id] = replace(
            cur,
            start_date=min(cur.start_date, ev.start_date),
            last_date=max(cur.last_date, ev.last_date),
            run_ids=_union(cur.run_ids, ev.run_ids),
            game_versions=_union(cur.game_versions, ev.game_versions),
            policy_sha256=_union(cur.policy_sha256, ev.policy_sha256),
            status="active" if reopen else cur.status,
        )
    elif cur is None:
        log.warning("index row %d names unknown campaign %s", ev.seq, ev.campaign_id)
    elif ev.event == "closed":
        if cur.status == "active":
            states[ev.campaign_id] = replace(cur, status="closed")
    elif ev.event == "excluded":
        if cur.status != "deleted":
            states[ev.campaign_id] = replace(cur, status="excluded")
    elif ev.event == "deleted":
        states[ev.campaign_id] = replace(cur, status="deleted")
    elif ev.event == "labelled" and cur.label_order is None:
        order = 1 + sum(1 for s in states.values() if s.label_order is not None)
        states[ev.campaign_id] = replace(cur, label_order=order)


def _base_split(order: int) -> Split:
    return "sealed" if order > FREE_TUNE_LABELS and (order - 6) % SEALED_EVERY == 0 else "tune"


def _assign_splits(states: dict[str, CampaignState], sealed_queries: Mapping[str, int]) -> dict[str, CampaignState]:
    sealed_orders = [s.label_order for s in states.values() if s.label_order and _base_split(s.label_order) == "sealed"]
    out: dict[str, CampaignState] = {}
    for cid, s in states.items():
        if s.label_order is None:
            out[cid] = s
            continue
        split = _base_split(s.label_order)
        if split == "sealed":
            newer = sum(1 for o in sealed_orders if o > s.label_order)
            if newer >= RETIRE_NEWER_SEALED and sealed_queries.get(cid, 0) >= RETIRE_QUERIES:
                split = "tune"
        out[cid] = replace(s, split=split)
    return out


def campaign_state(
    events: Iterable[CampaignEvent], sealed_queries: Mapping[str, int] | None = None
) -> dict[str, CampaignState]:
    """Pure fold of the index; dict order is first-seen order."""
    states: dict[str, CampaignState] = {}
    for ev in events:
        _apply(states, ev)
    return _assign_splits(states, sealed_queries or {})


def _segments_of(
    run_id: str, t_wall: datetime, policy: tuple[str, ...], obs: Iterable[ObservationEvent]
) -> list[Segment]:
    segs: list[Segment] = []
    key: str | None = None
    first = last = ""
    versions: dict[str, None] = {}

    def flush() -> None:
        if key is not None:
            segs.append(Segment(run_id, t_wall, key, first, last, tuple(versions), policy))

    for ev in obs:
        o = ev.payload
        if o.save_folder_key is None:
            continue
        if o.save_folder_key != key:
            flush()
            key, first, last, versions = o.save_folder_key, o.game_date, o.game_date, {}
        first, last = min(first, o.game_date), max(last, o.game_date)
        if o.game_version:
            versions[o.game_version] = None
    flush()
    return segs


def scan_runs(runs_dir: Path, include_synth: bool) -> list[Segment]:
    """Segments of every indexable run under ``runs_dir`` (reads ``bus.jsonl`` only), ordered by run t_wall."""
    allowed = _LIVE_MODES | (_SYNTH_MODES if include_synth else frozenset())
    out: list[Segment] = []
    for bus in sorted(runs_dir.glob(f"*/{BUS_FILE}")):
        started: RunStartedEvent | None = None
        obs: list[ObservationEvent] = []
        for ev in iter_events(bus):
            if isinstance(ev, RunStartedEvent) and started is None:
                started = ev
            elif isinstance(ev, ObservationEvent):
                obs.append(ev)
        if started is None or started.payload.adapter_mode not in allowed:
            continue
        pol = started.payload.policy
        out.extend(_segments_of(started.run_id, started.t_wall, (pol.sha256,) if pol else (), obs))
    out.sort(key=lambda s: s.t_wall)  # stable: keeps file order inside one run
    return out


Ask = Callable[[str], Literal["new", "reload"] | None]


def _row(cid: str, event: Literal["seen", "closed"], seg: Segment | None = None) -> CampaignEvent:
    if event == "closed" or seg is None:
        return CampaignEvent(seq=0, at=store.now(), campaign_id=cid, event="closed")
    return CampaignEvent(
        seq=0, at=store.now(), campaign_id=cid, event="seen", folder_key=seg.folder_key,
        start_date=seg.first_date, last_date=seg.last_date, run_ids=(seg.run_id,),
        game_versions=seg.game_versions, policy_sha256=seg.policy_sha256,
    )  # fmt: skip


def plan_update(
    state: Mapping[str, CampaignState], segments: Iterable[Segment], ask: Ask
) -> tuple[list[CampaignEvent], list[Segment]]:
    """Rows to append and the split candidates left pending (025 section 3.2-3.4).

    ``segments`` must be ordered by run t_wall. Each emitted row is folded into a working state so later
    segments see it.
    """
    work = dict(state)
    rows: list[CampaignEvent] = []
    pending: list[Segment] = []

    def emit(ev: CampaignEvent) -> None:
        rows.append(ev)
        _apply(work, ev)

    for seg in segments:
        owner = next((c for c in work.values() if seg.run_id in c.run_ids and c.folder_key == seg.folder_key), None)
        if owner is not None:
            if owner.status != "deleted" and seg.last_date > owner.last_date:
                emit(_row(owner.campaign_id, "seen", seg))
            continue
        live = [c for c in work.values() if c.folder_key == seg.folder_key and c.status != "deleted"]
        if not live:
            emit(_row(new_campaign_id(seg.folder_key, seg.run_id), "seen", seg))
            continue
        current = live[-1]
        if mi_of(current.last_date) - mi_of(seg.first_date) > SPLIT_GAP_MONTHS:
            answer = ask(current.campaign_id)
            if answer is None:
                pending.append(seg)
            elif answer == "reload":
                emit(_row(current.campaign_id, "seen", seg))
            else:
                if current.status != "closed":
                    emit(_row(current.campaign_id, "closed"))
                emit(_row(new_campaign_id(seg.folder_key, seg.run_id), "seen", seg))
        else:
            emit(_row(current.campaign_id, "seen", seg))
    return rows, pending
