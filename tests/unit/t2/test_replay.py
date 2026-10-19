"""ReplayAdapter: determinism, from/stop_at, ack models, pacing, fog (T2 U5). No real sleeps, tmp_path only."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from sae.adapters.replay import ReplayAdapter, parse_speed
from sae.adapters.simulator import ModSimulator, SimCountry
from sae.contracts.c1_log import AckRecord, format_log_line
from sae.contracts.c3_intent import IntentBatch
from sae.contracts.c4_parse import PARSE_OUTPUT_ADAPTER, ParseResult
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import AdapterMode, GameAdapter, IntentAck, Observation
from sae.contracts.common import AckCode, AckStatus

from ._asm_helpers import Sink

FIXTURE = Path(__file__).resolve().parents[3] / "parser" / "fixtures" / "balanced" / "expected.json"
SENTINEL = 777777.0
Events = list[tuple[HealthKind, str | None]]


def fake_result(date: str = "2200.06.01") -> ParseResult:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["meta"] = {**raw["meta"], "date": date}
    raw |= {
        "parser_version": "t",
        "save": {"path": "x.sav", "size_bytes": 1, "mtime_ns": 1},
        "cost": {"rss_peak_bytes": 1, "cpu_s": 0, "wall_s": 0},
    }
    out = PARSE_OUTPUT_ADAPTER.validate_python(raw)
    assert isinstance(out, ParseResult)
    return out


class FakeParser:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def parse(self, save_path: str, *, sections: tuple[str, ...] | None = None) -> ParseResult | None:
        self.calls.append(Path(save_path).name)
        return fake_result()


def build_session(
    root: Path,
    months: int = 12,
    *,
    save_after: int = 6,
    extra_lines: dict[int, str] | None = None,
    dirty: bool = False,
) -> Path:
    """A LiveAdapter-format session: 1 human + 2 managed AIs, one chunk and one ts line per 3 months."""
    sim = ModSimulator(
        [SimCountry(1, "Human", is_ai=False, fleet=SENTINEL)]
        + [SimCountry(i, f"AI{i}", contact_with_human=True) for i in (2, 3)]
    )
    d = root / "session"
    (d / "saves").mkdir(parents=True)
    log = b""
    ts: list[str] = []
    for i in range(months):
        date = f"2200.{i + 1:02d}.01"
        lines = sim.pulse(date)
        if extra_lines and i in extra_lines:
            lines.append(extra_lines[i])
        log += "".join(f"{ln}\n" for ln in lines).encode()
        if dirty and i == 0:
            log += b"noise \xff\xfe not utf-8\n"  # invalid bytes must not shift later byte offsets
        if (i + 1) % 3 == 0:
            ts.append(f"{len(log)} {(i + 1) * 1_000_000_000}")
        if i + 1 == save_after:
            (d / "saves" / f"{len(log):012d}-autosave.sav").write_bytes(b"x")
    (d / "game.log").write_bytes(log)
    (d / "game.log.ts").write_text("\n".join(ts) + "\n", encoding="utf-8")
    return d


def make(
    root: Path,
    session: Path,
    *,
    sleeps: list[float] | None = None,
    **kw: object,
) -> tuple[ReplayAdapter, Sink, Events, FakeParser]:
    sink, ev, parser = Sink(), [], FakeParser()

    async def sleep(s: float) -> None:
        if sleeps is not None:
            sleeps.append(s)

    a = ReplayAdapter(
        session, parser, sink, health=lambda k, d: ev.append((k, d)), scratch_dir=root / "scratch", sleep=sleep,
        **kw,  # type: ignore[arg-type]
    )
    return a, sink, ev, parser


def batch(n: int, m: int, date: str) -> IntentBatch:
    return IntentBatch.model_validate(
        {
            "seq": n,
            "issued_date": date,
            "issued_m": m,
            "intents": [
                {
                    "intent_id": f"i-{n:06d}",
                    "decision_id": "d-0001.01-01",
                    "country": 2,
                    "kind": "SET_ECON_PLAN",
                    "plan": "research",
                    "reason_key": "baseline",
                }
            ],
        }
    )


async def drive(a: GameAdapter, *, emit: bool = True) -> tuple[list[Observation], list[IntentAck]]:
    """Consume both iterators; the fake engine emits one batch per observation."""
    obs: list[Observation] = []
    acks: list[IntentAck] = []

    async def collect() -> None:
        async for ack in a.acks():
            acks.append(ack)

    task = asyncio.create_task(collect())
    n = 0
    async for o in a.observations():
        obs.append(o)
        if emit and o.m is not None and o.source == "log":
            n += 1
            receipt = await a.emit(batch(n, o.m, o.game_date))
            assert receipt.ok, receipt
    await asyncio.wait_for(task, 1)
    return obs, acks


def digest(obs: list[Observation], acks: list[IntentAck]) -> str:
    h = hashlib.sha256()
    for o in obs:
        h.update(o.model_dump_json().encode())
    for k in acks:
        h.update(k.model_dump_json().encode())
    return h.hexdigest()


def run(root: Path, session: Path, **kw: object) -> tuple[list[Observation], list[IntentAck], Sink, Events]:
    a, sink, ev, _ = make(root, session, **kw)
    obs, acks = asyncio.run(drive(a))
    return obs, acks, sink, ev


def test_parse_speed() -> None:
    assert parse_speed("max") is None
    assert parse_speed("realtime") == 1.0
    assert parse_speed("2.5x") == 2.5
    for bad in ("bogus", "0x", "x", "-1x", ""):
        with pytest.raises(ValueError):
            parse_speed(bad)


def test_mode_and_protocol(tmp_path: Path) -> None:
    a, *_ = make(tmp_path, build_session(tmp_path))
    assert isinstance(a, GameAdapter)
    assert a.mode is AdapterMode.REPLAY


def test_determinism_echo(tmp_path: Path) -> None:
    s = build_session(tmp_path)
    o1, a1, _, _ = run(tmp_path / "r1", s)
    o2, a2, _, _ = run(tmp_path / "r2", s)
    assert digest(o1, a1) == digest(o2, a2)
    assert a1, "echo model must produce acks"
    assert all(k.status is AckStatus.APPLIED and k.code is AckCode.OK for k in a1)
    assert [o.source for o in o1].count("save") == 1
    assert [o.source for o in o1].count("log") == 12


def test_ack_before_its_observation(tmp_path: Path) -> None:
    """The ack applied at HB k is in the ack list before the observation of pulse k is yielded."""
    s = build_session(tmp_path)
    a, *_ = make(tmp_path, s)

    async def go() -> None:
        seen: list[str] = []

        async def collect() -> None:
            async for k in a.acks():
                seen.append(f"ack@{k.game_date}")

        t = asyncio.create_task(collect())
        n = 0
        async for o in a.observations():
            seen.append(f"obs@{o.game_date}")
            if o.source == "log" and o.m is not None:
                n += 1
                await a.emit(batch(n, o.m, o.game_date))
            await asyncio.sleep(0)
        await t
        assert seen.index("ack@2200.03.01") < seen.index("obs@2200.03.01")

    asyncio.run(go())


def test_from_and_stop_at(tmp_path: Path) -> None:
    s = build_session(tmp_path)
    obs, acks, _, _ = run(tmp_path / "r", s, from_="2200.06.01", stop_at="2200.08.01")
    assert min(o.game_date for o in obs) >= "2200.06.01"
    assert max(o.game_date for o in obs) == "2200.09.01"  # first observation past stop_at is the last
    assert all(k.game_date >= "2200.06.01" for k in acks)
    full, _, _, _ = run(tmp_path / "r2", s)
    assert len(obs) < len(full)


def test_bad_dates_and_models_rejected(tmp_path: Path) -> None:
    s = build_session(tmp_path)
    for kw in ({"from_": "nope"}, {"ack_model": "drop:2"}, {"ack_model": "delay:x"}, {"speed": "bogus"}):
        with pytest.raises(ValueError):
            make(tmp_path, s, **kw)


def test_ack_model_drop_all(tmp_path: Path) -> None:
    obs, acks, _, _ = run(tmp_path / "r", build_session(tmp_path), ack_model="drop:1.0")
    assert obs and acks == []


def test_ack_model_drop_partial_is_seeded(tmp_path: Path) -> None:
    s = build_session(tmp_path)
    runs = [run(tmp_path / f"r{i}", s, ack_model="drop:0.5", seed=3)[1] for i in range(2)]
    assert [k.seq for k in runs[0]] == [k.seq for k in runs[1]]
    full = run(tmp_path / "rf", s)[1]
    assert 0 < len(runs[0]) < len(full)


def test_ack_model_delay(tmp_path: Path) -> None:
    s = build_session(tmp_path)
    echo = run(tmp_path / "r0", s)[1]
    delayed = run(tmp_path / "r2", s, ack_model="delay:2")[1]
    assert echo[0].seq == delayed[0].seq == 1
    assert echo[0].game_date == "2200.02.01"  # emitted after HB 1, applied at the next HB
    assert delayed[0].game_date == "2200.04.01"  # two HBs later


def test_ack_model_from_recording(tmp_path: Path) -> None:
    rec = AckRecord(date="2200.04.01", sid=2, seq=99, status=AckStatus.REJECTED, code=AckCode.RANGE)
    s = build_session(tmp_path, extra_lines={3: format_log_line(rec)})
    obs, acks, _, _ = run(tmp_path / "r", s, ack_model="from_recording")
    assert obs and [(k.seq, k.status, k.code) for k in acks] == [(99, AckStatus.REJECTED, AckCode.RANGE)]
    # echo ignores the recorded ACK line and answers only the engine's own batches
    _, echo, _, _ = run(tmp_path / "r2", s)
    assert all(k.seq != 99 for k in echo)


def test_realtime_pacing_uses_ts_deltas(tmp_path: Path) -> None:
    s = build_session(tmp_path)  # ts: 3 s, 6 s, 9 s, 12 s since start
    sleeps: list[float] = []
    run(tmp_path / "r", s, speed="realtime", sleeps=sleeps)
    assert sleeps == [3.0, 3.0, 3.0]
    sleeps2: list[float] = []
    run(tmp_path / "r2", s, speed="3x", sleeps=sleeps2)
    assert sleeps2 == [1.0, 1.0, 1.0]
    sleeps3: list[float] = []
    run(tmp_path / "r3", s, speed="max", sleeps=sleeps3)
    assert sleeps3 == []


def test_invalid_utf8_does_not_shift_offsets(tmp_path: Path) -> None:
    clean, dirty = build_session(tmp_path / "c"), build_session(tmp_path / "d", dirty=True)
    sleeps: list[float] = []
    obs_d, _, _, _ = run(tmp_path / "r1", dirty, speed="realtime", sleeps=sleeps)
    obs_c, _, _, _ = run(tmp_path / "r2", clean)
    assert sleeps == [3.0, 3.0, 3.0]
    assert [(o.source, o.game_date) for o in obs_d] == [(o.source, o.game_date) for o in obs_c]


def test_idle_poll_waits_are_kept(tmp_path: Path) -> None:
    s = build_session(tmp_path)
    ts = (s / "game.log.ts").read_text().splitlines()
    off, ns = ts[0].split()
    idle = f"{off} {int(ns) + 500_000_000}"  # an idle poll at the same offset, 0.5 s later
    (s / "game.log.ts").write_text("\n".join([ts[0], idle, *ts[1:]]) + "\n")
    sleeps: list[float] = []
    run(tmp_path / "r", s, speed="realtime", sleeps=sleeps)
    assert sleeps[0] == pytest.approx(3.0 + 0.5 - 0.5 + 0.5 - 0.5, abs=1.0) and len(sleeps) == 3


def test_missing_ts_reports_no_ts_and_runs_at_max(tmp_path: Path) -> None:
    s = build_session(tmp_path)
    (s / "game.log.ts").unlink()
    sleeps: list[float] = []
    obs, _, _, ev = run(tmp_path / "r", s, speed="realtime", sleeps=sleeps)
    assert len(obs) == 13 and sleeps == []
    assert [d for k, d in ev if d == "no_ts"] == ["no_ts"]


def test_fog_no_god_values_in_observations(tmp_path: Path) -> None:
    obs, acks, sink, _ = run(tmp_path / "r", build_session(tmp_path))
    assert any(g.plyr.get("fleet") == SENTINEL for g in sink.got), "PLYR must reach the god sink"
    blob = "".join(o.model_dump_json() for o in obs) + "".join(k.model_dump_json() for k in acks)
    assert "777777" not in blob
    assert all(o.player_sid == 1 for o in obs if o.player_sid is not None)


def test_unreadable_log_reports_and_ends(tmp_path: Path) -> None:
    s = build_session(tmp_path)
    (s / "game.log").unlink()
    obs, acks, _, ev = run(tmp_path / "r", s)
    assert obs == [] and acks == []
    assert ev and ev[0][0] is HealthKind.INVALID_EVENT


def test_emit_writes_only_scratch_and_close_semantics(tmp_path: Path) -> None:
    s = build_session(tmp_path)
    a, *_ = make(tmp_path, s)

    async def go() -> None:
        r = await a.emit(batch(1, 1, "2200.01.01"))
        assert r.ok and r.path is not None and Path(r.path).parent == tmp_path / "scratch"
        assert not list(s.glob("*.ini"))
        await a.close()
        await a.close()  # idempotent
        r2 = await a.emit(batch(2, 1, "2200.01.01"))
        assert not r2.ok and r2.error == "closed"
        assert [o async for o in a.observations()] == []
        assert [k async for k in a.acks()] == []

    asyncio.run(go())


def test_close_ends_acks_while_waiting(tmp_path: Path) -> None:
    a, *_ = make(tmp_path, build_session(tmp_path))

    async def go() -> None:
        t = asyncio.create_task(_drain(a))
        await asyncio.sleep(0)
        await a.close()
        assert await asyncio.wait_for(t, 1) == []

    async def _drain(x: GameAdapter) -> list[IntentAck]:
        return [k async for k in x.acks()]

    asyncio.run(go())



async def loopish(a: GameAdapter) -> tuple[list[Observation], list[IntentAck], int]:
    """Driver shaped like T4's loop.py: feeders pull into one unbounded queue, one consumer emits."""
    q: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    obs: list[Observation] = []
    acks: list[IntentAck] = []
    emits = 0

    async def feed_obs() -> None:
        async for o in a.observations():
            q.put_nowait(("obs", o))
        q.put_nowait(("end", None))

    async def feed_acks() -> None:
        async for k in a.acks():
            q.put_nowait(("ack", k))

    async def consume() -> None:
        nonlocal emits
        while True:
            kind, item = await q.get()
            if kind == "end":
                return
            if kind == "ack":
                assert isinstance(item, IntentAck)
                acks.append(item)
            else:
                assert isinstance(item, Observation)
                obs.append(item)
                if item.source == "log" and item.m is not None:
                    emits += 1
                    assert (await a.emit(batch(emits, item.m, item.game_date))).ok

    await asyncio.wait_for(asyncio.gather(consume(), feed_obs(), feed_acks()), 5)
    return obs, acks, emits


