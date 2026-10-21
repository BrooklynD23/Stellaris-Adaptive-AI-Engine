"""Pure mapping: per event type, tier and level cap, diagnostic label, multiplayer, unknown health kind."""

from __future__ import annotations

import json
import re

from hypothesis import given
from hypothesis import strategies as st

from sae.contracts.c5_bus import BUS_EVENT_ADAPTER, GOD_BUS_EVENT_ADAPTER, HealthEvent, HealthKind
from sae.contracts.c7_governor import HealthLevel, TelemetryTier
from sae.telemetry.mapping import Mapper, Record, effective_tier, game_month

from ._fixtures import _DOC

T0, T1, T2 = TelemetryTier.T0, TelemetryTier.T1, TelemetryTier.T2


def doc_lines() -> list[str]:
    text = _DOC.read_text(encoding="utf-8")
    return [ln for b in re.findall(r"```jsonl model=BusEvent\n(.*?)```", text, re.S) for ln in b.splitlines()]


def by_type(name: str, n: int = 0) -> str:
    return [ln for ln in doc_lines() if f'"type":"{name}"' in ln][n]


def run(tier: TelemetryTier, line: str, **kw: object) -> list[Record]:
    return Mapper(tier, **kw).map(BUS_EVENT_ADAPTER.validate_json(line))  # type: ignore[arg-type]


def ents(recs: list[Record], kind: str | None = None) -> list[str]:
    return [r.entity for r in recs if kind is None or r.kind == kind]


def test_game_month() -> None:
    assert game_month("2200.01.01") == 0
    assert game_month("2231.04.01") == 31 * 12 + 3
    assert game_month(None) is None
    assert game_month("junk") is None


@given(st.integers(2200, 2600), st.integers(1, 12), st.integers(1, 30))
def test_game_month_property(y: int, m: int, d: int) -> None:
    assert game_month(f"{y}.{m:02d}.{d:02d}") == (y - 2200) * 12 + m - 1


def test_t0_nothing_t1_scalars_only_t2_text() -> None:
    line = by_type("belief_update")
    assert run(T0, line) == []
    t1 = run(T1, line)
    assert t1 and all(r.kind == "scalar" for r in t1)
    assert any(r.kind == "text" for r in run(T2, line))


def test_each_event_type_maps() -> None:
    for name in ("run_started", "observation", "belief_update", "strategy_selected", "intent_emitted",
                 "intent_rejected", "intent_ack", "health", "control", "outcome"):
        assert run(T2, by_type(name)), name


def test_belief_entities_and_diagnostic_label() -> None:
    line = by_type("belief_update").replace('"entropy"', '"change_score":1.5,"entropy"', 1)
    e = ents(run(T1, line))
    assert "belief/entropy" in e and any(x.startswith("belief/posterior/") for x in e)
    assert "belief/change_score_diagnostic" in e
    assert "belief/change_score" not in e


def test_intent_counters_accumulate() -> None:
    m = Mapper(T1)
    for name in ("intent_emitted", "intent_rejected", "intent_ack"):
        m.map(BUS_EVENT_ADAPTER.validate_json(by_type(name)))
    assert m.counters["emitted"] == 1 and m.counters["rejected"] == 1
    assert m.counters["applied"] + m.counters["lost_superseded"] <= 1


def test_multiplayer_banner_from_receipt_and_health() -> None:
    line = by_type("intent_emitted").replace('"ok":true', '"ok":false,"error":"multiplayer"')
    ev = BUS_EVENT_ADAPTER.validate_json(line)
    recs = Mapper(T2).map(ev)
    banner = [r for r in recs if r.entity == "alert/multiplayer"]
    assert banner and banner[0].level == "ERROR" and "MULTIPLAYER" in banner[0].text
    assert any(r.entity == "intent/refused_multiplayer" for r in Mapper(T1).map(ev))
    h = BUS_EVENT_ADAPTER.validate_json(by_type("health"))
    assert isinstance(h, HealthEvent)
    h2 = h.model_copy(update={"payload": h.payload.model_copy(update={"kind": HealthKind.INI_WRITE_FAILED,
                                                                       "detail": "seq 3: multiplayer"})})
    assert any(r.entity == "alert/multiplayer" for r in Mapper(T2).map(h2))


