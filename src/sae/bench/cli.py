"""``sae bench`` dispatch (C8 section 5). Exit 0 pass, 1 fail/insufficient/runtime error, 2 usage."""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from pathlib import Path

from sae.adapters.paths import resolve_paths
from sae.bench import gates
from sae.bench.demo import Runner, default_runner, run_demo
from sae.contracts.c8_config import PathsConfig


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(2)


def _build() -> argparse.ArgumentParser:
    p = _Parser(prog="sae bench", description="SAE benchmark gates and demo")
    sub = p.add_subparsers(dest="sub", required=True, parser_class=_Parser)
    s = sub.add_parser("parse", help="parser cost over saves")
    s.add_argument("saves", nargs="+", type=Path)
    s.add_argument("--repeat", type=int, default=3)
    s.add_argument("--parser-exe", type=Path)
    sub.add_parser("overhead", help="R-1").add_argument("csv", type=Path)
    s = sub.add_parser("stutter", help="R-2")
    s.add_argument("a_csv", type=Path)
    s.add_argument("c_csv", type=Path)
    s = sub.add_parser("script-cost", help="R-3")
    s.add_argument("csv", type=Path)
    s.add_argument("--emits", type=float, required=True, help="shipped emits per month")
    s = sub.add_parser("bus", help="R-4/R-5/R-6")
    s.add_argument("run_dir", type=Path)
    s.add_argument("--gate", required=True, choices=sorted(gates.BUS_GATES))
    s = sub.add_parser("spy", help="seconds per game year from autosaves")
    s.add_argument("save_dir", type=Path)
    s.add_argument("--parser-exe", type=Path)
    s = sub.add_parser("demo", help="four-archetype synth + telemetry demo")
    s.add_argument("--years", type=int, default=6)
    s.add_argument("--seed", type=int, default=2)
    s.add_argument("--out", type=Path)
    s.add_argument("--parser-exe", type=Path)
    return p


def _err(msg: str) -> int:
    print(f"sae bench: {msg}", file=sys.stderr)
    return 1


def _parse_json(exe: Path, save: Path, run: Runner, sections: str | None = None) -> dict[str, object]:
    cmd = [str(exe), "parse", "--save", str(save)] + (["--sections", sections] if sections else [])
    res = run(cmd)
    if res.returncode != 0:
        raise ValueError(f"sae-parse failed on {save}: exit {res.returncode}")
    data: dict[str, object] = json.loads(res.stdout)
    return data


def _cmd_parse(ns: argparse.Namespace, run: Runner, exe: Path) -> int:
    cols: dict[str, list[float]] = {"wall_s": [], "cpu_s": [], "rss_peak_bytes": []}
    for save in ns.saves:
        for _ in range(ns.repeat):
            cost = _parse_json(exe, save, run)["cost"]
            if not isinstance(cost, dict):
                raise ValueError(f"{save}: no cost object in parser output")
            for k, vals in cols.items():
                vals.append(float(cost[k]))
    for k, vals in cols.items():
        print(f"{k} median={statistics.median(vals):.6g} max={max(vals):.6g} n={len(vals)}")
    return 0


def _cmd_spy(ns: argparse.Namespace, run: Runner, exe: Path) -> int:
    saves = sorted(ns.save_dir.glob("*.sav"), key=lambda p: p.stat().st_mtime)
    pts: list[tuple[float, int]] = []
    for sv in saves:
        meta = _parse_json(exe, sv, run, "meta")["meta"]
        if not isinstance(meta, dict):
            raise ValueError(f"{sv}: no meta object in parser output")
        # UNVERIFIED (A-34): autosave mtimes track game progress; M0 would compare against a real session.
        pts.append((sv.stat().st_mtime, gates.months(str(meta["date"]))))
    rates = [
        (t1 - t0) / (m1 - m0) * 12 for (t0, m0), (t1, m1) in itertools.pairwise(pts) if m1 > m0
    ]
    if not rates:
        print(f"spy INSUFFICIENT saves={len(saves)}")
        return 1
    print(f"spy seconds_per_game_year median={statistics.median(rates):.4g} pairs={len(rates)}")
    return 0


def main(command: str, argv: list[str], *, runner: Runner = default_runner) -> int:
    """Entry point called by ``sae.cli``."""
    try:
        ns = _build().parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    try:
        return _dispatch(ns, runner)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return _err(str(exc))


def _exe(ns: argparse.Namespace) -> Path | None:
    """``--parser-exe``, else C8 section 3 resolution (T2's resolver, proposal 014)."""
    return ns.parser_exe or resolve_paths(PathsConfig()).parser_exe


def _dispatch(ns: argparse.Namespace, run: Runner) -> int:
    verdict: gates.Verdict
    if ns.sub in ("parse", "spy", "demo"):
        exe = _exe(ns)
        if exe is None:
            return _err("sae-parse not found (use --parser-exe or build parser/)")
        if ns.sub == "demo":
            return run_demo(ns.years, ns.seed, ns.out, exe, runner=run)
        return (_cmd_parse if ns.sub == "parse" else _cmd_spy)(ns, run, exe)
    if ns.sub == "overhead":
        verdict = gates.judge_r1(ns.csv)
    elif ns.sub == "stutter":
        verdict = gates.judge_r2(ns.a_csv, ns.c_csv)
    elif ns.sub == "script-cost":
        verdict = gates.judge_r3(ns.csv, shipped_emits=ns.emits)
    else:
        verdict = gates.BUS_GATES[ns.gate](gates.read_bus(ns.run_dir / "bus.jsonl"))
    print(verdict.render())
    return verdict.exit_code


