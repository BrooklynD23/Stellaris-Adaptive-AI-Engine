"""Gate Stage 2 (proposal 029 sections 3, 3b, 4; C10 section 6): the sealed tripwire and game-paired drafts.

``detector_replay`` reads sealed campaigns once per candidate. Only ``sealed_noninferior:<cid>`` checks decide the
verdict; the rest are informational (``passed=true``) so ``GateReport.passed == all(checks.passed)`` holds. The
game-paired objectives write a draft protocol and exit 4: no flag turns them into a result. CUSUM never gates.
UNVERIFIED (M0): real sealed campaigns make these numbers meaningful.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path

from sae.bench.gate import GATE_VERSION
from sae.bench.gate.metrics import fold_score, round2
from sae.contracts.c10_profile import GateCheck, GateReport, PolicyVersion
from sae.contracts.common import Archetype
from sae.core.belief import ARCHS
from sae.core.dates import mi_of
from sae.profile import ledger, store
from sae.profile.cli import _Ctx

from .stage1 import (
    GateError,
    Replay,
    _sealed_budget,
    commit,
    load_campaigns,
    load_env,
    load_policy,
    prereg_of,
    replay,
    switch_months,
)

log = logging.getLogger(__name__)

__all__ = ["GAME_OBJECTIVES", "NO_RESULT", "cmd_stage2", "run_detector_replay", "write_protocol"]

GAME_OBJECTIVES = ("beat_player", "challenge_band")
NO_RESULT = "no result: game-paired Stage 2 needs D6 and real paired runs (M4)"
MIN_PAIRS = 30
WINDOW_YEARS = 30
SEED_BASE = 2_000_000  # above the 1e6 synth range; the real bank is drawn at M4
BANK_FACTOR = 3
RETIRE_AFTER = 10
HALT_AFTER_CRASHES = 2
_PREFIX = ledger.SEALED_CHECK_PREFIX


def write_protocol(objective: str, sha: str, out: Path) -> None:
    """Write the draft game-paired protocol (no result, no registry row)."""
    protocol = {
        "status": "draft", "objective": objective, "candidate_sha256": sha, "gate_version": GATE_VERSION,
        "paired_seeds": list(range(SEED_BASE, SEED_BASE + MIN_PAIRS)), "min_n": MIN_PAIRS,
        "window_years": WINDOW_YEARS, "crash_is_loss": True, "halt_after_crashes": HALT_AFTER_CRASHES,
        "sealed_save_bank_min": BANK_FACTOR * MIN_PAIRS, "retire_save_after_uses": RETIRE_AFTER,
        "note": NO_RESULT,
    }  # fmt: skip
    try:
        store.write_atomic(out, (json.dumps(protocol, sort_keys=True, indent=1) + "\n").encode())
    except OSError as e:
        raise GateError(f"cannot write {out}: {e}", 1) from e


def _delays(r: Replay) -> list[int]:
    """Months from each label change until the argmax follows it (censored at the trace end)."""
    out: list[int] = []
    for seg in r.label.segments[1:]:
        start = mi_of(seg.from_date)
        later = [m.mi for m in r.cand if m.mi >= start]
        if not later:
            continue
        hit = next((mi for mi in later if _top(r, mi) == seg.archetype), later[-1] + 1)
        out.append(hit - start)
    return out


def _top(r: Replay, mi: int) -> Archetype:
    post = next(m.posterior for m in r.cand if m.mi == mi)
    return max(ARCHS, key=lambda a: (post[a], -ARCHS.index(a)))


def _false_lock(r: Replay, threshold: float) -> float:
    """Share of labelled months where the candidate is confident (>= threshold) and wrong."""
    scored = 0
    wrong = 0
    for m in r.cand:
        lab = None
        for seg in r.label.segments:
            if mi_of(seg.from_date) <= m.mi:
                lab = seg.archetype
        if lab is None:
            continue
        scored += 1
        top = max(ARCHS, key=lambda a: (m.posterior[a], -ARCHS.index(a)))
        wrong += top != lab and m.posterior[top] >= threshold
    return wrong / scored if scored else 0.0


def run_detector_replay(
    cand: PolicyVersion, inc: PolicyVersion, margin: float, sealed: list[ledger.LabelledCampaign],
) -> list[GateCheck]:
    """The Stage 2 checks for ``sealed`` campaigns (sealed checks first; informational checks never gate)."""
    k = len(sealed)
    tag = f"tripwire, not a significance test (n_sealed = {k})"
    reps = [replay(lc.state.campaign_id, lc.label, lc.observations, cand, inc) for lc in sealed]
    checks: list[GateCheck] = []
    for r in reps:
        delta = fold_score(r.cand, r.label).log_loss - fold_score(r.inc, r.label).log_loss
        ok = math.isfinite(delta) and delta <= margin
        val = round2(delta) if math.isfinite(delta) else None
        checks.append(GateCheck(name=f"{_PREFIX}{r.cid}", passed=ok, value=val, detail=tag))
    delays = [d for r in reps for d in _delays(r)]
    locks = [_false_lock(r, cand.detector.belief_threshold) for r in reps]
    sw = [len(switch_months(r.cand_strat)) - len(switch_months(r.inc_strat)) for r in reps]
    info = f"informational; {tag}"
    checks.append(GateCheck(name="detection_delay", passed=True, detail=info,
                            value=round2(sum(delays) / len(delays)) if delays else None))  # fmt: skip
    checks.append(GateCheck(name="false_lock", passed=True, value=round2(sum(locks) / k), detail=info))
    checks.append(GateCheck(name="v3_signal", passed=True, value=round2(sum(sw) / k), detail=info))
    return checks


def _stage2_detector(args: argparse.Namespace, ctx: _Ctx) -> int:
    env = load_env(ctx)
    sha: str = args.sha
    acts = env.acts(sha)
    if not args.include_synth and any(a.startswith("stage2_") for a in acts):
        raise GateError(f"{sha[:8]} already used its sealed query (one per candidate)", 2)
    if acts[:3] != ["propose", "prereg", "stage1_pass"]:
        raise GateError(f"{sha[:8]} needs propose, prereg, stage1_pass in order before Stage 2 (has {acts})", 2)
    pre = prereg_of(env, sha)
    cand = load_policy(env, sha)
    inc = load_policy(env, pre.incumbent_sha256) if pre else None
    if pre is None or cand is None or inc is None:
        raise GateError("candidate, incumbent or preregistration unreadable", 2)
    if not args.include_synth and not _sealed_budget(env, cand).passed:
        raise GateError(f"{sha[:8]}: detector already spent a sealed query", 2)
    sealed = load_campaigns(env, "sealed", args.include_synth)
    if args.include_synth:  # diagnostic mode must never read live sealed data: that read is unbudgeted
        sealed = [lc for lc in sealed if not ledger._is_live(lc.state, env.runs)]
    if not sealed:
        raise GateError("no sealed campaign (D3: label a 6th)", 2)
    checks = run_detector_replay(cand, inc, pre.non_inferiority_margin, sealed)
    report = GateReport(stage=2, candidate_sha256=sha, incumbent_sha256=pre.incumbent_sha256,
                        gate_version=GATE_VERSION, checks=tuple(checks), created_at=store.now(),
                        passed=all(c.passed for c in checks))  # fmt: skip
    return commit(env, report, record=not args.include_synth)


def cmd_stage2(args: argparse.Namespace, ctx: _Ctx) -> int:
    """``sae gate stage2 <sha> --objective ... [--include-synth] [--out FILE]``."""
    if args.objective in GAME_OBJECTIVES:
        if not args.out:
            raise GateError(f"--out is required for {args.objective}", 2)
        write_protocol(args.objective, args.sha, Path(args.out))
        raise GateError(NO_RESULT, 4)
    return _stage2_detector(args, ctx)

