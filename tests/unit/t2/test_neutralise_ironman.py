"""N3 neutralise on close / mp=1 and N6 ironman observe-only (T2 Wave 3, proposal 017)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sae.adapters.ini_writer import IniWriter, neutralise
from sae.contracts.c2_ini import KnobCommand, parse_ini
from sae.contracts.c4_parse import ParseResult
from sae.contracts.c5_bus import HealthKind

from ._asm_helpers import hb, hello
from .test_live import Parser, _append, _batch, _mk, _next, _result


def _ini(tmp: Path) -> Path:
    return tmp / "sae_in.ini"


def test_close_neutralises_stale_ini(tmp_path: Path) -> None:
    async def go() -> None:
        a, _, ev, _ = _mk(tmp_path)
        assert (await a.emit(_batch())).ok
        assert parse_ini(_ini(tmp_path).read_text()).countries
        await a.close()
        assert _ini(tmp_path).exists()
        ini = parse_ini(_ini(tmp_path).read_text())
        assert not ini.countries and ini.knobs == KnobCommand(emit_period=1, quiet=0)
        assert (HealthKind.INI_NEUTRALISED, "close") in ev
        assert await a.neutralise()  # after close: safe, idempotent
        assert _ini(tmp_path).exists()

    asyncio.run(go())


def test_knobs_from_latest_hb_any_epoch(tmp_path: Path) -> None:
    async def go() -> None:
        a, _, _, _ = _mk(tmp_path)
        _ini(tmp_path).write_text("stale\n")
        _append(tmp_path, hello("2231.01.01").model_copy(update={"period": 3, "quiet": 1}))
        await _next(a.observations()) if False else await asyncio.sleep(0)
        it = a.observations()
        _append(tmp_path, hb("2231.01.01", 5).model_copy(update={"period": 4, "quiet": 1}))
        await _next(it)
        await a.close()
        assert parse_ini(_ini(tmp_path).read_text()).knobs == KnobCommand(emit_period=4, quiet=1)

    asyncio.run(go())


def test_no_file_and_release_noop(tmp_path: Path) -> None:
    async def go() -> None:
        a, *_ = _mk(tmp_path)
        await a.close()
        assert not _ini(tmp_path).exists()  # nothing to neutralise, nothing created
        r, _, ev, _ = _mk(tmp_path / "r", release=True)
        _ini(tmp_path / "r").write_text("x\n")
        await r.close()
        assert not await r.neutralise()
        assert _ini(tmp_path / "r").read_text() == "x\n" and not ev

    asyncio.run(go())


def test_neutralise_write_failure_health(tmp_path: Path) -> None:
    async def go() -> None:
        ev: list[tuple[HealthKind, str | None]] = []

        def boom(_a: str | Path, _b: str | Path) -> None:
            raise PermissionError("locked")

        async def nosleep(_s: float) -> None:
            return None

        def hk(k: HealthKind, d: str | None) -> None:
            ev.append((k, d))

        w = IniWriter(_ini(tmp_path), replace=boom, sleep=nosleep, health=hk)
        assert not await neutralise(w, KnobCommand(emit_period=2, quiet=1), "multiplayer", hk)
        assert len(ev) == 1 and ev[0][0] is HealthKind.INI_WRITE_FAILED
        assert (ev[0][1] or "").startswith("neutralise multiplayer: PermissionError")

    asyncio.run(go())


def test_mp_hello_neutralises_once(tmp_path: Path) -> None:
    async def go() -> None:
        a, _, ev, _ = _mk(tmp_path)
        assert (await a.emit(_batch())).ok
        _append(tmp_path, hello("2231.01.01").model_copy(update={"mp": 1}), hb("2231.01.01", 5))
        it = a.observations()
        await _next(it)
        assert not parse_ini(_ini(tmp_path).read_text()).countries
        assert ev.count((HealthKind.INI_NEUTRALISED, "multiplayer")) == 1
        _append(tmp_path, hb("2231.02.01", 6))
        await _next(it)
        assert ev.count((HealthKind.INI_NEUTRALISED, "multiplayer")) == 1
        await a.close()

    asyncio.run(go())


def _meta(ironman: bool | None) -> ParseResult:
    r = _result()
    return r.model_copy(update={"meta": r.meta.model_copy(update={"ironman": ironman})})


def test_ironman_latches_observe_only(tmp_path: Path) -> None:
    async def go(ironman: bool | None) -> None:
        tmp = tmp_path / str(ironman)
        p = Parser(_meta(ironman))
        a, _, ev, _ = _mk(tmp, p)
        save = tmp / "saves" / "c1" / "ironman.sav"
        a._start_parse(save)
        assert await a._parse_task is None  # type: ignore[misc]  # never fed to the assembler
        assert p.calls == [str(save)] and p.sections == [("meta",)]
        assert a.ironman_detected is bool(ironman)
        if ironman:
            assert ev == [(HealthKind.IRONMAN_OBSERVE_ONLY, "ironman: observe only")]
            r = await a.emit(_batch())
            assert not r.ok and r.error == "ironman"
            _ini(tmp).write_text("x\n")
            assert not await a.neutralise("multiplayer")  # mp rewrite stays blocked under Ironman
            assert _ini(tmp).read_text() == "x\n"
            await a.close()  # maintainer decision: the close rewrite still happens under Ironman
            assert parse_ini(_ini(tmp).read_text()).countries == ()
            assert ev[-1] == (HealthKind.INI_NEUTRALISED, "close")
        else:
            assert not ev and (await a.emit(_batch())).ok

    for v in (True, False, None):
        asyncio.run(go(v))


def test_autosave_uses_full_parse(tmp_path: Path) -> None:
    async def go() -> None:
        p = Parser(_result())
        a, *_ = _mk(tmp_path, p)
        a._start_parse(tmp_path / "saves" / "c1" / "autosave_2231.sav")
        await a._parse_task  # type: ignore[misc]
        assert p.sections == [None]

    asyncio.run(go())
