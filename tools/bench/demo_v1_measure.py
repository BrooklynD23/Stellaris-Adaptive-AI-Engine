"""Demo v1 lag and wrong-lock measurement (W1-9).

Usage: uv run python tools/bench/demo_v1_measure.py 0.45 <empty-runs-dir>.
Synth only; first confident belief = first belief_update with max posterior >= threshold; first counter = first
strategy_selected with reason "table". Balanced lag is an artifact (its counter is baseline, so nothing switches)."""

import json
import os
import statistics
import subprocess
import sys

sys.path.insert(0, "src")
from sae.bench.demo import ARCHETYPES

THR = float(sys.argv[1])
out = sys.argv[2]


def mon(d):
    y, m, _ = d.split(".")
    return int(y) * 12 + int(m)


rows = []
for a, exp in ARCHETYPES.items():
    for s in range(10):
        before = set(os.listdir(out))
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "sae",
                "synth",
                "--archetype",
                a,
                "--years",
                "6",
                "--seed",
                str(s),
                "--runs-dir",
                out,
            ],
            capture_output=True,
        )
        new = [p for p in os.listdir(out) if p not in before]
        with open(f"{out}/{new[0]}/bus.jsonl", encoding="utf-8") as fh:
            ev = [json.loads(line) for line in fh]
        conf = next(
            (
                mon(e["game_date"])
                for e in ev
                if e["type"] == "belief_update" and max(e["payload"]["posterior"].values()) >= THR
            ),
            None,
        )
        first = next((e for e in ev if e["type"] == "strategy_selected" and e["payload"]["reason"] == "table"), None)
        if conf is None or first is None:
            rows.append((a, s, None, None, r.returncode))
            continue
        rows.append(
            (a, s, mon(first["game_date"]) - conf, first["payload"]["strategy"] != exp, first["payload"]["strategy"])
        )
json.dump(rows, open(out + "/rows.json", "w"))
for a in ARCHETYPES:
    rr = [r for r in rows if r[0] == a]
    lags = [r[2] for r in rr if r[2] is not None]
    print(
        a,
        "n_ok",
        len(lags),
        "lag mean",
        statistics.mean(lags) if lags else None,
        "min/max",
        min(lags, default=None),
        max(lags, default=None),
        "wrong",
        sum(1 for r in rr if r[3]),
        "/",
        len(rr),
        [r[4] for r in rr if r[3]],
    )
print("TOTAL wrong", sum(1 for r in rows if r[3]), "/", len(rows), "nodata", sum(1 for r in rows if r[2] is None))
