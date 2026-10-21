"""Pure bus-event to Rerun-record mapping (no rerun import). Tiers per C7: T0 nothing, T1 scalars, T2 + text + god."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sae.bus.reader import Event
from sae.contracts.c5_bus import (
    BeliefUpdateEvent,
    ControlEvent,
    HealthEvent,
    IntentAckEvent,
    IntentEmittedEvent,
    IntentRejectedEvent,
    ObservationEvent,
    ObservationGodEvent,
    OutcomeEvent,
    RunStartedEvent,
    StrategySelectedEvent,
)
from sae.contracts.c7_governor import HealthLevel, TelemetryTier

__all__ = ["Mapper", "Record", "effective_tier", "game_month"]

Level = Literal["INFO", "WARN", "ERROR"]
_TIER_RANK = {TelemetryTier.T0: 0, TelemetryTier.T1: 1, TelemetryTier.T2: 2}
_LEVEL_CAP = {HealthLevel.H0: TelemetryTier.T2, HealthLevel.H1: TelemetryTier.T1}  # H2/H3/S -> T0
_LEVEL_INDEX = {HealthLevel.H0: 0, HealthLevel.H1: 1, HealthLevel.H2: 2, HealthLevel.H3: 3, HealthLevel.S: 4}
_MANAGED_FIELDS = ("fleet_power", "energy", "minerals", "alloys", "alloys_income", "systems_owned", "wars",
                   "applied_econ", "applied_mil")
_ACK_COUNTER = {"APPLIED": "applied", "LOST": "lost_superseded", "SUPERSEDED": "lost_superseded"}


def game_month(date: str | None) -> int | None:
    """Months since 2200.01 for ``YYYY.MM.DD``; ``None`` for a null or malformed date."""
    if date is None:
        return None
    try:
        y, m, _ = date.split(".")
        return (int(y) - 2200) * 12 + int(m) - 1
    except ValueError:
        return None


def effective_tier(config: TelemetryTier, level: HealthLevel) -> TelemetryTier:
    """Live cap: min(config tier, cap for the latest health level)."""
    cap = _LEVEL_CAP.get(level, TelemetryTier.T0)
    return config if _TIER_RANK[config] <= _TIER_RANK[cap] else cap


_SPM_KEYS = frozenset({"spm", "spm_med6", "spm_med24"})
_PARSE_KEYS = frozenset({"parse_rss_peak_bytes", "parse_cpu_s", "parse_wall_s", "saves_skipped", "workers_killed"})


def _metric_entity(key: str) -> str:
    """Governor panel: spm and last-parse cost get their own entities; other metrics stay under health."""
    if key in _SPM_KEYS:
        return f"governor/spm/{key}"
    if key in _PARSE_KEYS:
        return f"governor/parse/{key}"
    return f"health/metric/{key}"


def _is_deferral(rule: str, detail: str | None) -> bool:
    """T4 audits deferred batches as ``intent_rejected`` rule ``rate_limit``, detail ``deferred: ...``."""
    return rule == "deferred" or (detail or "").startswith("deferred:")


@dataclass(frozen=True)
class Record:
    """One thing to log: a scalar (``value``) or a text line (``text``, ``level``) at three timelines."""

    entity: str
    kind: Literal["scalar", "text"]
    seq: int
    t_wall: datetime
    game_month: int | None
    value: float = 0.0
    text: str = ""
    level: Level = "INFO"


@dataclass
class Mapper:
    """Stateful mapper (intent counters, latest health level); ``map`` applies the tier filter."""

    tier: TelemetryTier
    live: bool = False
    level: HealthLevel = HealthLevel.H0
    counters: dict[str, int] = field(default_factory=lambda: {
        "emitted": 0, "applied": 0, "rejected": 0, "lost_superseded": 0, "refused_multiplayer": 0,
        "deferred": 0})

    def map(self, ev: Event) -> list[Record]:
        """Records for ``ev`` allowed at the current effective tier."""
        if isinstance(ev, HealthEvent):
            self.level = ev.payload.level
        tier = effective_tier(self.tier, self.level) if self.live else self.tier
        rank = _TIER_RANK[tier]
        if rank == 0:
            return []
        recs = self._all(ev)
        god = isinstance(ev, ObservationGodEvent)
        return [r for r in recs if (r.kind == "scalar" or rank >= 2) and (not god or rank >= 2)]

    def _count(self, name: str, ev: Event) -> Record:
        self.counters[name] += 1
        return _s(ev, f"intent/{name}", float(self.counters[name]))

    def _all(self, ev: Event) -> list[Record]:
        out: list[Record] = []
        if isinstance(ev, RunStartedEvent):
            out.append(_t(ev, "status/header", f"run {ev.run_id} engine {ev.payload.engine_version} "
                          f"preset {ev.payload.preset} adapter {ev.payload.adapter_mode}"))
        elif isinstance(ev, ObservationEvent):
            for ms in ev.payload.managed:
                for f in _MANAGED_FIELDS:
                    v = getattr(ms, f)
                    if v is not None:
                        out.append(_s(ev, f"legal/{ms.sid}/{f}", float(v)))
        elif isinstance(ev, BeliefUpdateEvent):
            bp = ev.payload
            out += [_s(ev, f"belief/posterior/{a}", v) for a, v in bp.posterior.items()]
            out.append(_s(ev, "belief/entropy", bp.entropy))
            if bp.change_score is not None:  # diagnostic only, never acted on (C5 v1.1)
                out.append(_s(ev, "belief/change_score_diagnostic", bp.change_score))
            top = ", ".join(f"{e.feature}->{e.archetype}:{e.llr:+.2f}" for e in bp.top_evidence)
            out.append(_t(ev, "audit/belief", f"{ev.decision_id}: {top or 'no evidence'}"))
        elif isinstance(ev, StrategySelectedEvent):
            sp = ev.payload
            if sp.confidence is not None:
                out.append(_s(ev, "belief/confidence", sp.confidence))
            out.append(_t(ev, "strategy/selected", f"{ev.decision_id}: {sp.strategy} ({sp.reason}) "
                          f"top={sp.top_archetype} until={sp.commit_until} switched={sp.switched}"))
        elif isinstance(ev, IntentEmittedEvent):
            rc = ev.payload.receipt
            out.append(self._count("emitted", ev))
            line = f"{ev.decision_id}: batch seq {rc.seq} ok={rc.ok} intents={len(ev.payload.batch.intents)}"
            if rc.error == "multiplayer":
                out.append(self._count("refused_multiplayer", ev))
                out.append(_t(ev, "alert/multiplayer", "MULTIPLAYER: writes refused", "ERROR"))
            elif rc.error == "ironman":  # C5 v1.2, proposal 017
                out.append(_t(ev, "alert/ironman", "IRONMAN: observe only, writes refused", "ERROR"))
            out.append(_t(ev, "audit/intent", line + (f" error={rc.error}" if rc.error else "")))
        elif isinstance(ev, IntentRejectedEvent) and _is_deferral(str(ev.payload.rule), ev.payload.detail):
            out.append(self._count("deferred", ev))  # proposed but not emitted yet (one-per-month, no m)
            out.append(_t(ev, "audit/deferred", f"{ev.decision_id}: {ev.payload.intent.kind} {ev.payload.detail}",
                          "WARN"))
        elif isinstance(ev, IntentRejectedEvent):
            out.append(self._count("rejected", ev))
            out.append(_t(ev, "audit/intent", f"{ev.decision_id}: rejected {ev.payload.intent.kind} "
                          f"rule={ev.payload.rule}: {ev.payload.detail}", "WARN"))
        elif isinstance(ev, IntentAckEvent):
            name = _ACK_COUNTER.get(ev.payload.status.value)
            if name:
                out.append(self._count(name, ev))
            if ev.payload.ack_lag_months is not None:
                out.append(_s(ev, "intent/ack_lag_months", float(ev.payload.ack_lag_months)))
            out.append(_t(ev, "audit/ack", f"seq {ev.payload.seq} country {ev.payload.country} "
                          f"{ev.payload.status} {ev.payload.code}"))
        elif isinstance(ev, OutcomeEvent):
            out.append(_s(ev, "outcome/reward", ev.payload.reward))
            out += [_s(ev, f"outcome/metrics/{k}", v) for k, v in ev.payload.metrics.items()]
            out.append(_t(ev, "strategy/outcome", f"{ev.payload.strategy} reward {ev.payload.reward:+.2f}"))
        elif isinstance(ev, ControlEvent):
            r = ev.payload.result
            cmd = ev.payload.command.command if ev.payload.command else None
            out.append(_t(ev, "control/log", f"{cmd} accepted={r.accepted} error={r.error} {r.detail or ''}".strip(),
                          "INFO" if r.accepted else "WARN"))
        elif isinstance(ev, HealthEvent):
            hp = ev.payload
            out.append(_s(ev, "health/level_index", float(_LEVEL_INDEX[hp.level])))
            out += [_s(ev, _metric_entity(k), v) for k, v in hp.metrics.items()]
            detail = f" {hp.detail}" if hp.detail else ""
            out.append(_t(ev, f"health/{hp.kind}", f"{hp.component} {hp.state} {hp.level}{detail}",
                          "INFO" if str(hp.kind) in ("monthly", "level_change", "state_change") else "WARN"))
            if str(hp.kind) == "ini_write_failed" and "multiplayer" in (hp.detail or ""):
                out.append(_t(ev, "alert/multiplayer", "MULTIPLAYER: writes refused", "ERROR"))
            elif str(hp.kind) == "ironman_observe_only":  # C5 v1.2, proposal 017
                out.append(_t(ev, "alert/ironman", f"IRONMAN: observe only{detail}", "ERROR"))
            elif str(hp.kind) == "ini_neutralised":
                out.append(_t(ev, "alert/ini", f"sae_in.ini neutralised:{detail}", "WARN"))
        elif isinstance(ev, ObservationGodEvent):
            gp = ev.payload
            if gp.player is not None:
                out += [_s(ev, f"god/{k}", float(v)) for k, v in gp.player.model_dump().items()]
            out += [_s(ev, f"god/plyr/{k}", v) for k, v in gp.plyr.items()]
        return out


def _s(ev: Event, entity: str, value: float) -> Record:
    return Record(entity, "scalar", ev.seq, ev.t_wall, game_month(ev.game_date), value=value)


def _t(ev: Event, entity: str, text: str, level: Level = "INFO") -> Record:
    return Record(entity, "text", ev.seq, ev.t_wall, game_month(ev.game_date), text=text, level=level)
