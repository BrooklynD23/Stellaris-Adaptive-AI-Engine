"""Fog-adversarial wrong-lock measurement (Wave 1.5, WO-0).

Usage: uv run python tools/bench/fog_adversarial_measure.py <empty-runs-dir> [--seeds 10] [--years 6]

Runs ``sae synth`` for 4 archetypes x seeds 0..N-1 (exit code checked; a failed synth aborts), then replays each
run's bus observations 8 times, each with one seeded perturbation of ``player_legal``, through
``feature_steps`` + ``belief.update`` + ``strategy.select`` (builtin policy). Wrong lock = the first ``table``
strategy is not the archetype's expected counter. Also reports "wrong in window" (first table decision falls in
the perturbed window) and, for (h), the fraction of evidence ticks that kept a categorical feature.
Synth only; the perturbation shapes are an approximation of fog artefacts (UNVERIFIED against real saves).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from typing import Any

sys.path.insert(0, "src")
from sae.bench.demo import ARCHETYPES
from sae.bench.gate.metrics import _effective_cfg, feature_steps
from sae.contracts.c6_adapter import Observation
from sae.contracts.c8_config import EngineConfig
from sae.contracts.common import Strategy
from sae.core import belief, strategy
from sae.core.belief import BeliefState
from sae.core.dates import mi_of
from sae.core.policy import builtin_policy_doc

PERTS = ("a", "b", "c", "d", "e", "f", "g", "h", "i")
Views = list[dict[str, Any]]


def _mi(o: dict[str, Any]) -> int:
    return mi_of(o["game_date"])


def perturb(obs: list[dict[str, Any]], p: str, rng: random.Random) -> tuple[list[dict[str, Any]], int, int]:
    """Return perturbed raw observation dicts and the perturbed window [lo, hi] in months (hi=lo for h)."""
    first = _mi(obs[0])
    lo = first + rng.randint(0, 6)  # early: the first lock is ~month 3, so the fog artefact must precede it
    span = rng.randint(12, 24)
    hi = lo + span
    out: list[dict[str, Any]] = []
    stale: tuple[Any, Any] | None = None
    ordinal: dict[str, int] = {}
    for o in obs:
        o = json.loads(json.dumps(o))
        pl = o["payload"]
        views: Views = pl["player_legal"]
        m = _mi(o)
        inwin = lo <= m < hi
        if p in "ae" and (inwin if p == "a" else hi <= m < hi + span) and len(views) > 1:
            views.pop(0)
        if p == "b" and m >= lo and views:
            views[0]["observer_sid"] += 1000
        if p == "c" and inwin and views:
            views[0]["frontier_systems"] = 1
            views[0]["fortified_border_starbases"] = 0
        if p == "d" and m >= lo:
            for v in views[:1]:
                v["at_war_with_observer"] = True
                v["player_war_count"] = 1
        if p == "e":
            if inwin:  # save gap: legal data frozen at its last pre-gap value
                stale = stale or (pl["player_legal"], pl["legal_as_of"])
                pl["player_legal"], pl["legal_as_of"] = json.loads(json.dumps(stale[0])), stale[1]
        if p == "f" and inwin:
            pl["player_legal"] = []
        if p == "g" and inwin and views:
            # Keep the player's real views; add 3 observers that each see the same single bordering system.
            tmpl = {**views[0], "frontier_systems": 1, "fortified_border_starbases": rng.randint(0, 1)}
            extra = ({**tmpl, "observer_sid": tmpl["observer_sid"] + 100 * (i + 1)} for i in range(3))
            pl["player_legal"] = [*views, *extra]
        if p in "hi":  # h: partial flicker (first observer id changes every other save); i: every id changes
            key = pl["legal_as_of"] or ""
            n = ordinal.setdefault(key, len(ordinal))
            if n % 2:
                for v in (views[:1] if p == "h" else views):
                    v["observer_sid"] += 1000
        out.append(o)
    return out, lo, (hi if p not in "hi" else lo)


def replay(
    obs_raw: list[dict[str, Any]], cfg: EngineConfig, model: Any
) -> tuple[Strategy | None, int | None, float | None]:
    """First table strategy, its month, and the fraction of evidence ticks keeping a categorical."""
    obs = [Observation.model_validate(o["payload"]) for o in obs_raw]
    steps = feature_steps(obs)
    bs, ss = BeliefState(), strategy.StrategyState()
    ev = kept = 0
    first: tuple[Strategy, int] | None = None
    for i, st in enumerate(steps):
        bs, _ = belief.update(bs, st.vector, st.new_evidence, st.k, cfg, model, st.mi)
        if st.new_evidence:
            ev += 1
            kept += st.vector.fleet_power_cat is not None or st.vector.tech_cat is not None
        if i + 1 < len(steps) and steps[i + 1].mi == st.mi:
            continue  # decide on the last observation of each month
        ss, pay, _ = strategy.select(ss, bs.posterior, st.mi, cfg, decision_id=f"m{st.mi}")
        if first is None and pay.reason.value == "table":
            first = (pay.strategy, st.mi)
    return (first[0] if first else None), (first[1] if first else None), (kept / ev if ev else None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs_dir")
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--years", type=int, default=6)
    a = ap.parse_args()
    out = a.runs_dir
    from sae.core.belief import LikelihoodModel
    doc = builtin_policy_doc()
    cfg = _effective_cfg(doc)
    model = LikelihoodModel()
    res: dict[str, list[tuple[bool, bool, float | None]]] = {p: [] for p in ("0", *PERTS)}
    for arch, exp in ARCHETYPES.items():
        for s in range(a.seeds):
            before = set(os.listdir(out))
            r = subprocess.run([sys.executable, "-m", "sae", "synth", "--archetype", arch, "--years", str(a.years),
                                "--seed", str(s), "--runs-dir", out], capture_output=True, text=True)
            if r.returncode != 0:  # code review N7
                print(f"synth failed rc={r.returncode} {arch} seed {s}: {r.stderr[-300:]}", file=sys.stderr)
                return 2
            new = [d for d in os.listdir(out) if d not in before]
            with open(f"{out}/{new[0]}/bus.jsonl", encoding="utf-8") as fh:
                obs = [e for e in (json.loads(x) for x in fh) if e["type"] == "observation"]
            for p in ("0", *PERTS):
                if p == "0":
                    pert, lo, hi = obs, -1, -1
                else:
                    pert, lo, hi = perturb(obs, p, random.Random(f"{p}-{arch}-{s}"))
                strat, mon, frac = replay(pert, cfg, model)
                wrong = strat is None or strat.value != exp
                res[p].append((wrong, wrong and mon is not None and lo <= mon <= hi + 1, frac))
    print("pert  runs wrong wrong_in_window evid_kept_frac")
    for p, rows in res.items():
        fr = [f for _, _, f in rows if f is not None]
        print(p, len(rows), sum(w for w, _, _ in rows), sum(i for _, i, _ in rows),
              f"{sum(fr) / len(fr):.3f}" if fr else "n/a")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
