"""Synth end to end per archetype: chain, files, telemetry, final strategy (C3 section 6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e import _bus
from e2e._cli import CliResult, run_parallel, run_sae
from e2e._synth import COUNTER, synth

ARCHETYPES = tuple(COUNTER)


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory, parser_exe: Path) -> dict[str, CliResult]:
    base = tmp_path_factory.mktemp("synth_chain")
    results = run_parallel([lambda a=a: synth(base / a, parser_exe, a, 6, 2) for a in ARCHETYPES])  # type: ignore[misc]
    return dict(zip(ARCHETYPES, results, strict=True))


@pytest.mark.parametrize("arch", ARCHETYPES)
def test_chain_and_files(runs: dict[str, CliResult], arch: str) -> None:
    r = runs[arch]
    assert r.code == 0, r.stderr[-500:]
    run_dir = r.run_dir
    assert run_dir is not None
    for name in ("bus.jsonl", "bus.god.jsonl", "scratch/sae_in.ini"):
        assert (run_dir / name).is_file(), name
    assert r.strays() == []
    events = _bus.load(run_dir)
    assert _bus.check_chain(events) == []
    assert len([e for e in events if e["type"] == "strategy_selected"]) >= 72  # 6 years of months
    assert _bus.load(run_dir, god=True), "god bus is empty"


@pytest.fixture(scope="module")
def rrds(tmp_path_factory: pytest.TempPathFactory, runs: dict[str, CliResult]) -> dict[str, tuple[CliResult, Path]]:
    pytest.importorskip("rerun")
    base = tmp_path_factory.mktemp("rrd")

    def one(arch: str) -> tuple[CliResult, Path]:
        run_dir = runs[arch].run_dir
        assert run_dir is not None
        rrd = base / f"{arch}.rrd"
        t = run_sae(["telemetry", "--run", run_dir.name, "--rrd", str(rrd)], base / arch, use_runs_dir=False,
                    sets=[f"paths.runs_dir={json.dumps(str(run_dir.parent))}"])
        return t, rrd

    return dict(zip(ARCHETYPES, run_parallel([lambda a=a: one(a) for a in ARCHETYPES]), strict=True))  # type: ignore[misc]


@pytest.mark.parametrize("arch", ARCHETYPES)
def test_telemetry_rrd(rrds: dict[str, tuple[CliResult, Path]], arch: str) -> None:
    t, rrd = rrds[arch]
    assert t.code == 0, t.stderr[-500:]
    assert rrd.is_file() and rrd.stat().st_size > 0


@pytest.mark.parametrize("arch", ARCHETYPES)
def test_final_strategy_is_counter(runs: dict[str, CliResult], arch: str) -> None:
    run_dir = runs[arch].run_dir
    assert run_dir is not None
    assert _bus.final_strategy(_bus.load(run_dir)) == COUNTER[arch]


def test_parser_exe_default_resolves_without_set(tmp_path: Path, parser_exe: Path) -> None:
    """T2 proposal 014 landed: ``sae synth`` finds the parser build without ``--set paths.parser_exe``."""
    r = synth(tmp_path, parser_exe, "turtle", 1, 1, with_exe=False)
    assert r.code == 0, r.stderr[-500:]
    assert r.run_dir is not None
    assert _bus.check_chain(_bus.load(r.run_dir), min_decisions=12, require_applied=False) == []