def test_unknown_health_kind_rendered_generically() -> None:
    h = BUS_EVENT_ADAPTER.validate_json(by_type("health", 1))
    assert isinstance(h, HealthEvent)
    for kind in ("ack_alarm", "daemon_exit", "invalid_event"):
        ev = h.model_copy(update={"payload": h.payload.model_copy(update={"kind": HealthKind(kind), "detail": "why"})})
        texts = [r for r in Mapper(T2).map(ev) if r.kind == "text"]
        assert texts and texts[0].entity == f"health/{kind}" and "why" in texts[0].text


def test_level_cap_live_only() -> None:
    assert effective_tier(T2, HealthLevel.H0) is T2
    assert effective_tier(T2, HealthLevel.H1) is T1
    assert effective_tier(T1, HealthLevel.H1) is T1
    assert effective_tier(T2, HealthLevel.H3) is T0
    assert effective_tier(T2, HealthLevel.S) is T0
    h = BUS_EVENT_ADAPTER.validate_json(by_type("health"))
    assert isinstance(h, HealthEvent)
    h2 = h.model_copy(update={"payload": h.payload.model_copy(update={"level": HealthLevel.H2})})
    live = Mapper(T2, live=True)
    assert live.map(h2) == []
    assert live.map(BUS_EVENT_ADAPTER.validate_json(by_type("belief_update"))) == []
    assert Mapper(T2).map(h2)  # offline: no cap


def test_god_only_at_t2_under_god_prefix() -> None:
    line = ('{"v":1,"run_id":"20261019-031500-synth-0a1b","seq":1,"t_wall":"2026-10-19T03:15:01.000Z",'
            '"t_mono_ns":2000,"game_date":"2231.04.01","type":"observation_god","fow":"god","payload":'
            '{"game_date":"2231.04.01","source":"save","player":{"fleet_power":5.0,"systems_owned":3,'
            '"tech_researched":2,"alloys_income":1.5,"fortified_starbases":0},"plyr":{"x":1.0}}}')
    ev = GOD_BUS_EVENT_ADAPTER.validate_json(line)
    assert Mapper(T1).map(ev) == []
    recs = Mapper(T2).map(ev)
    assert recs and all(r.entity.startswith("god/") for r in recs)


def test_null_game_date_skips_month() -> None:
    recs = run(T2, by_type("run_started"))
    assert recs and all(r.game_month is None for r in recs)




def test_v12_health_kinds_and_ironman_receipt_alert() -> None:
    hl = json.loads(by_type("health"))
    for kind, detail, entity in (("ironman_observe_only", "save x.sav", "alert/ironman"),
                                 ("ini_neutralised", "close", "alert/ini")):
        h = BUS_EVENT_ADAPTER.validate_json(json.dumps({**hl, "payload": {**hl["payload"], "kind": kind,
                                                                         "detail": detail}}))
        recs = Mapper(T2).map(h)
        assert any(r.entity == entity and detail in r.text for r in recs)
        assert any(r.entity == f"health/{kind}" for r in recs)
    line = by_type("intent_emitted").replace('"ok":true', '"ok":false,"error":"ironman"')
    assert any(r.entity == "alert/ironman" for r in Mapper(T2).map(BUS_EVENT_ADAPTER.validate_json(line)))


def test_governor_metrics_and_deferred() -> None:
    hl = json.loads(by_type("health"))
    metrics = {"spm_med6": 4.0, "parse_wall_s": 2.5, "daemon_rss_bytes": 1.0}
    h = BUS_EVENT_ADAPTER.validate_json(json.dumps({**hl, "payload": {**hl["payload"], "metrics": metrics}}))
    ents = {r.entity for r in Mapper(T1).map(h)}
    assert {"governor/spm/spm_med6", "governor/parse/parse_wall_s", "health/metric/daemon_rss_bytes"} <= ents
    rj = json.loads(by_type("intent_rejected"))
    d = BUS_EVENT_ADAPTER.validate_json(json.dumps({**rj, "payload": {**rj["payload"], "rule": "rate_limit",
                                                                     "detail": "deferred: one batch per game month"}}))
    recs = Mapper(T2).map(d)
    assert any(r.entity == "intent/deferred" and r.value == 1.0 for r in recs)
    assert any(r.entity == "audit/deferred" for r in recs)
    assert not any(r.entity == "intent/rejected" for r in recs)


def test_rule_deferred_counted_apart_from_rejections() -> None:
    rj = json.loads(by_type("intent_rejected"))
    d = BUS_EVENT_ADAPTER.validate_json(json.dumps({**rj, "payload": {**rj["payload"], "rule": "deferred",
                                                                     "detail": "no HB month counter yet"}}))
    ents = {r.entity for r in Mapper(T2).map(d)}
    assert "intent/deferred" in ents and "intent/rejected" not in ents
