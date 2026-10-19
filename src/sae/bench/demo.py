"""``sae bench demo``: run the four archetypes through ``sae synth`` + ``sae telemetry`` and report."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from sae.bench.gates import read_bus

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]
ARCHETYPES: dict[str, str] = {
    "turtle": "outscale_siege",
    "tech_rush": "early_punish",
    "early_aggression": "hold_counterpunch",
    "balanced": "baseline",
}


def default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run ``cmd`` capturing text output; never raises on non-zero exit."""
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def _summarise(bus: Path) -> tuple[str, int]:
    ev = read_bus(bus)
    strategies = [e["payload"]["strategy"] for e in ev if e.get("type") == "strategy_selected"]
    applied = sum(1 for e in ev if e.get("type") == "intent_ack" and e["payload"].get("status") == "APPLIED")
    return (strategies[-1] if strategies else "none"), applied


def _run_one(arch: str, years: int, seed: int, out: Path, exe: Path, run: Runner) -> str | None:
    before = {p.name for p in out.iterdir()}
    cmd = [sys.executable, "-m", "sae", "synth", "--archetype", arch, "--years", str(years),
           "--seed", str(seed), "--runs-dir", str(out), "--set", f"paths.parser_exe={json.dumps(str(exe))}"]
    if run(cmd).returncode != 0:
        return None
    new = sorted(p.name for p in out.iterdir() if p.name not in before and (p / "bus.jsonl").is_file())
    return new[-1] if new else None


def _rrd(arch: str, run_id: str, out: Path, run: Runner) -> str:
    rrd = out / f"{arch}.rrd"
    cmd = [sys.executable, "-m", "sae", "telemetry", "--runs-dir", str(out), "--run", run_id, "--rrd", str(rrd)]
    res = run(cmd)
    return str(rrd) if res.returncode == 0 else "rrd skipped"


def run_demo(
    years: int, seed: int, out: Path | None, parser_exe: Path, *, runner: Runner = default_runner,
    echo: Callable[[str], None] = print,
) -> int:
    """Run all four archetypes; print one line each. Exit 0 iff every run completed (never judges matches)."""
    out = out or Path(tempfile.mkdtemp(prefix="sae-demo-"))
    out.mkdir(parents=True, exist_ok=True)
    done = 0
    for arch, expected in ARCHETYPES.items():
        run_id = _run_one(arch, years, seed, out, parser_exe, runner)
        if run_id is None:
            echo(f"{arch}: run FAILED")
            continue
        final, applied = _summarise(out / run_id / "bus.jsonl")
        match = "yes" if final == expected else "no"
        echo(f"{arch} final={final} expected={expected} match={match} "
             f"acks_applied={applied} {_rrd(arch, run_id, out, runner)}")
        done += 1
    return 0 if done == len(ARCHETYPES) else 1
