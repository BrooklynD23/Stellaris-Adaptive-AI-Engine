"""Deferred X3-G9..G11: decision chain, determinism and fog on a real recording (docs/handoff/deferred/X3.md)."""

from __future__ import annotations

from pathlib import Path

import pytest
from e2e import _bus
from e2e._cli import run_sae
from e2e._fog import decision_stream, run_replay


def _has_ack(rec: Path) -> bool:
    return b"|ACK|" in (rec / "game.log").read_bytes()


def _replay_cli(rec: Path, base: Path, exe: Path) -> Path:
    r = run_sae(
        ["replay", str(rec), "--speed", "max", "--ack-model", "from_recording"], base, parser_exe=exe
    )
    assert r.code == 0, f"sae replay exit {r.code}; stderr tail {len(r.stderr)} chars"
    assert r.run_dir is not None, "no single run dir"
    return r.run_dir


@pytest.mark.game(id="X3-G9")
def test_replay_chain(game_recording: Path, game_parser_exe: Path, tmp_path: Path) -> None:
    """Replay exits 0 and the decision chain is clean (V-3 signal)."""
    run = _replay_cli(game_recording, tmp_path, game_parser_exe)
    events = _bus.load(run)
    applied = _has_ack(game_recording)
    problems = _bus.check_chain(events, min_decisions=3, require_applied=applied)
    print(f"X3-G9 events={len(events)} ack_in_recording={applied} problems={len(problems)}")
    assert problems == [], f"{len(problems)} chain problems, first: {problems[:1]}"


@pytest.mark.game(id="X3-G10")
def test_replay_deterministic(game_recording: Path, game_parser_exe: Path, tmp_path: Path) -> None:
    """Two replays of one recording give an equal bus digest."""
    a = _replay_cli(game_recording, tmp_path / "a", game_parser_exe)
    b = _replay_cli(game_recording, tmp_path / "b", game_parser_exe)
    da, db = _bus.digest(_bus.load(a)), _bus.digest(_bus.load(b))
    print(f"X3-G10 digest_a={da[:12]} digest_b={db[:12]}")
    assert da == db, "digests differ between two replays"


@pytest.mark.game(id="X3-G11")
def test_fog_v5(game_recording: Path, game_parser_exe: Path, tmp_path: Path) -> None:
    """Decisions ignore god-view perturbation (F1 / V-5); the god bus is non-empty (non-vacuity)."""
    base = run_replay(game_recording, tmp_path / "base", game_parser_exe)
    want = decision_stream(base)
    god = _bus.load(base, god=True)
    print(f"X3-G11 decisions={len(want)} god_events={len(god)}")
    assert god, "god bus is empty: the perturbation could not matter"
    assert want, "no decision events"
    for seed in (1, 2):
        got = decision_stream(run_replay(game_recording, tmp_path / f"s{seed}", game_parser_exe, perturb_seed=seed))
        assert got == want, f"seed {seed}: decision stream differs ({len(got)} vs {len(want)} lines)"
