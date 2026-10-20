"""Fog-perturbation checks F0, F1 (engine boundary) on a synth recording (plan section 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e import _bus
from e2e._fog import decision_stream, run_replay, with_plyr_lines
from e2e._synth import record

SEED = 7  # baseline uses seed 1: a different random player


@pytest.fixture(scope="module")
def session(tmp_path_factory: pytest.TempPathFactory, parser_exe: Path) -> Path:
    return record(tmp_path_factory.mktemp("fog_session"), parser_exe, "turtle", 2, 1)


@pytest.fixture(scope="module")
def baseline(tmp_path_factory: pytest.TempPathFactory, session: Path, parser_exe: Path) -> Path:
    base = tmp_path_factory.mktemp("fog_base")
    return run_replay(with_plyr_lines(session, base / "session", 1), base, parser_exe)


def test_f1_engine_boundary_decisions_unchanged(
    tmp_path: Path, session: Path, baseline: Path, parser_exe: Path
) -> None:
    perturbed = with_plyr_lines(session, tmp_path / "perturbed_session", SEED)
    run = run_replay(perturbed, tmp_path / "p", parser_exe, perturb_seed=SEED)
    base_stream = decision_stream(baseline)
    assert len(base_stream) >= 12, "vacuous: no decision stream"
    assert decision_stream(run) == base_stream
    assert _bus.check_chain(_bus.load(run)) == []
    base_god = (baseline / "bus.god.jsonl").read_text(encoding="utf-8")
    assert (run / "bus.god.jsonl").read_text(encoding="utf-8") != base_god, "vacuous: god bus did not change"


def test_f0_leaky_assembler_is_caught(tmp_path: Path, session: Path, parser_exe: Path) -> None:
    """F0: a leaky assembler under perturbed god data must differ from a leaky run on baseline god data."""
    leaky_base = run_replay(
        with_plyr_lines(session, tmp_path / "base_session", 1), tmp_path / "b", parser_exe, leaky=True
    )
    perturbed = with_plyr_lines(session, tmp_path / "perturbed_session", SEED)
    run = run_replay(perturbed, tmp_path / "p", parser_exe, perturb_seed=SEED, leaky=True)
    assert decision_stream(run) != decision_stream(leaky_base), "F1 is vacuous: a god leak went undetected"
