"""T4-HARDEN regressions (adversary probes): engine, lifecycle, strategy, runs, saveparser, loop."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from sae.contracts.c2_ini import KnobCommand
from sae.contracts.c3_intent import IntentBatch
from sae.contracts.c4_parse import LegalView
from sae.contracts.c5_bus import ControlPayload, HealthKind, HealthPayload
from sae.contracts.c6_adapter import AdapterMode, EmitReceipt, GameEvent, IntentAck, ManagedState, Observation
from sae.contracts.c7_governor import HealthLevel
from sae.contracts.c8_config import EngineConfig
from sae.contracts.c9_control import ControlResult, ForceStrategy
from sae.contracts.common import CLAUSEWITZ_INT_MAX, AckCode, AckStatus, Strategy
from sae.core import lifecycle, strategy
from sae.core.dates import date_of
from sae.core.engine import EngineCtx, initial_state, on_ack, on_emitted, on_observation
from sae.core.engine_tick import make_decision_id
from sae.core.state import EngineState
from sae.daemon import runs
from sae.daemon.control import apply_item
from sae.daemon.ports import AdapterFactory, InboxItem

from .fakes import FakeGovernor, FakePlatform, FakeWorker, decision
from .test_loop import CFG, EchoAdapter, go, kinds
from .test_saveparser import Rig, _err_json, run

ECFG = EngineConfig()
CTX = EngineCtx(mode=AdapterMode.LIVE, cfg=ECFG)
M0 = 2205 * 12
TURTLE = {"systems_owned": 20, "frontier_systems": 10, "fortified_border_starbases": 8, "fleet_power_cat": 0,
          "tech_cat": 0}
AGGR = {"systems_owned": 20, "frontier_systems": 10, "fortified_border_starbases": 1, "fleet_power_cat": 2,
        "tech_cat": -1, "player_war_count": 1}


def view(sid: int, **kw: int) -> LegalView:
    return LegalView(observer_sid=sid, in_contact=True, **kw)  # type: ignore[arg-type]


def managed(applied: int = 0, pending: int | None = None, systems: int = 10) -> tuple[ManagedState, ...]:
    return tuple(ManagedState(sid=s, name=f"ai{s}", applied_seq=applied, pending_seq=pending, fleet_power=100.0,
                              naval_cap=200.0, systems_owned=systems, alloys_income=5.0) for s in (2, 3))


def obs(mi: int, m: int, epoch: int = 0, views: tuple[LegalView, ...] = (), asof: int | None = None,
        events: tuple[GameEvent, ...] = (), mgd: tuple[ManagedState, ...] | None = None) -> Observation:
    return Observation(game_date=date_of(mi), m=m, session_epoch=epoch, source="log", player_sid=1,
                       managed=managed() if mgd is None else mgd, player_legal=views,
                       legal_as_of=None if asof is None else date_of(asof), events=events)


def step(s: EngineState, o: Observation, ctx: EngineCtx = CTX) -> tuple[EngineState, list[str], IntentBatch | None]:
    s, out = on_observation(s, o, ctx)
    if out.batch is not None:
        s, _ = on_emitted(s, out.batch, EmitReceipt(seq=out.batch.seq, ok=True, path="x", bytes_written=1))
    return s, [d.type for d in out.drafts], out.batch


# -- 1: same-month reload keeps resync, no rate-limit loss -------------------------------------------------
def test_same_month_reload_defers_batch_to_next_month() -> None:
    vs = (view(2, **TURTLE), view(3, **TURTLE))
    s, _, b = step(initial_state(ECFG), obs(M0, 100, views=vs, asof=M0 - 1))
    assert b is not None
    s, types, b = step(s, obs(M0, 100, epoch=1, views=vs, asof=M0 - 1, events=(GameEvent.DATE_REGRESSED,)))
    assert b is None and s.lifecycle.resync_needed
    assert "intent_rejected" in types  # audited as deferred (telemetry fix, proposal 018 pending)
    s, _, b = step(s, obs(M0 + 1, 101, epoch=1, views=vs, asof=M0))
    assert b is not None and not s.lifecycle.resync_needed


def test_knob_only_batch_obeys_one_per_month_and_stays_pending() -> None:
    vs = (view(2, **TURTLE), view(3, **TURTLE))
    s, _, b = step(initial_state(ECFG), obs(M0, 100, views=vs, asof=M0 - 1))
    assert b is not None
    ctx = EngineCtx(mode=AdapterMode.LIVE, cfg=ECFG, knob_request=KnobCommand(emit_period=2))
    s, _, b = step(s, obs(M0, 100, epoch=1, views=vs, asof=M0 - 1, events=(GameEvent.HELLO,)), ctx)
    assert b is None and s.batches_this_month == 1
    s, _, b = step(s, obs(M0 + 1, 101, epoch=1, views=vs, asof=M0), ctx)
    assert b is not None and b.knobs == ctx.knob_request


# -- 2: out-of-range seq values --------------------------------------------------------------------------
def test_huge_seq_rejected_at_model_boundary() -> None:
    with pytest.raises(ValidationError):
        ManagedState(sid=2, name="ai2", applied_seq=CLAUSEWITZ_INT_MAX + 1)
    with pytest.raises(ValidationError):
        ManagedState(sid=2, name="ai2", applied_seq=0, pending_seq=2**70)


def test_huge_seq_ignored_with_health_not_crash() -> None:
    # Defence in depth (proposal 007): bypass validation, the engine still ignores out-of-range seqs.
    bad = tuple(ManagedState.model_construct(**{**m.model_dump(), "applied_seq": CLAUSEWITZ_INT_MAX + 5})
                for m in managed())
    s2, out = on_observation(initial_state(ECFG), obs(M0, 100, mgd=bad), CTX)
    h = [d.payload for d in out.drafts if d.type == "health"]
    assert any(isinstance(p, HealthPayload) and p.kind is HealthKind.INVALID_EVENT and p.component == "engine"
               and str(CLAUSEWITZ_INT_MAX) in (p.detail or "") for p in h)
    assert s2.lifecycle.invalid_seen == 0  # reported once
    assert s2.lifecycle.next_seq == 1


def test_seq_equal_to_cap_is_valid_and_exhausts_space() -> None:
    st = lifecycle.observe_managed(lifecycle.LifecycleState(next_seq=1), managed(applied=CLAUSEWITZ_INT_MAX))
    assert st.invalid_seen == 0 and st.next_seq == CLAUSEWITZ_INT_MAX + 1
    assert not lifecycle.can_allocate(st)


def test_huge_ack_applied_is_ignored() -> None:
    with pytest.raises(ValidationError):
        IntentAck(seq=1, country=2, status=AckStatus.DUPLICATE, code=AckCode.SEQ_OLD, applied=10**9,
                  game_date=date_of(M0))
    ack = IntentAck.model_construct(seq=1, country=2, status=AckStatus.DUPLICATE, code=AckCode.SEQ_OLD,
                                    applied=10**9, game_date=date_of(M0), reason=None)
    s, out = on_ack(initial_state(ECFG), ack)
    assert s.lifecycle.next_seq == 2 and any(d.type == "health" for d in out.drafts)


def test_seq_space_exhausted_skips_batch_with_health() -> None:
    s = initial_state(ECFG)
    s = s.model_copy(update={"lifecycle": s.lifecycle.model_copy(update={"next_seq": CLAUSEWITZ_INT_MAX + 1})})
    vs = (view(2, **TURTLE), view(3, **TURTLE))
    s, out = on_observation(s, obs(M0, 100, views=vs, asof=M0 - 1), CTX)
    assert out.batch is None and not lifecycle.can_allocate(s.lifecycle)
    assert any(d.type == "health" for d in out.drafts)


# -- 3: restart at the same save does not recount --------------------------------------------------------
def test_restart_same_save_does_not_recount_evidence() -> None:
    vs = (view(2, **AGGR), view(3, **AGGR))
    s, _, _ = step(initial_state(ECFG), obs(M0, 100, views=vs, asof=M0))
    posts = []
    for e in range(1, 4):
        s, out = on_observation(s, obs(M0, 100, epoch=e, views=vs, asof=M0, events=(GameEvent.HELLO,)), CTX)
        posts.append(next(d.payload for d in out.drafts if d.type == "belief_update").posterior)  # type: ignore[attr-defined]
    assert posts[0] == posts[1] == posts[2]


# -- 6: untrusted huge ints ------------------------------------------------------------------------------
def test_huge_untrusted_ints_never_raise() -> None:
    big = 10**400
    with pytest.raises(ValidationError):
        view(2, systems_owned=big)
    vs = (view(2, systems_owned=10), view(3, systems_owned=10))
    s, _, _ = step(initial_state(ECFG), obs(M0, 100, views=vs, asof=M0))
    # Defence in depth: values that bypassed validation are clamped, never raise.
    raw = dict(systems_owned=big, frontier_systems=big, fortified_border_starbases=big, player_war_count=big)
    vs2 = (LegalView.model_construct(observer_sid=2, in_contact=True, fleet_power_cat=None, tech_cat=None,
                                     economy_cat=None, at_war_with_observer=None, **raw),)
    mgd = tuple(ManagedState.model_construct(**{**m.model_dump(), "systems_owned": big})
                for m in managed())
    on_observation(s, obs(M0 + 13, 113, asof=M0 + 13).model_copy(update={"player_legal": vs2, "managed": mgd}), CTX)
    on_observation(s, obs(M0 + 14, 114, asof=M0 + 13).model_copy(update={"player_legal": vs2, "managed": mgd}), CTX)


# -- 7: no-op clear ---------------------------------------------------------------------------------------
def test_noop_clear_does_not_reopen() -> None:
    st = strategy.StrategyState()
    assert not strategy.apply_force(st, ForceStrategy(cmd="force_strategy", strategy=None, months=1), 5).reopen
    forced = strategy.apply_force(st, ForceStrategy(cmd="force_strategy", strategy=Strategy.BASELINE, months=6), 5)
    assert forced.reopen
    cleared = strategy.apply_force(forced, ForceStrategy(cmd="force_strategy", strategy=None, months=1), 6)
    assert cleared.reopen and cleared.forced is None


# -- 8: decision id wraps ---------------------------------------------------------------------------------
def test_decision_id_modulo_100() -> None:
    assert make_decision_id(M0, 100) == make_decision_id(M0, 0) and make_decision_id(M0, 101).endswith("-01")


# -- 9: unstable retry cap -------------------------------------------------------------------------------
def test_unstable_retry_cap_after_three(tmp_path: Path) -> None:
    f = tmp_path / "a.sav"
    f.write_bytes(b"x")
    r = Rig(*(FakeWorker(6, _err_json(6)) for _ in range(3)))
    for _ in range(3):
        assert run(r.p.parse(str(f))) is None
    assert len(r.platform.specs) == 3
    assert run(r.p.parse(str(f))) is None
    assert len(r.platform.specs) == 3 and r.health[-1][0] is HealthKind.SAVE_SKIPPED and r.health[-1][1] == "retry_cap"


# -- 10: runs -------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("argv", "ok"), [
    (["/usr/bin/sae", "daemon"], True), (["C:\\x\\sae.exe", "daemon"], True),
    (["python", "-m", "sae", "daemon"], True), (["vim", "/home/u/sae-notes.txt"], False),
    (["python", "-m", "pytest", "tests/sae_x"], False), ([], False),
])
def test_is_sae_argv(argv: list[str], ok: bool) -> None:
    assert runs._is_sae_argv(argv) is ok


def test_fresh_current_is_exclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "CURRENT").write_text("r0 999999")
    monkeypatch.setattr(runs, "_is_sae_alive", lambda pid: False)
    runs.acquire_current(tmp_path, "r1", False)
    assert (tmp_path / "CURRENT").read_text().split()[0] == "r1"


# -- 13/14: contracts -------------------------------------------------------------------------------------
def test_adapter_factory_is_contract_alias() -> None:
    from sae.contracts import c6_adapter
    assert AdapterFactory is c6_adapter.AdapterFactory


@pytest.mark.parametrize("err", ["invalid", "too_large", "unknown_cmd"])
def test_error_item_logged_as_control_event(err: str) -> None:
    res = ControlResult(command_id="unknown", accepted=False, error=err, detail="d", applied_at=None,  # type: ignore[arg-type]
                        status=None)
    out = apply_item(initial_state(ECFG), InboxItem(command=None, error=res, handle="h"), "r1",
                     CTX.game_state, HealthLevel.H0, None)
    assert out.result == res and len(out.drafts) == 1 and out.drafts[0].type == "control"
    p = out.drafts[0].payload
    assert isinstance(p, ControlPayload) and p.command is None and p.result == res


# -- 11/12: loop ------------------------------------------------------------------------------------------
class RaisingEmit(EchoAdapter):
    async def emit(self, batch: IntentBatch) -> EmitReceipt:
        raise OSError("disk full: secret path")


def test_emit_raising_becomes_write_failed_and_continues(tmp_path: Path) -> None:
    ad = RaisingEmit(30)
    code, sink, _, _ = go(ad, tmp_path)
    assert code == 0
    em = [d.payload for d in sink.drafts if d.type == "intent_emitted"]
    assert em and all(not p.receipt.ok and p.receipt.error == "write_failed"  # type: ignore[attr-defined]
                      and p.receipt.detail == "OSError" for p in em)
    assert HealthKind.INI_WRITE_FAILED in kinds(sink)


def test_reexec_guard_blocks_fourth_in_an_hour(tmp_path: Path) -> None:
    # guard re-checked each poll; emits budget_breach (not daemon_exit) once per game month; daemon stays up
    now = time.time()
    (tmp_path / "reexec.json").write_text(json.dumps([now - 60, now - 120, now - 180]))
    calls: list[int] = []
    gov = FakeGovernor(decision(daemon_should_exit=True))
    code, sink, _, _ = go(EchoAdapter(40, delay=0.005), tmp_path, gov=gov, reexec=lambda: calls.append(1))
    h = [d.payload for d in sink.drafts if d.type == "health"]
    guard = [p for p in h if p.detail == "reexec_guard"]  # type: ignore[attr-defined]
    assert code == 0 and calls == [] and guard
    assert all(p.kind == HealthKind.BUDGET_BREACH and p.component == "daemon" for p in guard)  # type: ignore[attr-defined]
    dates = [d.game_date for d in sink.drafts if d.type == "health" and d.payload.detail == "reexec_guard"]  # type: ignore[attr-defined]
    assert len(dates) == len(set(dates))  # at most once per game month, never once per poll


def test_reexec_records_timestamp_and_old_ones_expire(tmp_path: Path) -> None:
    (tmp_path / "reexec.json").write_text(json.dumps([time.time() - 7200] * 5))
    calls: list[int] = []
    gov = FakeGovernor(decision(daemon_should_exit=True))
    code, _, _, _ = go(EchoAdapter(500, delay=0.01), tmp_path, gov=gov, reexec=lambda: calls.append(1))
    assert code == 0 and calls == [1]
    assert len(json.loads((tmp_path / "reexec.json").read_text())) == 1


_ = (FakePlatform, CFG)


def test_attempt2_reemission_with_exhausted_seq_emits_health() -> None:
    vs = (view(2, **TURTLE), view(3, **TURTLE))
    s, _, b = step(initial_state(ECFG), obs(M0, 100, views=vs, asof=M0 - 1))
    assert b is not None
    s = s.model_copy(update={"lifecycle": s.lifecycle.model_copy(update={"next_seq": CLAUSEWITZ_INT_MAX + 1})})
    found = False
    for k in range(1, 6):  # unacked batch -> attempt-2 re-emission request, but no seq left
        s, out = on_observation(s, obs(M0 + k, 100 + k, views=vs, asof=M0 + k - 1), CTX)
        assert out.batch is None
        found = found or any(d.type == "health" and "seq space exhausted" in (d.payload.detail or "")  # type: ignore[attr-defined]
                             for d in out.drafts)
    assert found


# -- C6 v1.1 section 1.6: multiplayer write guard ---------------------------------------------------------
def test_multiplayer_receipt_logged_and_never_counts_toward_ack_alarm() -> None:
    vs = (view(2, **TURTLE), view(3, **TURTLE))
    s, out = on_observation(initial_state(ECFG), obs(M0, 100, views=vs, asof=M0 - 1), CTX)
    assert out.batch is not None
    s, em = on_emitted(s, out.batch, EmitReceipt(seq=out.batch.seq, ok=False, error="multiplayer"))
    assert [d.type for d in em.drafts][:2] == ["intent_emitted", "health"]
    h = em.drafts[1].payload
    assert isinstance(h, HealthPayload) and h.kind is HealthKind.INI_WRITE_FAILED and h.component == "engine"
    assert h.detail == f"seq {out.batch.seq}: multiplayer"
    assert lifecycle.in_flight_batches(s.lifecycle) == 0
    for k in range(1, 40):  # the guard stays up for 3+ years: nothing LOST, no ack_alarm
        s, o = on_observation(s, obs(M0 + k, 100 + k, views=vs, asof=M0 - 1), CTX)
        if o.batch is not None:
            s, _ = on_emitted(s, o.batch, EmitReceipt(seq=o.batch.seq, ok=False, error="multiplayer"))
        kinds_ = [d.payload.kind for d in o.drafts if d.type == "health"]  # type: ignore[attr-defined]
        assert HealthKind.ACK_ALARM not in kinds_
        assert not any(d.type == "intent_ack" for d in o.drafts)
