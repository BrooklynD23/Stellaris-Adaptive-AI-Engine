"""Acceptance through the REAL `sae synth` path (T2 SynthAdapter + T3 sae-parse + the daemon loop).

Each archetype must select its COUNTER_TABLE strategy in >= 3 of 4 seeds. The 30-year no-drift matrix at
forced H0 and H2 (T7 CI-1 shape) runs only with T7_E2E_FULL=1.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from sae.contracts.common import COUNTER_TABLE, Archetype

ROOT = Path(__file__).resolve().parents[3]
PARSER = ROOT / "parser" / "target" / "debug" / "sae-parse"
SEEDS = (1, 2, 3, 4)
FULL = os.environ.get("T7_E2E_FULL") == "1"

pytestmark = pytest.mark.skipif(not PARSER.exists(), reason="needs parser/target/debug/sae-parse (cargo build -j 1)")


def _strategies(schedule: str, seed: int, years: int, runs: Path, level: str | None = None) -> list[tuple[str, str]]:
    """Run `sae synth` in a subprocess; return the (game_date, strategy) of every strategy_selected event."""
    out = runs / f"{schedule.replace(',', '_').replace('@', '_')}-{seed}-{level or 'auto'}"
    argv = [sys.executable, "-m", "sae", "synth", "--archetype", schedule, "--years", str(years), "--seed", str(seed),
            "--runs-dir", str(out), "--set", f"paths.parser_exe={PARSER}"]
    if level is not None:
        argv += ["--set", f'governor.forced_level="{level}"']
    done = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=600, check=False)
    assert done.returncode == 0, done.stderr[-2000:]
    (bus,) = out.glob("*/bus.jsonl")
    rows = (json.loads(line) for line in bus.read_text(encoding="utf-8").splitlines())
    return [(e["game_date"], e["payload"]["strategy"]) for e in rows if e["type"] == "strategy_selected"]


def test_each_archetype_picks_its_counter_in_3_of_4_seeds(tmp_path: Path) -> None:
    jobs = [(a, s) for a in Archetype for s in SEEDS]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda j: _strategies(j[0].value, j[1], 6, tmp_path), jobs))
    hits: dict[Archetype, int] = dict.fromkeys(Archetype, 0)
    for (arch, _), tl in zip(jobs, results, strict=True):
        hits[arch] += bool(tl) and tl[-1][1] == COUNTER_TABLE[arch].value
    assert all(h >= 3 for h in hits.values()), hits


@pytest.mark.skipif(not FULL, reason="30-year H0/H2 matrix: set T7_E2E_FULL=1")
@pytest.mark.parametrize("level", ["H0", "H2"])
def test_no_drift_over_30_years(tmp_path: Path, level: str) -> None:
    jobs = [(a, s) for a in Archetype for s in SEEDS]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda j: _strategies(j[0].value, j[1], 30, tmp_path, level), jobs))
    for (arch, seed), tl in zip(jobs, results, strict=True):
        want = COUNTER_TABLE[arch].value
        after = [st for d, st in tl if d >= "2203.01.01"]
        assert after and set(after) == {want}, (arch, seed, sorted(set(after)))
    mixed = _strategies("turtle,early_aggression@12", 1, 30, tmp_path, level)
    assert mixed[-1][1] == COUNTER_TABLE[Archetype.EARLY_AGGRESSION].value
