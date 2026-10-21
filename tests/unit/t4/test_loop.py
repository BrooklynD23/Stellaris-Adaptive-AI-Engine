"""Daemon loop (U6): fakes only, tiny poll intervals, no real sleeps beyond a few ms."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from sae.contracts.c2_ini import KnobCommand
from sae.contracts.c3_intent import IntentBatch
from sae.contracts.c5_bus import BUS_EVENT_ADAPTER, HealthKind, HealthPayload, IntentAckPayload, IntentEmittedPayload
from sae.contracts.c6_adapter import (
    AdapterMode,
    EmitReceipt,
    GameAdapter,
    GameClock,
    GodSink,
    IntentAck,
    Observation,
    SaveParser,
)
from sae.contracts.c7_governor import HealthLevel, Trigger
from sae.contracts.c8_config import SaeConfig
from sae.contracts.c9_control import ControlCommand, ControlResult, ForceStrategy, Pause, Status
from sae.contracts.common import AckCode, AckStatus, Archetype, Strategy
from sae.core.state import EngineState, EventDraft
from sae.daemon import run_engine
from sae.daemon.ports import InboxItem
from sae.daemon.saveparser import GovernedSaveParser
from sae.daemon.statefile import load_state

from .fake_synth import Sim, date_for
from .fakes import FakeGovernor, FakeInbox, FakePlatform, FakeSensors, FakeSink, FakeWorker, decision, make_deps

CFG = SaeConfig()
FAST = {"poll_s": 0.001, "inbox_s": 0.001}


def run(coro: Awaitable[Any]) -> Any:
    async def _w() -> Any:
        return await asyncio.wait_for(coro, 20)

    return asyncio.run(_w())


def cmd(command: Any, n: int = 0) -> InboxItem:
    # Fresh issue time: C9 v1.1 section 3.1 expires commands issued before the daemon session started.
    c = ControlCommand(command_id=f"c-20260101T0000{n:02d}Z-abcdef",
                       issued_at=datetime.now(UTC) + timedelta(seconds=2), operator="t", command=command)
    return InboxItem(command=c, error=None, handle=f"h{n}")


class PushInbox(FakeInbox):
    """FakeInbox that tests can push into while the loop runs."""

    def push(self, *items: InboxItem) -> None:
        self.items.extend(items)

    async def wait_done(self, n: int) -> None:
        while len(self.done) < n:
            await asyncio.sleep(0.001)


class EchoAdapter:
    """Sim-driven adapter: log obs every month, save obs mid-year, echoes APPLIED ACKs for every batch."""

    mode = AdapterMode.SYNTH

    def __init__(self, months: int, start: int = 0, delay: float = 0.002,
                 hooks: dict[int, Callable[[], Awaitable[None]]] | None = None, boom_at: int | None = None) -> None:
        self.sim = Sim(Archetype.TURTLE, 1)
        self.sim.t = start
        self.months, self.delay, self.hooks, self.boom_at = months, delay, hooks or {}, boom_at
        self.batches: list[IntentBatch] = []
        self.acks_q: asyncio.Queue[IntentAck] = asyncio.Queue()
        self.closed = False
        self.date = date_for(start)

    async def observations(self) -> AsyncIterator[Observation]:
        s = self.sim
        await asyncio.sleep(self.delay)
        for _ in range(self.months):
            if s.t == self.boom_at:
                raise RuntimeError("boom " + "x" * 600)
            self.date = date_for(s.t)
            yield s.obs("log")
            if s.t % 12 == 6:
                s.views, s.as_of = s._views(), date_for(s.t)
                yield s.obs("save")
            hook = self.hooks.get(s.t)
            if hook is not None:
                await hook()
            s.t += 1
            await asyncio.sleep(self.delay)

    async def acks(self) -> AsyncIterator[IntentAck]:
        while True:
            yield await self.acks_q.get()

    async def emit(self, batch: IntentBatch) -> EmitReceipt:
        self.batches.append(batch)
        for c in sorted({i.country for i in batch.intents}):
            self.sim.applied[c] = batch.seq
            self.acks_q.put_nowait(IntentAck(seq=batch.seq, country=c, status=AckStatus.APPLIED, code=AckCode.OK,
                                             game_date=self.date))
        return EmitReceipt(seq=batch.seq, ok=True)

    def clock(self) -> GameClock:
        return GameClock(game_date=self.date, m=self.sim.t, session_epoch=0)

    async def close(self) -> None:
        self.closed = True


def go(adapter: GameAdapter | Callable[[SaveParser, GodSink], GameAdapter], tmp: Path, *,
       gov: FakeGovernor | None = None, sink: FakeSink | None = None, inbox: FakeInbox | None = None,
       sensors: FakeSensors | None = None, state: EngineState | None = None, platform: FakePlatform | None = None,
       reexec: Callable[[], None] | None = None) -> tuple[int, FakeSink, FakeGovernor, FakeInbox]:
    deps = make_deps(platform or FakePlatform(), gov, sink, inbox)
    from dataclasses import replace
    if sensors is not None:
        deps = replace(deps, sensors=sensors)
    if reexec is not None:
        deps = replace(deps, reexec=reexec)
    code = run(run_engine(CFG, adapter, "r1", deps, tmp, state, **FAST))
    return code, deps.sink, deps.governor, deps.inbox  # type: ignore[return-value]


def types(sink: FakeSink) -> list[str]:
    return [d.type for d in sink.drafts]


def kinds(sink: FakeSink, component: str | None = None) -> list[HealthKind]:
    return [d.payload.kind for d in sink.drafts if d.type == "health"  # type: ignore[attr-defined]
            and (component is None or d.payload.component == component)]  # type: ignore[attr-defined]


def valid(d: EventDraft) -> None:
    BUS_EVENT_ADAPTER.validate_python({
        "v": 1, "run_id": "20260101-000000-synth-abcd", "seq": 0, "t_wall": "2026-01-01T00:00:00Z",
        "t_mono_ns": 0, "game_date": d.game_date, "decision_id": d.decision_id, "type": d.type,
        "payload": d.payload.model_dump(mode="json")})


def test_full_chain_shares_decision_id_and_validates(tmp_path: Path) -> None:
    ad = EchoAdapter(14)
    code, sink, gov, _ = go(ad, tmp_path)
    assert code == 0 and ad.closed and sink.closed
    assert {"observation", "belief_update", "strategy_selected", "intent_emitted", "intent_ack"} <= set(types(sink))
    for d in sink.drafts:
        valid(d)
    emitted = [d for d in sink.drafts if d.type == "intent_emitted"]
    assert emitted and ad.batches
    first = emitted[0]
    same = [d.type for d in sink.drafts if d.decision_id == first.decision_id]
    assert {"observation", "belief_update", "strategy_selected", "intent_emitted"} <= set(same)
    ids = {i for e in emitted for i in (x.intent_id for x in e.payload.batch.intents)}  # type: ignore[attr-defined]
    acks = [d.payload for d in sink.drafts if d.type == "intent_ack"]
    assert acks and all(isinstance(a, IntentAckPayload) and set(a.intent_ids) <= ids for a in acks)
    assert isinstance(emitted[0].payload, IntentEmittedPayload)
    assert gov.boundary is not None  # set_next_boundary after strategy step
    assert (tmp_path / "state.json").exists()


def test_pause_stops_batches_and_suspends_governor(tmp_path: Path) -> None:
    inbox = PushInbox()
    ad = EchoAdapter(40)
    marks: list[int] = []

    async def pause() -> None:
        marks.append(len(ad.batches))
        inbox.push(cmd(Pause()))
        await inbox.wait_done(1)

    ad.hooks = {8: pause}
    code, sink, gov, _ = go(ad, tmp_path, inbox=inbox)
    assert code == 0 and gov.suspended is True
    assert len(ad.batches) == marks[0]
    assert "control" in types(sink)
    res = inbox.done[0][1]
    assert res.accepted and res.status is not None and res.status.suspended and res.status.level is HealthLevel.S


def test_force_strategy_status_and_immediate_completion(tmp_path: Path) -> None:
    inbox = PushInbox()
    ad = EchoAdapter(30)

    async def force() -> None:
        inbox.push(cmd(ForceStrategy(strategy=Strategy.EARLY_PUNISH, months=24), 1), cmd(Status(), 2))
        await inbox.wait_done(2)

    ad.hooks = {10: force}
    code, sink, _, _ = go(ad, tmp_path, inbox=inbox)
    assert code == 0
    by_id = {i.command.command_id: r for i, r in inbox.done if i.command is not None}
    status = by_id["c-20260101T000002Z-abcdef"]
    assert status.accepted and status.status is not None and status.status.forced_until == date_for(34)
    assert status.status.run_id == "r1" and status.applied_at == date_for(10)
    later = [d.payload.strategy for d in sink.drafts  # type: ignore[attr-defined]
             if d.type == "strategy_selected" and (d.game_date or "") > date_for(10)]
    assert later and all(s is Strategy.EARLY_PUNISH for s in later)
    assert load_state(tmp_path / "state.json").strategy.forced is not None


def test_inbox_error_item_logged_as_control_event_without_command(tmp_path: Path) -> None:
    err = ControlResult(command_id="unknown", accepted=False, error="invalid", detail="bad", applied_at=None,
                        status=None)
    inbox = FakeInbox(InboxItem(command=None, error=err, handle="bad"))
    _, sink, _, _ = go(EchoAdapter(3), tmp_path, inbox=inbox)
    assert inbox.done == [(inbox.done[0][0], err)]
    ctl = [d.payload for d in sink.drafts if d.type == "control"]
    assert len(ctl) == 1 and ctl[0].command is None and ctl[0].result == err  # type: ignore[attr-defined]


def test_knob_request_lands_in_next_batch(tmp_path: Path) -> None:
    knob = KnobCommand(emit_period=2)
    gov = FakeGovernor(decision(knob_request=knob))
    ad = EchoAdapter(8)
    go(ad, tmp_path, gov=gov)
    assert ad.batches and ad.batches[0].knobs == knob


def test_level_and_state_change_health(tmp_path: Path) -> None:
    gov = FakeGovernor(decision(level=HealthLevel.H1, trigger=Trigger.RAM))
    _, sink, _, _ = go(EchoAdapter(3), tmp_path, gov=gov)
    assert HealthKind.LEVEL_CHANGE in kinds(sink)
    lc = next(d.payload for d in sink.drafts if d.type == "health"
              and d.payload.kind is HealthKind.LEVEL_CHANGE)  # type: ignore[attr-defined]
    assert isinstance(lc, HealthPayload) and lc.trigger is Trigger.RAM and "daemon_rss_bytes" in lc.metrics


def test_monthly_health_and_spm_samples(tmp_path: Path) -> None:
    t = [0.0]

    def mono() -> float:
        t[0] += 3.0
        return t[0]

    from dataclasses import replace
    gov = FakeGovernor()
    deps = replace(make_deps(FakePlatform(), gov), monotonic=mono)
    run(run_engine(CFG, EchoAdapter(6), "r1", deps, tmp_path, None, **FAST))
    assert kinds(deps.sink) .count(HealthKind.MONTHLY) >= 4  # type: ignore[arg-type]
    assert gov.months and all(m.months == 1 and m.wall_s > 0 for m in gov.months)


def _mk_loop(tmp: Path, sensors: FakeSensors | None = None, gov: FakeGovernor | None = None,
             inbox: FakeInbox | None = None, platform: FakePlatform | None = None, factory: bool = False) -> Any:
    from dataclasses import replace

    from sae.daemon.loop import _Loop
    deps = make_deps(platform or FakePlatform(), gov, None, inbox)
    if sensors is not None:
        deps = replace(deps, sensors=sensors)
    ad: Any = (lambda _p, _s: EchoAdapter(1)) if factory else EchoAdapter(1)
    return _Loop(CFG, ad, "r1", deps, tmp, None, 0.001, 0.001)


def test_rss_breach_once_per_game_month(tmp_path: Path) -> None:
    lp = _mk_loop(tmp_path, FakeSensors(rss=200 << 20))
    for _ in range(5):  # deterministic driver: same game month
        lp.poll_once()
    lp.state = lp.state.model_copy(update={"last_obs_date": "2200.02.01"})
    for _ in range(5):
        lp.poll_once()
    assert kinds(lp.deps.sink, "daemon").count(HealthKind.BUDGET_BREACH) == 2


class RaisingSensors(FakeSensors):
    def sample(self) -> Any:
        raise RuntimeError("sensor boom")


def test_sensor_exception_does_not_kill_daemon(tmp_path: Path) -> None:
    ad = EchoAdapter(14)
    deps = make_deps(FakePlatform())
    from dataclasses import replace
    deps = replace(deps, sensors=RaisingSensors())
    code = run(run_engine(CFG, ad, "r1", deps, tmp_path, None, **FAST))
    assert code == 0 and ad.batches
    assert HealthKind.BUDGET_BREACH not in kinds(deps.sink, "daemon")


def test_inbox_exception_does_not_kill_daemon(tmp_path: Path) -> None:
    class BadInbox(FakeInbox):
        def poll(self) -> Sequence[InboxItem]:
            raise OSError("inbox boom")

    code, sink, _, _ = go(EchoAdapter(4, delay=0.01), tmp_path, inbox=BadInbox())
    assert code == 0 and HealthKind.BUDGET_BREACH not in kinds(sink, "daemon")


def test_queued_ack_applied_before_final_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from sae.daemon import loop as loop_mod
    monkeypatch.setattr(loop_mod.engine, "on_ack",
                        lambda st, a: (st.model_copy(update={"decision_n": 99}), loop_mod.Outputs()))
    lp = _mk_loop(tmp_path)
    ack = IntentAck(seq=1, country=1, status=AckStatus.APPLIED, code=AckCode.OK, game_date="2200.01.01")
    lp._put(1, "ack", ack)
    lp._put(0, "exit")
    run(lp._consume(lp.adapter))
    assert load_state(tmp_path / "state.json").decision_n == 99


def test_inbox_redelivery_is_skipped_until_complete(tmp_path: Path) -> None:
    item = cmd(Status())

    class Sticky(FakeInbox):
        def poll(self) -> Sequence[InboxItem]:
            return [] if self.done else [item]

    inbox = Sticky()
    lp = _mk_loop(tmp_path, inbox=inbox)
    for _ in range(3):
        lp.inbox_once()
    assert lp.q.qsize() == 1


def test_worker_killed_on_shutdown(tmp_path: Path) -> None:
    w = FakeWorker(None, hang=True)
    plat = FakePlatform(w)

    async def scenario() -> None:
        lp = _mk_loop(tmp_path, platform=plat, factory=True)
        lp.parser.set_decision(decision())
        t = asyncio.create_task(lp.parser.parse("x.sav"))
        await asyncio.sleep(0.01)
        await lp.close()
        assert w.killed
        await t

    run(scenario())


def test_daemon_should_exit_reexecs_and_returns_zero(tmp_path: Path) -> None:
    calls: list[int] = []
    gov = FakeGovernor(decision(daemon_should_exit=True))
    code, sink, _, _ = go(EchoAdapter(500, delay=0.01), tmp_path, gov=gov, reexec=lambda: calls.append(1))
    assert code == 0 and calls == [1] and sink.closed and sink.flushed >= 1
    assert HealthKind.DAEMON_EXIT in kinds(sink)
    assert (tmp_path / "state.json").exists()


def test_state_json_resume_continues_seq(tmp_path: Path) -> None:
    ad1 = EchoAdapter(20)
    go(ad1, tmp_path)
    st = load_state(tmp_path / "state.json")
    assert ad1.batches
    last = ad1.batches[-1].seq
    ad2 = EchoAdapter(30, start=20)
    go(ad2, tmp_path, state=st)
    assert ad2.batches and min(b.seq for b in ad2.batches) > last


def test_factory_builds_adapter_with_governed_parser_and_sink(tmp_path: Path) -> None:
    seen: list[tuple[SaveParser, GodSink]] = []
    ad = EchoAdapter(3)

    def factory(parser: SaveParser, god: GodSink) -> GameAdapter:
        seen.append((parser, god))
        return ad

    code, sink, _, _ = go(factory, tmp_path)
    assert code == 0 and ad.closed
    assert isinstance(seen[0][0], GovernedSaveParser) and seen[0][1] is sink


def test_kill_worker_path(tmp_path: Path) -> None:
    worker = FakeWorker(None, hang=True)
    plat = FakePlatform(worker)
    results: list[Any] = []
    gov = FakeGovernor(decision(kill_worker=True))
    ad = EchoAdapter(30, delay=0.005)

    def factory(parser: SaveParser, god: GodSink) -> GameAdapter:
        async def parse() -> None:
            results.append(await parser.parse("x.sav"))

        ad.hooks = {0: lambda: asyncio.create_task(parse()) and _noop()}
        return ad

    async def _noop() -> None:
        await asyncio.sleep(0)

    go(factory, tmp_path, gov=gov, platform=plat)
    assert worker.killed
    assert gov.outcomes and gov.outcomes[0].kind == "killed_by_governor"


def test_iterator_end_exits_zero_and_closes(tmp_path: Path) -> None:
    ad = EchoAdapter(2)
    code, sink, _, _ = go(ad, tmp_path)
    assert code == 0 and ad.closed and sink.closed
    assert kinds(sink)[-1] is HealthKind.DAEMON_EXIT


def test_exception_exits_one_with_daemon_exit(tmp_path: Path) -> None:
    ad = EchoAdapter(10, boom_at=3)
    code, sink, _, _ = go(ad, tmp_path)
    assert code == 1 and ad.closed and sink.closed
    h = [d.payload for d in sink.drafts if d.type == "health"][-1]
    assert isinstance(h, HealthPayload) and h.kind is HealthKind.DAEMON_EXIT
    assert h.detail is not None and h.detail == "RuntimeError"


def test_cancellation_exits_zero(tmp_path: Path) -> None:
    async def main() -> int:
        deps = make_deps(FakePlatform())
        t = asyncio.create_task(run_engine(CFG, EchoAdapter(10_000, delay=0.01), "r1", deps, tmp_path, None, **FAST))
        await asyncio.sleep(0.05)
        t.cancel()
        code = await t
        assert HealthKind.DAEMON_EXIT in kinds(deps.sink)  # type: ignore[arg-type]
        assert deps.sink.closed  # type: ignore[attr-defined]
        return int(code)

    assert run(main()) == 0


def test_run_without_wiring_returns_one(capsys: Any) -> None:
    from sae.daemon import run as daemon_run
    try:
        import sae.daemon.wiring  # noqa: F401
    except ImportError:
        assert asyncio.run(daemon_run(CFG, EchoAdapter(1), "r")) == 1
        assert "wiring not available" in capsys.readouterr().err


_ = Sequence


def test_stale_force_strategy_from_previous_session_is_not_applied(tmp_path: Path) -> None:
    """C9 v1.1 section 3.1 (proposal 016): a command left in the inbox before this session started expires."""
    stale = ControlCommand(command_id="c-20260101T000009Z-abcdef", issued_at=datetime.now(UTC) - timedelta(hours=3),
                           operator="t", command=ForceStrategy(strategy=Strategy.EARLY_PUNISH, months=24))
    inbox = FakeInbox(InboxItem(command=stale, error=None, handle="old"))
    code, sink, _, _ = go(EchoAdapter(30), tmp_path, inbox=inbox)
    assert code == 0
    ((item, res),) = inbox.done
    assert not res.accepted and res.error == "expired" and item.command is not None
    ctl = [d.payload for d in sink.drafts if d.type == "control"]
    assert len(ctl) == 1 and ctl[0].command == stale and ctl[0].result.error == "expired"  # type: ignore[attr-defined]
    assert not any(d.payload.strategy is Strategy.EARLY_PUNISH  # type: ignore[attr-defined]
                   for d in sink.drafts if d.type == "strategy_selected")
    assert load_state(tmp_path / "state.json").strategy.forced is None
