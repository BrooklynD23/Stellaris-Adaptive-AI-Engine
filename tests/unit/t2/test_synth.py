"""SynthAdapter: strict specs per archetype, determinism, fog, real-binary run (T2 U5). tmp_path only."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from sae.adapters.synth import PROFILES, SynthAdapter, parse_schedule
from sae.contracts.c4_parse import PARSE_OUTPUT_ADAPTER, FixtureSpec, ParseResult
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import AdapterMode, GameAdapter, IntentAck, Observation
from sae.contracts.common import AckStatus, Archetype

from ._asm_helpers import Sink
from .test_replay import FakeParser, batch, digest, loopish

EXE = Path(__file__).resolve().parents[3] / "parser" / "target" / "debug" / "sae-parse"
Events = list[tuple[HealthKind, str | None]]


class NullParser:
    async def parse(self, save_path: str, *, sections: tuple[str, ...] | None = None) -> ParseResult | None:
        return None


async def ok_fixture(argv: list[str]) -> int:
    return 0


def make(
    root: Path,
    schedule: list[tuple[Archetype, int]],
    *,
    seed: int = 1,
    years: int = 2,
    parser: object | None = None,
    run_fixture: object = ok_fixture,
    ack_model: str = "echo",
    exe: str = "sae-parse",
) -> tuple[SynthAdapter, Sink, Events]:
    sink, ev = Sink(), []
    kw: dict[str, object] = {} if run_fixture is None else {"run_fixture": run_fixture}
    a = SynthAdapter(
        schedule, seed, years, parser or NullParser(), sink,  # type: ignore[arg-type]
        health=lambda k, d: ev.append((k, d)), parser_exe=exe, scratch_dir=root / "scratch",
        ack_model=ack_model, **kw,  # type: ignore[arg-type]
    )
    return a, sink, ev


async def drive(a: GameAdapter, *, emit: bool = True) -> tuple[list[Observation], list[IntentAck]]:
    obs: list[Observation] = []
    acks: list[IntentAck] = []

    async def collect() -> None:
        async for k in a.acks():
            acks.append(k)

    t = asyncio.create_task(collect())
    n = 0
    async for o in a.observations():
        obs.append(o)
        if emit and o.source == "log" and o.m is not None:
            n += 1
            assert (await a.emit(batch(n, o.m, o.game_date))).ok
    await asyncio.wait_for(t, 1)
    return obs, acks


def test_parse_schedule() -> None:
    assert parse_schedule("balanced") == [(Archetype.BALANCED, 0)]
    assert parse_schedule("turtle@0,tech_rush@5") == [(Archetype.TURTLE, 0), (Archetype.TECH_RUSH, 5)]
    for bad in ("nope", "turtle@x", "turtle@-1", "", "turtle,,balanced"):
        with pytest.raises(ValueError):
            parse_schedule(bad)


def test_rejects_bad_construction(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make(tmp_path, [], years=1)
    with pytest.raises(ValueError):
        make(tmp_path, [(Archetype.TURTLE, 0)], years=0)
    with pytest.raises(ValueError):
        make(tmp_path, [(Archetype.TURTLE, 0)], ack_model="from_recording")


@pytest.mark.parametrize("arch", list(Archetype))
def test_specs_validate_strictly(tmp_path: Path, arch: Archetype) -> None:
    a, _, ev = make(tmp_path, [(arch, 0)])
    obs, _ = asyncio.run(drive(a))
    assert a.mode is AdapterMode.SYNTH and isinstance(a, GameAdapter)
    assert len(a.specs) == 8  # 24 months / 3
    for spec in a.specs:
        FixtureSpec.model_validate_json(spec.model_dump_json())  # strict JSON types
    assert [o.source for o in obs].count("log") == 24
    assert not [e for e in ev if e[0] is HealthKind.PARSE_FAILED]
    assert PROFILES[arch].borders >= 1


def test_archetype_trajectories_differ(tmp_path: Path) -> None:
    last = {}
    for arch in Archetype:
        a, _, _ = make(tmp_path / arch.value, [(arch, 0)], years=3)
        asyncio.run(drive(a, emit=False))
        last[arch] = a.specs[-1]

    def forts(s: FixtureSpec) -> int:
        return sum(1 for b in s.starbases if b.defense_modules + b.platforms > 0)

    def player(s: FixtureSpec) -> float:
        return next(c.fleet_power for c in s.countries if c.is_player)

    assert forts(last[Archetype.TURTLE]) > forts(last[Archetype.TECH_RUSH])
    assert last[Archetype.EARLY_AGGRESSION].wars and not last[Archetype.TURTLE].wars
    assert player(last[Archetype.EARLY_AGGRESSION]) > player(last[Archetype.TURTLE]) > player(last[Archetype.TECH_RUSH])
    tech = {k: next(c.tech_researched for c in s.countries if c.is_player) for k, s in last.items()}
    assert tech[Archetype.TECH_RUSH] > tech[Archetype.BALANCED]


def test_schedule_switches_archetype(tmp_path: Path) -> None:
    a, _, _ = make(tmp_path, [(Archetype.TURTLE, 0), (Archetype.EARLY_AGGRESSION, 1)], years=2)
    asyncio.run(drive(a, emit=False))
    assert a.specs[0].name.startswith("synth_turtle_") and a.specs[-1].name.startswith("synth_early_aggression_")
    assert not a.specs[0].wars and a.specs[-1].wars


def test_late_contact_ai(tmp_path: Path) -> None:
    a, _, _ = make(tmp_path, [(Archetype.BALANCED, 0)], years=3)
    asyncio.run(drive(a, emit=False))
    assert len(a.specs[0].contacts) == 3 and len(a.specs[-1].contacts) == 4


def test_determinism_and_seed_sensitivity(tmp_path: Path) -> None:
    def specs(seed: int, sub: str) -> list[str]:
        a, _, _ = make(tmp_path / sub, [(Archetype.BALANCED, 0)], seed=seed)
        asyncio.run(drive(a))
        return [s.model_dump_json() for s in a.specs]

    assert specs(5, "a") == specs(5, "b")
    assert specs(5, "a2") != specs(6, "c")


def test_observation_and_ack_sequences_deterministic(tmp_path: Path) -> None:
    def seq(sub: str) -> tuple[list[str], list[str]]:
        a, *_ = make(tmp_path / sub, [(Archetype.TURTLE, 0)], seed=2)
        o, k = asyncio.run(drive(a))
        return [x.model_dump_json() for x in o], [x.model_dump_json() for x in k]

    assert seq("a") == seq("b")
    acks = [IntentAck.model_validate_json(x) for x in seq("c")[1]]
    assert acks and all(k.status is AckStatus.APPLIED for k in acks)


def test_ack_models_in_synth(tmp_path: Path) -> None:
    def acks(model: str, sub: str) -> list[IntentAck]:
        a, *_ = make(tmp_path / sub, [(Archetype.TURTLE, 0)], ack_model=model, years=1)
        return asyncio.run(drive(a))[1]

    echo = acks("echo", "e")
    assert acks("drop:1.0", "d") == []
    delayed = acks("delay:2", "l")
    assert echo[0].seq == delayed[0].seq == 1
    assert delayed[0].game_date > echo[0].game_date


def test_fixture_failure_is_health_and_skipped(tmp_path: Path) -> None:
    async def bad(argv: list[str]) -> int:
        return 4

    a, _, ev = make(tmp_path, [(Archetype.TURTLE, 0)], years=1, run_fixture=bad, parser=FakeParser())
    obs, _ = asyncio.run(drive(a, emit=False))
    assert [o.source for o in obs].count("save") == 0
    assert len([e for e in ev if e[0] is HealthKind.PARSE_FAILED and "exit 4" in (e[1] or "")]) == 4


def test_fixture_runner_receives_cli_shape_and_cleans_up(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    async def spy(argv: list[str]) -> int:
        calls.append(argv)
        assert Path(argv[argv.index("--spec") + 1]).exists()
        return 0

    a, *_ = make(tmp_path, [(Archetype.TURTLE, 0)], years=1, run_fixture=spy, exe="/x/sae-parse")
    asyncio.run(drive(a, emit=False))
    assert [c[:2] for c in calls] == [["/x/sae-parse", "fixture"]] * 4
    assert all(c[2] == "--spec" and c[4] == "--out" for c in calls)
    assert not list((tmp_path / "scratch" / "synth").glob("*"))


def test_fog_no_player_values_in_observations(tmp_path: Path) -> None:
    a, sink, _ = make(tmp_path, [(Archetype.TECH_RUSH, 0)], years=1)
    obs, acks = asyncio.run(drive(a))
    player_fleets = {g.plyr["fleet"] for g in sink.got if "fleet" in g.plyr}
    assert len(sink.got) == 12 and player_fleets, "PLYR lines reach the god sink"
    for o in obs:
        assert all(m.fleet_power not in player_fleets for m in o.managed)
        assert o.player_sid == 1
    assert acks


def test_emit_close_semantics(tmp_path: Path) -> None:
    a, *_ = make(tmp_path, [(Archetype.TURTLE, 0)], years=1)

    async def go() -> None:
        r = await a.emit(batch(1, 1, "2200.01.01"))
        assert r.ok and Path(r.path or "").parent == tmp_path / "scratch"
        await a.close()
        await a.close()
        assert (await a.emit(batch(2, 1, "2200.01.01"))).error == "closed"
        assert [o async for o in a.observations()] == []
        assert [k async for k in a.acks()] == []

    asyncio.run(go())


class BinaryParser:
    """Runs the real ``sae-parse parse`` and validates its JSON with the C4 adapter."""

    async def parse(self, save_path: str, *, sections: tuple[str, ...] | None = None) -> ParseResult | None:
        proc = await asyncio.to_thread(
            subprocess.run, [str(EXE), "parse", "--save", save_path], capture_output=True, check=True
        )
        out = PARSE_OUTPUT_ADAPTER.validate_json(proc.stdout)
        assert isinstance(out, ParseResult)
        return out


@pytest.mark.skipif(not EXE.exists(), reason="parser/target/debug/sae-parse not built (cd parser && cargo build -j 1)")
def test_real_binary_archetypes_move_features(tmp_path: Path) -> None:
    final: dict[Archetype, Observation] = {}
    for arch in (Archetype.TURTLE, Archetype.TECH_RUSH):
        a, _, ev = make(
            tmp_path / arch.value, [(arch, 0)], years=1, parser=BinaryParser(), run_fixture=None, exe=str(EXE)
        )
        obs, _ = asyncio.run(drive(a, emit=False))
        saves = [o for o in obs if o.source == "save"]
        assert len(saves) == 4 and not [e for e in ev if e[0] is HealthKind.PARSE_FAILED]
        assert saves[-1].player_legal, "save observations carry player_legal"
        final[arch] = saves[-1]
    turtle, rush = final[Archetype.TURTLE].player_legal, final[Archetype.TECH_RUSH].player_legal

    def total(views: tuple[object, ...], field: str) -> int:
        return sum(int(getattr(v, field) or 0) for v in views)

    assert total(turtle, "fortified_border_starbases") > total(rush, "fortified_border_starbases")
    assert total(rush, "tech_cat") > total(turtle, "tech_cat")
    assert total(turtle, "fleet_power_cat") > total(rush, "fleet_power_cat")


def test_loop_shaped_driver_gets_acks_and_is_deterministic(tmp_path: Path) -> None:
    runs = []
    for i in range(2):
        a, *_ = make(tmp_path / f"r{i}", [(Archetype.TURTLE, 0)], years=1)
        o, k, n = asyncio.run(loopish(a))
        assert n == 12 and len(k) > 0
        runs.append(digest(o, k))
    assert runs[0] == runs[1]


def test_fixture_timeout_is_parse_failed(tmp_path: Path) -> None:
    async def hang(argv: list[str]) -> int:
        raise subprocess.TimeoutExpired(argv, 60)

    a, _, ev = make(tmp_path, [(Archetype.TURTLE, 0)], years=1, run_fixture=hang)
    obs, _ = asyncio.run(drive(a, emit=False))
    assert [o.source for o in obs].count("save") == 0
    assert len([e for e in ev if e[0] is HealthKind.PARSE_FAILED and "TimeoutExpired" in (e[1] or "")]) == 4
