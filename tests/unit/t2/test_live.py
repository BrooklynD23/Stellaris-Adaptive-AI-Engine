"""LiveAdapter: pump, ordering, parse concurrency, emit, close (T2 U4). Fake fs under tmp_path."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TypeVar

import pytest

from sae.adapters.live import LiveAdapter
from sae.adapters.paths import ResolvedPaths
from sae.contracts.c1_log import LogRecord, format_log_line
from sae.contracts.c3_intent import IntentBatch
from sae.contracts.c4_parse import PARSE_OUTPUT_ADAPTER, ParseResult
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import AdapterMode, GameAdapter, GodObservation, IntentAck, Observation

from ._asm_helpers import ack, hb, hello, plyr, snap

T = TypeVar("T")
FIXTURE = Path(__file__).resolve().parents[3] / "parser" / "fixtures" / "balanced" / "expected.json"


class Sink:
    def __init__(self) -> None:
        self.got: list[GodObservation] = []

    def write_god(self, obs: GodObservation) -> None:
        self.got.append(obs)


class Mono:
    """Fake monotonic clock: every fake sleep advances it by 3 s."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, _s: float) -> None:
        self.t += 3.0
        await asyncio.sleep(0)


class Parser:
    def __init__(self, result: ParseResult, gate: asyncio.Event | None = None) -> None:
        self.result = result
        self.gate = gate
        self.calls: list[str] = []
        self.sections: list[tuple[str, ...] | None] = []

    async def parse(self, save_path: str, *, sections: tuple[str, ...] | None = None) -> ParseResult | None:
        self.calls.append(save_path)
        self.sections.append(sections)
        if self.gate is not None:
            await self.gate.wait()
        return self.result


def _result(date: str | None = None) -> ParseResult:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if date is not None:
        raw["meta"] = {**raw["meta"], "date": date}
    raw |= {
        "parser_version": "t",
        "save": {"path": "x.sav", "size_bytes": 1, "mtime_ns": 1},
        "cost": {"rss_peak_bytes": 1, "cpu_s": 0, "wall_s": 0},
    }
    out = PARSE_OUTPUT_ADAPTER.validate_python(raw)
    assert isinstance(out, ParseResult)
    return out


def _paths(tmp: Path) -> ResolvedPaths:
    (tmp / "saves" / "c1").mkdir(parents=True)
    return ResolvedPaths(
        user_dir=tmp, game_log=tmp / "game.log", save_dir=tmp / "saves", ini_path=tmp / "sae_in.ini", parser_exe=None
    )


def _append(tmp: Path, *recs: LogRecord) -> None:
    with (tmp / "game.log").open("a", encoding="utf-8", newline="\n") as f:
        for r in recs:
            f.write(format_log_line(r) + "\n")


def _mk(
    tmp: Path, parser: Parser | None = None, *, release: bool = False, record_dir: Path | None = None,
) -> tuple[LiveAdapter, Sink, list[tuple[HealthKind, str | None]], Mono]:
    sink, ev, mono = Sink(), [], Mono()
    a = LiveAdapter(
        _paths(tmp),
        parser or Parser(_result()),
        sink,
        health=lambda k, d: ev.append((k, d)),
        release=release,
        sleep=mono.sleep,
        monotonic=mono,
        record_dir=record_dir,
    )
    return a, sink, ev, mono


async def _next(it: AsyncIterator[T]) -> T:
    return await asyncio.wait_for(it.__anext__(), 2.0)


def _batch() -> IntentBatch:
    return IntentBatch.model_validate(
        {
            "seq": 17,
            "issued_date": "2231.04.01",
            "issued_m": 375,
            "intents": [
                {
                    "intent_id": "i-000381",
                    "decision_id": "d-2231.04-07",
                    "country": 3,
                    "kind": "SET_ECON_PLAN",
                    "plan": "military",
                    "ttl_months": 3,
                    "reason_key": "turtle_siege",
                }
            ],
        }
    )


def test_is_game_adapter(tmp_path: Path) -> None:
    a, *_ = _mk(tmp_path)
    assert isinstance(a, GameAdapter)
    assert a.mode is AdapterMode.LIVE


def test_ack_then_observation(tmp_path: Path) -> None:
    async def go() -> None:
        a, _, _, _ = _mk(tmp_path)
        _append(tmp_path, hello("2231.01.01"), hb("2231.01.01", 5), snap("2231.01.01", sid=2), ack("2231.01.01"))
        # one synchronous poll: acks and observation are both queued by the same poll, ack side first
        items = a._poll_once()
        assert [type(i) for i in items] == [IntentAck, Observation]
        a._ack_q.put_nowait(items[0])
        a._obs_q.put_nowait(items[1])
        got_ack = await _next(a.acks())
        assert isinstance(got_ack, IntentAck) and got_ack.seq == 1
        obs = await _next(a.observations())
        assert obs.source == "log" and [m.sid for m in obs.managed] == [2]
        await a.close()

    asyncio.run(go())


