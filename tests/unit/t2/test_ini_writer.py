from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from sae.adapters.ini_writer import IniWriter, emit_batch
from sae.contracts.c2_ini import parse_ini
from sae.contracts.c3_intent import IntentBatch
from sae.contracts.c5_bus import HealthKind

Events = list[tuple[HealthKind, str | None]]


def writer(path: Path, replace: object = None, sleeps: list[float] | None = None) -> tuple[IniWriter, Events]:
    ev: Events = []
    rec = sleeps if sleeps is not None else []

    async def sleep(s: float) -> None:
        rec.append(s)

    kw = {"replace": replace} if replace else {}
    return IniWriter(path, sleep=sleep, health=lambda k, d: ev.append((k, d)), **kw), ev  # type: ignore[arg-type]


def test_success_sha(tmp_path: Path) -> None:
    p = tmp_path / "sae_in.ini"
    w, ev = writer(p)
    r = asyncio.run(w.write("hello\n", 5))
    assert r.ok and r.bytes_written == 6 and r.sha256 == hashlib.sha256(b"hello\n").hexdigest()
    assert p.read_text() == "hello\n" and not (tmp_path / "sae_in.ini.tmp").exists() and not ev


def test_retry_schedule_then_success(tmp_path: Path) -> None:
    import os

    calls: list[int] = []

    def replace(a: str | Path, b: str | Path) -> None:
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("held by game")  # Windows semantics, mocked
        os.replace(a, b)

    sleeps: list[float] = []
    w, _ev = writer(tmp_path / "x.ini", replace, sleeps)
    assert asyncio.run(w.write("a", 1)).ok
    assert sleeps == [0.1, 0.2]


def test_all_fail_cleans_tmp(tmp_path: Path) -> None:
    def replace(a: str | Path, b: str | Path) -> None:
        raise PermissionError("held")

    sleeps: list[float] = []
    w, ev = writer(tmp_path / "x.ini", replace, sleeps)
    r = asyncio.run(w.write("a", 9))
    assert not r.ok and r.error == "write_failed" and r.seq == 9 and "PermissionError" in (r.detail or "")
    assert sleeps == [0.1, 0.2, 0.4]
    assert not (tmp_path / "x.ini.tmp").exists()
    assert [k for k, _ in ev] == [HealthKind.INI_WRITE_FAILED]


def _batch(**kw: object) -> IntentBatch:
    base: dict[str, object] = {
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
    return IntentBatch.model_validate(base | kw)


def test_emit_batch_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "sae_in.ini"
    w, _ = writer(p)
    r = asyncio.run(emit_batch(_batch(), w))
    assert r.ok and r.seq == 17
    assert parse_ini(p.read_text()).countries[0].sid == 3


def test_emit_batch_compile_failure(tmp_path: Path) -> None:
    from unittest.mock import patch

    w, _ = writer(tmp_path / "x.ini")
    with patch("sae.adapters.ini_writer.compile_batch", side_effect=ValueError("boom")):
        r = asyncio.run(emit_batch(_batch(), w))
    assert not r.ok and r.error == "write_failed" and (r.detail or "").startswith("compile:")