def test_loop_shaped_driver_gets_acks_and_is_deterministic(tmp_path: Path) -> None:
    """Regression (proposal 011-T2): a max-speed walk must not outrun an unbounded-queue consumer."""
    s = build_session(tmp_path)
    runs = []
    for i in range(2):
        a, *_ = make(tmp_path / f"r{i}", s)
        o, k, n = asyncio.run(loopish(a))
        assert n == 12 and len(k) > 0
        runs.append(digest(o, k))
    assert runs[0] == runs[1]


def test_multiplayer_hello_blocks_emit(tmp_path: Path) -> None:
    s = build_session(tmp_path, months=3)
    log = (s / "game.log").read_bytes()
    assert b"|mp=0" in log
    (s / "game.log").write_bytes(log.replace(b"|mp=0", b"|mp=1", 1))  # the session's HELLO says multiplayer
    (s / "game.log.ts").unlink()
    a, *_ = make(tmp_path, s)
    errors: list[str | None] = []

    async def go() -> None:
        async for o in a.observations():
            if o.m is not None:
                errors.append((await a.emit(batch(1, o.m, o.game_date))).error)

    asyncio.run(go())
    assert errors and set(errors) == {"multiplayer"}
    assert not (tmp_path / "scratch" / "sae_in.ini").exists()
