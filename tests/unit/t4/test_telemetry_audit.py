"""Monthly health uses the C5 metric keys (incl. T5 spm medians and parse cost); deferred intents are audited."""

from __future__ import annotations

from pathlib import Path

from sae.contracts.c4_parse import Cost
from sae.contracts.c5_bus import IntentRejectedPayload, RejectRule
from sae.contracts.c6_adapter import GameEvent
from sae.core.engine import initial_state, on_observation
from sae.core.engine_tick import DEFER_NO_M, DEFER_ONE_PER_MONTH
from sae.daemon.loop import _Loop

from .fakes import FakeGovernor, FakePlatform, FakeSensors, make_deps
from .test_hardening import CTX, ECFG, M0, TURTLE, obs, step, view
from .test_loop import CFG, EchoAdapter

C5_KEYS = {"avail_ram_bytes", "fault_rate", "spm", "spm_med6", "spm_med24", "daemon_rss_bytes", "daemon_cpu_pct",
           "parse_rss_peak_bytes", "parse_cpu_s", "parse_wall_s", "saves_skipped", "workers_killed", "pause_fraction"}


class MedGovernor(FakeGovernor):
    spm_med6: float | None = 4.0
    spm_med24: float | None = None


def _loop(tmp_path: Path, gov: FakeGovernor) -> _Loop:
    deps = make_deps(FakePlatform(), gov)
    return _Loop(CFG, lambda p, g: EchoAdapter(1), "r1", deps, tmp_path, None, 2.0, 2.0)


def test_monthly_metrics_use_c5_keys_spm_medians_and_parse_cost(tmp_path: Path) -> None:
    lp = _loop(tmp_path, MedGovernor())
    lp.spm = 4.2
    assert lp.parser is not None
    lp.parser.last_cost = Cost(rss_peak_bytes=123, cpu_s=1.5, wall_s=2.5)
    m = lp._metrics(FakeSensors().sample())
    assert set(m) <= C5_KEYS
    assert m["spm"] == 4.2 and m["spm_med6"] == 4.0 and "spm_med24" not in m  # None -> omitted
    assert (m["parse_rss_peak_bytes"], m["parse_cpu_s"], m["parse_wall_s"]) == (123.0, 1.5, 2.5)
    assert m["avail_ram_bytes"] > 0


def test_metrics_without_accessors_or_cost(tmp_path: Path) -> None:
    m = _loop(tmp_path, FakeGovernor())._metrics(FakeSensors().sample())
    assert set(m) <= C5_KEYS and not {"spm_med6", "spm_med24", "parse_cpu_s"} & set(m)


def _deferred(types_payloads: list[tuple[str, object]], detail: str) -> list[IntentRejectedPayload]:
    return [p for t, p in types_payloads if t == "intent_rejected" and isinstance(p, IntentRejectedPayload)
            and p.rule is RejectRule.DEFERRED and p.detail == detail]


def test_m_none_deferral_is_audited_and_ids_not_reused() -> None:
    vs = (view(2, **TURTLE), view(3, **TURTLE))
    o = obs(M0, 100, views=vs, asof=M0 - 1).model_copy(update={"m": None})
    s, out = on_observation(initial_state(ECFG), o, CTX)
    assert out.batch is None
    rows = _deferred([(d.type, d.payload) for d in out.drafts], DEFER_NO_M)
    assert len(rows) == 4  # econ + posture for each in-contact AI
    s, out2 = on_observation(s, obs(M0 + 1, 101, views=vs, asof=M0), CTX)
    assert out2.batch is not None
    assert not {i.intent_id for i in out2.batch.intents} & {r.intent.intent_id for r in rows}


def test_one_per_month_deferral_is_audited() -> None:
    vs = (view(2, **TURTLE), view(3, **TURTLE))
    s, _, b = step(initial_state(ECFG), obs(M0, 100, views=vs, asof=M0 - 1))
    assert b is not None
    s, out = on_observation(s, obs(M0, 100, epoch=1, views=vs, asof=M0 - 1, events=(GameEvent.DATE_REGRESSED,)), CTX)
    assert out.batch is None and _deferred([(d.type, d.payload) for d in out.drafts], DEFER_ONE_PER_MONTH)
