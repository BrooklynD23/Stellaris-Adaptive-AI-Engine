"""Release-gate judges R-1..R-6 over CSV and ``bus.jsonl`` inputs. Pure functions, no game."""

from __future__ import annotations

import csv
import itertools
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sae.bench.stats import bootstrap_slowdown_ci, ols_slope, percentile

PASS, FAIL, INSUFFICIENT = "PASS", "FAIL", "INSUFFICIENT"
MIN_RUNS = 5
MIN_FRAMES = 100
R4_WINDOW_S = 10.0
_H2_UP = {"H2", "H3", "S"}
_H2_DOWN = {"H0", "H1", "H2"}


@dataclass(frozen=True)
class Verdict:
    """Gate outcome with the numbers behind it."""

    gate: str
    status: str
    numbers: dict[str, float | int | str] = field(default_factory=dict)

    def render(self) -> str:
        """One printable line."""
        nums = " ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in self.numbers.items())
        return f"{self.gate} {self.status} {nums}".rstrip()

    @property
    def exit_code(self) -> int:
        """0 only for PASS."""
        return 0 if self.status == PASS else 1


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def judge_r1(csv_path: Path, *, seed: int = 1) -> Verdict:
    """R-1: slowdown of condition c vs a: mean <= 2 %, bootstrap 95 % upper <= 3 %."""
    groups: dict[str, list[float]] = {}
    for r in _rows(csv_path):
        groups.setdefault(r["condition"].strip().lower(), []).append(float(r["seconds_per_year"]))
    a, c = groups.get("a", []), groups.get("c", [])
    if len(a) < MIN_RUNS or len(c) < MIN_RUNS:
        return Verdict("R-1", INSUFFICIENT, {"runs_a": len(a), "runs_c": len(c), "need": MIN_RUNS})
    mean = statistics.fmean(c) / statistics.fmean(a) - 1.0
    lo, upper = bootstrap_slowdown_ci(a, c, seed=seed)
    ok = mean <= 0.02 and upper <= 0.03
    return Verdict("R-1", PASS if ok else FAIL, {"slowdown_mean_pct": mean * 100, "boot95_upper_pct": upper * 100,
                       "boot_width_pct": (upper - lo) * 100})


def _frame_times(path: Path) -> list[float]:
    # UNVERIFIED: PresentMon column is MsBetweenPresents; M0 would capture a real CSV and check the header.
    return [float(r["MsBetweenPresents"]) for r in _rows(path)]


def judge_r2(a_csv: Path, c_csv: Path) -> Verdict:
    """R-2: p99 frame-time ratio c/a <= 1.05."""
    a, c = _frame_times(a_csv), _frame_times(c_csv)
    if len(a) < MIN_FRAMES or len(c) < MIN_FRAMES:
        return Verdict("R-2", INSUFFICIENT, {"frames_a": len(a), "frames_c": len(c), "need": MIN_FRAMES})
    pa, pc = percentile(a, 99), percentile(c, 99)
    ratio = pc / pa
    return Verdict("R-2", PASS if ratio <= 1.05 else FAIL, {"p99_a_ms": pa, "p99_c_ms": pc, "ratio": ratio})


def judge_r3(csv_path: Path, *, shipped_emits: float) -> Verdict:
    """R-3: OLS slope projected at ``shipped_emits`` per month costs <= 1 % of baseline s/year."""
    rows = _rows(csv_path)
    xs = [float(r["emits_per_month"]) for r in rows]
    ys = [float(r["seconds_per_year"]) for r in rows]
    if len(set(xs)) < 3:
        return Verdict("R-3", INSUFFICIENT, {"distinct_points": len(set(xs)), "need": 3})
    slope, base = ols_slope(xs, ys)
    if base <= 0:
        return Verdict("R-3", INSUFFICIENT, {"intercept_s_per_year": base})
    added = slope * shipped_emits
    pct = added / base * 100
    nums: dict[str, float | int | str] = {
        "emits_per_month": shipped_emits, "slope_s_per_emit": slope, "baseline_s_per_year": base, "projected_pct": pct,
        "ms_per_monthly_tick": added * 1000 / 12,
    }
    return Verdict("R-3", PASS if pct <= 1.0 else FAIL, nums)


def read_bus(path: Path) -> list[dict[str, Any]]:
    """Read ``bus.jsonl`` as dicts; blank lines skipped, malformed lines raise ``ValueError``.

    Text after the last LF is a partial line and is ignored (C5 section 1).
    """
    out: list[dict[str, Any]] = []
    complete = path.read_text(encoding="utf-8").rpartition("\n")[0]
    for n, line in enumerate(complete.split("\n"), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{n}: bad JSON: {exc}") from exc
    return out


def _health(ev: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in ev if e.get("type") == "health"]


def judge_r4(ev: list[dict[str, Any]]) -> Verdict:
    """R-4: first >=H2 health event at/after the first ram-trigger one is within 10 s of it; an ack follows.

    The hard-fault part of the gate is not judged here.
    """
    health = _health(ev)
    ram = next((e for e in health if e["payload"].get("trigger") == "ram"), None)
    if ram is None:
        return Verdict("R-4", INSUFFICIENT, {"ram_events": 0})
    h2 = next(
        (e for e in health if e["seq"] >= ram["seq"] and e["payload"].get("level") in _H2_UP), None
    )
    if h2 is None:
        return Verdict("R-4", FAIL, {"h2_events_after_ram": 0})
    dt = (h2["t_mono_ns"] - ram["t_mono_ns"]) / 1e9
    acks = sum(1 for e in ev if e.get("type") == "intent_ack" and e["seq"] > h2["seq"])
    ok = dt <= R4_WINDOW_S and acks >= 1
    return Verdict("R-4", PASS if ok else FAIL, {"h2_after_ram_s": dt, "acks_after_h2": acks})


def judge_r5(ev: list[dict[str, Any]]) -> Verdict:
    """R-5 (proxy for the gate text): a budget_breach health event exists and observations continue after it."""
    hit = next(
        (e for e in _health(ev)
         if "budget_breach" in (e["payload"].get("kind"), e["payload"].get("trigger"))),
        None,
    )
    if hit is None:
        return Verdict("R-5", INSUFFICIENT, {"breach_events": 0})
    after = sum(1 for e in ev if e.get("type") == "observation" and e["seq"] > hit["seq"])
    return Verdict("R-5", PASS if after else FAIL, {"observations_after": after})


def months(date: str) -> int:
    y, m, _ = date.split(".")
    return int(y) * 12 + int(m)


def judge_r6(ev: list[dict[str, Any]]) -> Verdict:
    """R-6 (proxy for the gate text): final level <= H2, observations span >= 12 months, no gap > 1 month."""
    health = _health(ev)
    dates = sorted({months(e["game_date"]) for e in ev if e.get("type") == "observation" and e.get("game_date")})
    if not health or not dates:
        return Verdict("R-6", INSUFFICIENT, {"health_events": len(health), "observations": len(dates)})
    last = str(health[-1]["payload"]["level"])
    span = dates[-1] - dates[0]
    gap = max((b - a for a, b in itertools.pairwise(dates)), default=0)
    ok = last in _H2_DOWN and span >= 12 and gap <= 1
    return Verdict("R-6", PASS if ok else FAIL, {"final_level": last, "span_months": span, "max_gap_months": gap})


BUS_GATES = {"R-4": judge_r4, "R-5": judge_r5, "R-6": judge_r6}
