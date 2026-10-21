"""GovernedSaveParser: gating, classification, kill, busy, fog rule 12."""

from __future__ import annotations

import asyncio
from typing import Any

from sae.contracts.c4_parse import Cost, GodView, ParseError, ParseResult, SaveInfo, SaveMeta
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c8_config import GovernorConfig, SaeConfig
from sae.daemon.saveparser import GovernedSaveParser

from .fakes import FakeGovernor, FakePlatform, FakeWorker, decision, make_deps

COST = Cost(rss_peak_bytes=1, cpu_s=0.1, wall_s=0.1)


def _ok_json(god: bool = True) -> bytes:
    r = ParseResult(
        parser_version="t", save=SaveInfo(path="x.sav", size_bytes=1, mtime_ns=1),
        meta=SaveMeta(game_version="v", date="2210.01.01"), countries=(), self_state={}, legal=(),
        god=GodView(fleet_power=1, systems_owned=1, tech_researched=1, alloys_income=1, fortified_starbases=1)
        if god else None,
        sections=("meta",), cost=COST,
    )
    return r.model_dump_json().encode() + b"\n"


def _err_json(code: int = 5) -> bytes:
    kind = {2: "usage", 3: "input", 4: "format", 5: "parse", 6: "unstable"}[code]
    return ParseError(parser_version="t", exit_code=code, error=kind, detail="d").model_dump_json().encode()  # type: ignore[arg-type]


class Rig:
    def __init__(self, *workers: FakeWorker, dec: Any = None, cfg: SaeConfig | None = None,
                 boundary: str | None = None, now: str | None = None) -> None:
        self.platform = FakePlatform(*workers)
        self.gov = FakeGovernor()
        self.health: list[tuple[HealthKind, str | None, dict[str, float]]] = []
        self.p = GovernedSaveParser(
            make_deps(self.platform, self.gov), cfg or SaeConfig(),
            lambda k, d, m: self.health.append((k, d, m)), lambda: boundary, lambda: now,
        )
        self.p.set_decision(dec or decision())

    def kinds(self) -> list[HealthKind]:
        return [h[0] for h in self.health]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_ok() -> None:
    r = Rig(FakeWorker(0, _ok_json()))
    res = run(r.p.parse("a.sav"))
    assert isinstance(res, ParseResult)
    assert [o.kind for o in r.gov.outcomes] == ["ok"] and r.health == []
    spec = r.platform.specs[0]
    assert spec.args[:3] == ("parse", "--save", "a.sav") and spec.threads == 2


def test_every_n() -> None:
    r = Rig(FakeWorker(0, _ok_json()), FakeWorker(0, _ok_json()), dec=decision(parse_every_n=2))
    out = [run(r.p.parse("a.sav")) for _ in range(4)]
    assert [o is not None for o in out] == [False, True, False, True]
    assert r.kinds() == [HealthKind.SAVE_SKIPPED] * 2


def test_n_zero() -> None:
    r = Rig(dec=decision(parse_every_n=0))
    assert run(r.p.parse("a.sav")) is None
    assert r.kinds() == [HealthKind.SAVE_SKIPPED] and r.gov.outcomes == []


def test_window_inside_and_outside() -> None:
    d = decision(parse_window_only=True)
    inside = Rig(FakeWorker(0, _ok_json()), dec=d, boundary="2210.06.01", now="2210.04.01")
    assert run(inside.p.parse("a")) is not None
    cases = (("2209.01.01", "2210.06.01"), (None, "2210.06.01"), ("2210.04.01", None), ("2210.07.01", "2210.06.01"))
    for now, b in cases:
        out = Rig(dec=d, boundary=b, now=now)
        assert run(out.p.parse("a")) is None
        assert out.kinds() == [HealthKind.SAVE_SKIPPED]


def test_timeout_kills() -> None:
    cfg = SaeConfig(governor=GovernorConfig(parse_timeout_s=0.01))
    w = FakeWorker(None, hang=True)
    r = Rig(w, cfg=cfg)
    assert run(r.p.parse("a")) is None
    assert w.killed and r.gov.outcomes[0].kind == "budget_breach"
    assert r.kinds() == [HealthKind.BUDGET_BREACH]


def test_clean_failure() -> None:
    r = Rig(FakeWorker(5, _err_json(5)))
    assert run(r.p.parse("a")) is None
    assert r.gov.outcomes[0].kind == "clean_failure" and r.gov.outcomes[0].exit_code == 5
    assert r.kinds() == [HealthKind.PARSE_FAILED]


def test_garbage_and_wrong_exit() -> None:
    for w in (FakeWorker(0, b"garbage"), FakeWorker(7, _err_json(5)), FakeWorker(5, _ok_json()), FakeWorker(None, b"")):
        r = Rig(w)
        assert run(r.p.parse("a")) is None
        assert r.gov.outcomes[0].kind == "budget_breach" and r.kinds() == [HealthKind.BUDGET_BREACH]


