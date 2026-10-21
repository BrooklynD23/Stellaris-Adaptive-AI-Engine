"""Replay of a recorded (simulator + real parser saves) session: chain, determinism, ack models."""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e import _bus
from e2e._cli import CliResult, run_parallel, run_sae
from e2e._synth import record


@pytest.fixture(scope="module")
def session(tmp_path_factory: pytest.TempPathFactory, parser_exe: Path) -> Path:
    return record(tmp_path_factory.mktemp("replay_session"), parser_exe, "turtle", 2, 1)


def _replay(session: Path, base: Path, exe: Path, ack_model: str) -> CliResult:
    return run_sae(["replay", str(session), "--speed", "max", "--ack-model", ack_model], base, parser_exe=exe)


@pytest.fixture(scope="module")
def runs(
    tmp_path_factory: pytest.TempPathFactory, session: Path, parser_exe: Path
) -> dict[str, CliResult]:
    base = tmp_path_factory.mktemp("replay_runs")
    jobs = [("echo1", "echo"), ("echo2", "echo"), ("drop", "drop:1.0")]
    out = run_parallel([lambda n=n, m=m: _replay(session, base / n, parser_exe, m) for n, m in jobs])  # type: ignore[misc]
    return {n: r for (n, _), r in zip(jobs, out, strict=True)}


def test_echo_chain_clean(runs: dict[str, CliResult]) -> None:
    r = runs["echo1"]
    assert r.code == 0, r.stderr[-500:]
    assert r.run_dir is not None and r.strays() == []
    events = _bus.load(r.run_dir)
    assert _bus.check_chain(events) == []
    saves = [e for e in events if e["type"] == "observation" and e["payload"]["source"] == "save"]
    assert len(saves) >= 8, "real saves were not parsed"
    assert not [e for e in events if e["type"] == "health" and e["payload"]["kind"] == "budget_breach"]


def test_same_session_twice_same_digest(runs: dict[str, CliResult]) -> None:
    a, b = runs["echo1"].run_dir, runs["echo2"].run_dir
    assert a is not None and b is not None
    ea, eb = _bus.load(a), _bus.load(b)
    assert len([e for e in ea if e["type"] == "intent_ack"]) >= 1
    assert _bus.digest(ea) == _bus.digest(eb)


def test_drop_all_acks_are_lost_never_applied(runs: dict[str, CliResult]) -> None:
    r = runs["drop"]
    assert r.code == 0, r.stderr[-500:]
    assert r.run_dir is not None
    events = _bus.load(r.run_dir)
    statuses = [e["payload"]["status"] for e in events if e["type"] == "intent_ack"]
    assert statuses, "no acks at all: nothing was emitted"
    assert "APPLIED" not in statuses
    assert "LOST" in statuses
    assert _bus.check_chain(events, require_applied=False) == []