def test_plyr_goes_to_god_sink_only(tmp_path: Path) -> None:
    async def go() -> None:
        a, sink, _, _ = _mk(tmp_path)
        _append(tmp_path, plyr("2231.01.01"))
        assert a._poll_once() == []
        assert len(sink.got) == 1
        await a.close()

    asyncio.run(go())


def test_save_parse_does_not_block_tailing(tmp_path: Path) -> None:
    async def go() -> None:
        gate = asyncio.Event()
        parser = Parser(_result("2231.02.01"), gate)
        a, _, _, _ = _mk(tmp_path, parser)
        save = tmp_path / "saves" / "c1" / "autosave_2231.01.01.sav"
        save.write_bytes(b"SAV")
        it = a.observations()
        _append(tmp_path, hb("2231.01.01", 1))
        first = await _next(it)
        assert first.source == "log"
        for _ in range(400):  # 8 s monotonic rule: ~3 fake polls; poll I/O runs in a thread
            if parser.calls:
                break
            await asyncio.sleep(0.005)
        assert parser.calls == [str(save)]
        # parse is in flight; more log lines still yield observations
        _append(tmp_path, hb("2231.02.01", 2))
        second = await _next(it)
        assert second.source == "log" and second.m == 2
        gate.set()
        third = await _next(it)
        assert third.source == "save"
        assert len(parser.calls) == 1
        await a.close()

    asyncio.run(go())


def test_emit_writes_ini(tmp_path: Path) -> None:
    async def go() -> None:
        a, *_ = _mk(tmp_path)
        r = await a.emit(_batch())
        assert r.ok and r.seq == 17
        text = (tmp_path / "sae_in.ini").read_text(encoding="utf-8")
        assert "17" in text and not (tmp_path / "sae_in.ini.tmp").exists()
        await a.close()
        r2 = await a.emit(_batch())
        assert not r2.ok and r2.error == "closed"

    asyncio.run(go())


def test_release_mode(tmp_path: Path) -> None:
    async def go() -> None:
        a, *_ = _mk(tmp_path, release=True)
        assert a.mode is AdapterMode.RELEASE
        r = await a.emit(_batch())
        assert not r.ok and r.error == "release_mode"
        assert not (tmp_path / "sae_in.ini").exists()
        _append(tmp_path, hb("2231.01.01", 1))  # release still tails
        obs = await _next(a.observations())
        assert obs.m == 1
        await a.close()

    asyncio.run(go())


def test_schema_skew_blocks_emit(tmp_path: Path) -> None:
    async def go() -> None:
        a, *_ = _mk(tmp_path)
        _append(tmp_path, hello("2231.01.01", schema=2))
        a._poll_once()
        r = await a.emit(_batch())
        assert not r.ok and r.error == "write_failed" and r.detail == "schema_skew"
        await a.close()

    asyncio.run(go())


def test_multiplayer_blocks_emit_then_lifts(tmp_path: Path) -> None:
    from sae.contracts.c1_log import HelloRecord

    async def go() -> None:
        a, *_ = _mk(tmp_path)
        _append(tmp_path, HelloRecord(date="2231.01.01", mod="0.1.0", schema=1, period=1, quiet=0, mp=1))
        a._poll_once()
        r = await a.emit(_batch())
        assert not r.ok and r.error == "multiplayer"
        assert not (tmp_path / "sae_in.ini").exists()
        _append(tmp_path, HelloRecord(date="2231.02.01", mod="0.1.0", schema=1, period=1, quiet=0, mp=0))
        a._poll_once()
        assert (await a.emit(_batch())).ok
        await a.close()

    asyncio.run(go())


def test_close_idempotent_and_iterators_end(tmp_path: Path) -> None:
    async def go() -> None:
        a, *_ = _mk(tmp_path)
        obs, acks = a.observations(), a.acks()
        t1 = asyncio.create_task(_next(obs))
        t2 = asyncio.create_task(_next(acks))
        await asyncio.sleep(0.01)
        await a.close()
        await a.close()
        for t in (t1, t2):
            with pytest.raises(StopAsyncIteration):
                await t

    asyncio.run(go())


