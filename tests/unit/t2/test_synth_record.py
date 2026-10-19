"""Synth session recorder (proposal 015): file format, CLI guard, error handling, record -> replay equivalence."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from sae.adapters import cli
from sae.adapters.replay import ReplayAdapter
from sae.adapters.synth import SynthAdapter
from sae.contracts.c5_bus import HealthKind, StrategySelectedPayload
from sae.contracts.c6_adapter import GameAdapter, IntentAck, Observation
from sae.contracts.c8_config import SaeConfig
from sae.contracts.common import Archetype
from sae.core import engine

from ._asm_helpers import Sink
from .test_synth import EXE, BinaryParser, NullParser, drive, make

needs_exe = pytest.mark.skipif(
    not EXE.exists(), reason="parser/target/debug/sae-parse not built (cd parser && cargo build -j 1)"
)


def _rec(tmp_path: Path, **kw: object) -> tuple[SynthAdapter, list[tuple[HealthKind, str | None]], Path]:
    rec = tmp_path / "rec"
    ev: list[tuple[HealthKind, str | None]] = []
    a = SynthAdapter(
        [(Archetype.TURTLE, 0)], 1, 1, NullParser(), Sink(),
        health=lambda k, d: ev.append((k, d)), parser_exe="sae-parse", scratch_dir=tmp_path / "scratch",
        run_fixture=_ok, record_dir=rec, **kw,  # type: ignore[arg-type]
    )
    return a, ev, rec


async def _ok(argv: list[str]) -> int:
    Path(argv[argv.index("--out") + 1]).write_bytes(b"SAVE")
    return 0


def test_record_files_format(tmp_path: Path) -> None:
    a, ev, rec = _rec(tmp_path)
    asyncio.run(drive(a))
    data = (rec / "game.log").read_bytes()
    assert data.endswith(b"\n") and b"\r" not in data
    assert all(ln.startswith(b"SAE|v1|") for ln in data.split(b"\n")[:-1])
    stamps = [tuple(map(int, ln.split())) for ln in (rec / "game.log.ts").read_text().splitlines()]
    assert [ns for _, ns in stamps] == [i * 1_000_000_000 for i in range(1, 13)]
    sizes = [s for s, _ in stamps]
    assert sizes == sorted(sizes) and sizes[-1] == len(data)
    saves = sorted(p.name for p in (rec / "saves").iterdir())
    assert len(saves) == 4 and all(re.fullmatch(r"\d{12}-autosave_\d{4}\.\d{2}\.\d{2}\.sav", n) for n in saves)
    assert {int(n[:12]) for n in saves} <= set(sizes)
    assert (rec / "saves" / saves[0]).read_bytes() == b"SAVE"
    assert not ev


def test_record_error_is_one_invalid_event_and_run_continues(tmp_path: Path) -> None:
    a, ev, rec = _rec(tmp_path)
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text("a file, so mkdir/open fails")
    obs, _ = asyncio.run(drive(a))
    assert len([o for o in obs if o.source == "log"]) == 12
    assert [e for e in ev if e[0] is HealthKind.INVALID_EVENT].__len__() == 1


def test_no_record_dir_writes_nothing(tmp_path: Path) -> None:
    a, *_ = make(tmp_path, [(Archetype.TURTLE, 0)], years=1)
    asyncio.run(drive(a))
    assert not (tmp_path / "rec").exists()


def test_cli_record_dir_must_be_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    full = tmp_path / "full"
    full.mkdir()
    (full / "x").write_text("x")
    assert cli.main("synth", ["--record", str(full)]) == 2
    assert "not empty" in capsys.readouterr().err
    f = tmp_path / "afile"
    f.write_text("x")
    assert cli.main("synth", ["--record", str(f)]) == 2


# -- record -> replay equivalence -------------------------------------------------------------------


async def _engine_run(a: GameAdapter) -> list[tuple[str, str]]:
    """Loop-shaped driver over T4's pure engine; returns (game_date, strategy) of every strategy_selected."""
    ctx = engine.EngineCtx(mode=a.mode, cfg=SaeConfig().engine)
    state = engine.initial_state(ctx.cfg)
    q: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    out: list[tuple[str, str]] = []

    async def feed_obs() -> None:
        async for o in a.observations():
            q.put_nowait(("obs", o))
        q.put_nowait(("end", None))

    async def feed_acks() -> None:
        async for k in a.acks():
            q.put_nowait(("ack", k))

    t = [asyncio.create_task(feed_obs()), asyncio.create_task(feed_acks())]
    while True:
        kind, item = await q.get()
        if kind == "end":
            break
        if kind == "ack":
            assert isinstance(item, IntentAck)
            state, o = engine.on_ack(state, item)
        else:
            assert isinstance(item, Observation)
            state, o = engine.on_observation(state, item, ctx)
            if o.batch is not None:
                state, more = engine.on_emitted(state, o.batch, await a.emit(o.batch))
                o = type(o)(drafts=(*o.drafts, *more.drafts), batch=None)
        for d in o.drafts:
            if d.type == "strategy_selected":
                assert isinstance(d.payload, StrategySelectedPayload)
                out.append((d.game_date or "", d.payload.strategy.value))
    await asyncio.gather(*t)
    return out


@needs_exe
def test_five_year_turtle_record_replay_same_strategy_sequence(tmp_path: Path) -> None:
    rec = tmp_path / "rec"
    synth = SynthAdapter(
        [(Archetype.TURTLE, 0)], 7, 5, BinaryParser(), Sink(),
        parser_exe=str(EXE), scratch_dir=tmp_path / "s1", record_dir=rec,
    )
    live = asyncio.run(_engine_run(synth))
    replay = ReplayAdapter(
        rec, BinaryParser(), Sink(), scratch_dir=tmp_path / "s2", ack_model="from_recording",
    )
    again = asyncio.run(_engine_run(replay))
    assert len(live) >= 60
    assert live == again