def test_oversized_stdout() -> None:
    r = Rig(FakeWorker(0, b" " * (4 * 1024 * 1024 + 1)))
    assert run(r.p.parse("a")) is None
    assert r.gov.outcomes[0].kind == "budget_breach"
    assert r.health[0][2]["stdout_bytes"] > 4 * 1024 * 1024


def test_kill_by_governor() -> None:
    w = FakeWorker(None, hang=True)
    r = Rig(w)

    async def go() -> Any:
        t = asyncio.create_task(r.p.parse("a"))
        await asyncio.sleep(0.01)
        r.p.kill_current()
        return await t

    assert run(go()) is None
    assert w.killed and r.gov.outcomes[0].kind == "killed_by_governor"
    assert r.kinds() == [HealthKind.WORKER_KILLED]


def test_busy_skips() -> None:
    w = FakeWorker(None, hang=True)
    r = Rig(w)

    async def go() -> Any:
        t = asyncio.create_task(r.p.parse("a"))
        await asyncio.sleep(0.01)
        second = await r.p.parse("b")
        r.p.kill_current()
        await t
        return second

    assert run(go()) is None
    assert HealthKind.SAVE_SKIPPED in r.kinds() and len(r.platform.specs) == 1


def test_no_god_text_in_health() -> None:
    cases = (FakeWorker(0, _ok_json(True) + b"x"), FakeWorker(3, _ok_json(True)), FakeWorker(0, b'{"god": {"bad"'))
    for w in cases:
        r = Rig(w)
        run(r.p.parse("a"))
        assert r.health and all('"god"' not in str(h) and "fleet_power" not in str(h) for h in r.health)


def test_spawn_failure_never_raises() -> None:
    r = Rig()  # no workers: spawn raises IndexError
    assert run(r.p.parse("a")) is None
    assert r.kinds() == [HealthKind.PARSE_FAILED]


def test_cancel_kills_worker() -> None:
    w = FakeWorker(None, hang=True)
    r = Rig(w)

    async def go() -> None:
        t = asyncio.create_task(r.p.parse("x.sav"))
        await asyncio.sleep(0.02)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            return
        raise AssertionError("not cancelled")

    run(go())
    assert w.killed and r.p._handle is None


def test_busy_skip_does_not_advance_every_n() -> None:
    w1, w2 = FakeWorker(None, hang=True), FakeWorker(0, _ok_json())
    r = Rig(w1, w2, dec=decision(parse_every_n=2))

    async def go() -> ParseResult | None:
        await r.p.parse("a.sav")  # eligible 1: every_n skip
        t = asyncio.create_task(r.p.parse("b.sav"))  # eligible 2: runs, hangs
        await asyncio.sleep(0.02)
        for _ in range(3):
            await r.p.parse("c.sav")  # busy: must not count
        w1.kill()
        await t
        await r.p.parse("d.sav")  # eligible 3: every_n skip
        return await r.p.parse("e.sav")  # eligible 4: runs

    assert isinstance(run(go()), ParseResult)
    assert r.kinds().count(HealthKind.SAVE_SKIPPED) == 5


def test_sections_passed_to_sae_parse_and_bypass_every_n() -> None:
    """Proposal 017 / C4 section 1: `parse(path, sections=("meta",))` -> `--sections meta`; a probe skips the gate."""
    r = Rig(FakeWorker(0, _ok_json()), FakeWorker(0, _ok_json()), dec=decision(parse_every_n=3))
    assert run(r.p.parse("ironman.sav", sections=("meta",))) is not None
    assert run(r.p.parse("ironman.sav", sections=("meta", "countries"))) is not None
    a0, a1 = r.platform.specs[0].args, r.platform.specs[1].args
    assert a0[:5] == ("parse", "--save", "ironman.sav", "--sections", "meta")
    assert a1[3:5] == ("--sections", "meta,countries")
    assert run(r.p.parse("a.sav")) is None  # full parses still gated: the probes did not advance every_n
    assert "--sections" not in Rig(FakeWorker(0, _ok_json())).platform.specs  # untouched default


def test_sections_probe_still_respects_n_zero() -> None:
    r = Rig(dec=decision(parse_every_n=0))
    assert run(r.p.parse("ironman.sav", sections=("meta",))) is None and r.platform.specs == []


def test_ok_parse_keeps_cost_for_telemetry() -> None:
    r = Rig(FakeWorker(0, _ok_json()))
    assert r.p.last_cost is None
    assert run(r.p.parse("a.sav")) is not None
    assert r.p.last_cost == COST