def test_pump_survives_tailer_exception(tmp_path: Path) -> None:
    class Flaky:
        def __init__(self, inner: object) -> None:
            self.inner = inner
            self.n = 0

        def poll(self) -> list[str]:
            self.n += 1
            if self.n == 1:
                raise RuntimeError("boom")
            return self.inner.poll()  # type: ignore[attr-defined, no-any-return]

    async def go() -> None:
        a, _, ev, _ = _mk(tmp_path)
        a._tailer = Flaky(a._tailer)  # type: ignore[assignment]
        _append(tmp_path, hb("2231.01.01", 1))
        obs = await _next(a.observations())
        assert obs.m == 1
        assert any(k is HealthKind.INVALID_EVENT for k, _ in ev)
        await a.close()

    asyncio.run(go())


def test_record_dir(tmp_path: Path) -> None:
    async def go() -> None:
        rec = tmp_path / "rec"
        a, *_ = _mk(tmp_path, record_dir=rec)
        _append(tmp_path, hb("2231.01.01", 1))
        a._poll_once()
        assert (rec / "game.log").read_text(encoding="utf-8").startswith("SAE|v1|")
        size = (rec / "game.log").stat().st_size
        assert (rec / "game.log.ts").read_text(encoding="utf-8").split()[0] == str(size)
        await a.close()

    asyncio.run(go())


def test_feeder_order_preserved_across_queues(tmp_path: Path) -> None:
    """Mirrors loop.py: one feeder per queue into one shared list; order must be obs1, ack, obs2."""

    async def go() -> None:
        a, *_ = _mk(tmp_path)
        seen: list[str] = []

        async def feed_obs() -> None:
            async for o in a.observations():
                seen.append(f"obs{o.m}")

        async def feed_acks() -> None:
            async for k in a.acks():
                seen.append(f"ack{k.seq}")

        _append(tmp_path, hb("2231.01.01", 1), ack("2231.01.15"), hb("2231.02.01", 2))
        tasks = [asyncio.create_task(feed_obs()), asyncio.create_task(feed_acks())]
        for _ in range(400):  # poll I/O runs in a thread: wait in real (tiny) time
            if len(seen) >= 3:
                break
            await asyncio.sleep(0.005)
        await a.close()
        await asyncio.gather(*tasks)
        assert seen == ["obs1", "ack1", "obs2"]

    asyncio.run(go())


def test_iterator_after_close_ends(tmp_path: Path) -> None:
    async def go() -> None:
        a, *_ = _mk(tmp_path)
        await a.close()
        for _ in range(2):
            with pytest.raises(StopAsyncIteration):
                await _next(a.observations())
        with pytest.raises(StopAsyncIteration):
            await _next(a.acks())

    asyncio.run(go())


def test_bad_record_and_failing_health_do_not_kill_pump(tmp_path: Path) -> None:
    async def go() -> None:
        a, _, _, _ = _mk(tmp_path)
        calls = 0
        real = a._asm.on_record

        def flaky(rec: object) -> list[IntentAck | Observation]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("bad record")
            return real(rec)  # type: ignore[arg-type]

        def bad_health(*_: object) -> None:
            raise RuntimeError("health down")

        a._asm.on_record = flaky  # type: ignore[method-assign]
        a._health = bad_health  # type: ignore[assignment]
        _append(tmp_path, hello("2231.01.01"), hb("2231.01.01", 1))
        obs = await _next(a.observations())
        assert obs.m == 1
        await a.close()

    asyncio.run(go())


def test_raising_health_does_not_drop_lines_and_parser_crash_is_health(tmp_path: Path) -> None:
    class Boom(Parser):
        async def parse(self, save_path: str, *, sections: tuple[str, ...] | None = None) -> ParseResult | None:
            raise RuntimeError("parser crashed")

    def bad_health(k: HealthKind, d: str | None) -> None:
        raise RuntimeError("bus down")

    a = LiveAdapter(_paths(tmp_path), Parser(_result()), Sink(), health=bad_health)
    _append(tmp_path, hello("2231.01.01", schema=1), hb("2231.01.01", 1))
    with (tmp_path / "game.log").open("a") as f:
        f.write("SAE|v9|HB|2231.01.01|m=1\nSAE|v1|BOGUS|2231.01.01\n")
    _append(tmp_path, hb("2231.02.01", 2))
    kinds = [o.m for o in a._poll_once() if isinstance(o, Observation)]
    assert kinds == [1, 2]

    ev: list[tuple[HealthKind, str | None]] = []
    b = LiveAdapter(_paths(tmp_path / "b"), Boom(_result()), Sink(), health=lambda k, d: ev.append((k, d)))

    async def go() -> None:
        b._start_parse(tmp_path / "x.sav")
        await asyncio.gather(b._parse_task, return_exceptions=True)  # type: ignore[arg-type]
        b._apply_parse()

    asyncio.run(go())
    assert any(k is HealthKind.PARSE_FAILED and "RuntimeError" in (d or "") for k, d in ev)
