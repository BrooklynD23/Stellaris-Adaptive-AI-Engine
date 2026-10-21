"""CI-1: H0 (full parse) vs H2 (degraded) runs of the same campaign pick the same strategy switches."""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pytest

from e2e import _bus
from e2e._cli import CliResult, run_parallel
from e2e._synth import record, replay

SCHEDULES = ("turtle", "tech_rush", "early_aggression", "balanced", "turtle,early_aggression@12")
COMMITMENT_MONTHS = 24  # engine.commitment_months default
MIN_SWITCHES = 8
THRESHOLD = 0.90  # HYPOTHESIS (resource proposal section 8)
YEARS = 30


@pytest.fixture(scope="module")
def seeds(full: bool) -> tuple[int, ...]:
    return (1, 2, 3, 4) if full else (1, 2)


def _name(sch: str, seed: int) -> str:
    return f"{sch.replace(',', '_').replace('@', 'at')}-{seed}"


@pytest.fixture(scope="module")
def recs(
    tmp_path_factory: pytest.TempPathFactory, parser_exe: Path, seeds: tuple[int, ...]
) -> dict[tuple[str, int], Path]:
    """One recording per schedule x seed (``sae synth --record``, 30 y)."""
    base = tmp_path_factory.mktemp("ci1_rec")
    keys = [(sch, seed) for sch in SCHEDULES for seed in seeds]
    out = run_parallel([partial(record, base / _name(*k), parser_exe, k[0], YEARS, k[1]) for k in keys])
    return dict(zip(keys, out, strict=True))


@pytest.fixture(scope="module")
def runs(
    tmp_path_factory: pytest.TempPathFactory, parser_exe: Path, recs: dict[tuple[str, int], Path]
) -> dict[tuple[str, int, str], CliResult]:
    """H0 and H2 replays of every recording, each in its own runs dir."""
    base = tmp_path_factory.mktemp("ci1_replay")
    keys = [(sch, seed, lvl) for (sch, seed) in recs for lvl in ("H0", "H2")]
    jobs = [
        partial(replay, recs[(k[0], k[1])], base / f"{_name(k[0], k[1])}-{k[2]}", parser_exe, forced=k[2])
        for k in keys
    ]
    return dict(zip(keys, run_parallel(jobs), strict=True))


def _events(r: CliResult) -> list[_bus.Event]:
    assert r.code == 0, r.stderr[-500:]
    assert r.run_dir is not None
    return _bus.load(r.run_dir)


def test_same_recording_h0_replays_have_equal_digest(tmp_path: Path, parser_exe: Path) -> None:
    rec = record(tmp_path / "rec", parser_exe, "turtle", 10, 1)
    a, b = run_parallel([partial(replay, rec, tmp_path / str(i), parser_exe, forced="H0") for i in (0, 1)])
    ea, eb = _events(a), _events(b)
    assert len([e for e in ea if e["type"] == "strategy_selected"]) >= 120
    assert _bus.digest(ea) == _bus.digest(eb)


def test_h2_really_degrades_every_campaign(runs: dict[tuple[str, int, str], CliResult], seeds: tuple[int, ...]) -> None:
    """H2 must observe strictly fewer save observations than H0 and differ in digest (else the runs are identical)."""

    def saves(r: CliResult) -> int:
        return sum(1 for e in _events(r) if e["type"] == "observation" and e["payload"]["source"] == "save")

    for sch in SCHEDULES:
        for seed in seeds:
            h0, h2 = runs[(sch, seed, "H0")], runs[(sch, seed, "H2")]
            assert saves(h2) < saves(h0), (sch, seed, saves(h2), saves(h0))
            assert _bus.digest(_events(h0)) != _bus.digest(_events(h2)), (sch, seed)


def test_pooled_switch_agreement_h0_vs_h2(runs: dict[tuple[str, int, str], CliResult], seeds: tuple[int, ...]) -> None:
    matched = total = n_switches = 0
    for sch in SCHEDULES:
        for seed in seeds:
            a = _bus.switches(_events(runs[(sch, seed, "H0")]))
            b = _bus.switches(_events(runs[(sch, seed, "H2")]))
            matched += _bus.ci1_matched(a, b, COMMITMENT_MONTHS)
            total += len(a) + len(b)
            n_switches += len(a) + len(b)
    assert n_switches >= MIN_SWITCHES, f"vacuous: {n_switches} switches"
    rate = 2 * matched / total
    print(f"CI-1 pooled rate {rate:.3f} over {n_switches} switches")
    assert rate >= THRESHOLD, f"rate {rate:.3f}"
