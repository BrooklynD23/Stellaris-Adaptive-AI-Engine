"""Gate Stage 1 (proposal 029 section 1; C10 sections 5-6): cheap, held-in checks on a proposed policy.

Also holds the shared registry/evidence plumbing used by Stage 2. Exit codes: 0 pass, 1 stage fail or I/O error,
2 usage or refused. The gate writes a ``GateReport`` (canonical JSON, name = sha256 of bytes) and one
``stage1_pass|fail`` row by ``gate``. UNVERIFIED (M0): real labelled campaigns make these numbers meaningful.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import math
import subprocess
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from sae.bench.gate import GATE_VERSION
from sae.bench.gate.metrics import MonthPosterior, detector_trace, fold_score, strategy_trace
from sae.contracts.c6_adapter import Observation
from sae.contracts.c10_profile import (
    CampaignLabel,
    GateCheck,
    GateReport,
    PolicyVersion,
    Preregistration,
    RegistryError,
    RegistryRow,
    append_registry_row,
    canonical_json,
    fold_registry,
    pin_matches,
    policy_sha256,
)
from sae.contracts.common import Strategy
from sae.core.policy import builtin_policy_doc, load_builtin
from sae.profile import ledger, store
from sae.profile.cli import _Ctx
from sae.profile.index import CampaignState, campaign_state

log = logging.getLogger(__name__)

__all__ = ["GateEnv", "GateError", "Replay", "cmd_stage1", "commit", "load_env", "load_policy", "run_stage1"]

SEED_LIMIT = 1_000_000
COMMIT_MONTHS = 24  # settled decision: 24-month strategy commitments
ECE_MAX = 0.10
HELDIN_DETAIL = (
    "held-in, biased toward the candidate; LOCO per fold is in the tuner notes; the unbiased check is Stage 2 sealed"
)
SYNTH_NOTE = "synth: diagnostic only, not verification"
_MUTABLE = frozenset({"detector", "tuning", "version", "created_at", "notes", "parent_sha256"})
REPO_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pytest", ("uv", "run", "pytest", "-x", "-q")),
    ("mypy", ("uv", "run", "mypy", "--strict")),
    ("ruff", ("uv", "run", "ruff", "check")),
)
REPO_TIMEOUT_S = 3600


class GateError(Exception):
    """A refusal or I/O failure; ``code`` is the CLI exit code (2 refused, 1 runtime)."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GateEnv:
    """The registry and paths as read for one gate command."""

    sae: Path
    runs: Path
    key: str
    rows: list[RegistryRow]
    builtin: str
    current: str | None

    @property
    def pdir(self) -> Path:
        """``<sae_dir>/policies``."""
        return self.sae / "policies"

    @property
    def incumbent(self) -> str:
        """The policy currently in force (built-in when nothing is promoted)."""
        return self.current or self.builtin

    def acts(self, sha: str) -> list[str]:
        """Registry actions recorded for ``sha``, in order."""
        return [r.action for r in self.rows if r.sha256 == sha]


@dataclass(frozen=True)
class Replay:
    """Candidate and incumbent traces of one campaign."""

    cid: str
    label: CampaignLabel
    cand: list[MonthPosterior]
    inc: list[MonthPosterior]
    cand_strat: list[tuple[int, Strategy]]
    inc_strat: list[tuple[int, Strategy]]


def load_env(ctx: _Ctx) -> GateEnv:
    """Read the registry (invalid lines skipped) and fold it; raises :class:`GateError` (1) when unusable."""
    builtin = load_builtin().sha256
    rows: list[RegistryRow] = []
    path = ctx.sae / "policies" / "registry.jsonl"
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                rows.append(RegistryRow.model_validate_json(line))
            except ValueError:
                log.warning("registry: invalid line skipped")
        state = fold_registry(rows, builtin)
    except (OSError, UnicodeDecodeError) as e:
        raise GateError(f"registry unreadable: {e}", 1) from e
    except RegistryError as e:
        raise GateError(f"registry error: {e}", 1) from e
    return GateEnv(ctx.sae, ctx.runs_dir, ctx.key, rows, builtin, state.current)


def load_policy(env: GateEnv, sha: str) -> PolicyVersion | None:
    """The built-in doc, or ``policies/<sha>.json`` when it validates and hashes to ``sha``; else ``None``."""
    if sha == env.builtin:
        return builtin_policy_doc()
    try:
        doc = PolicyVersion.model_validate_json((env.pdir / f"{sha}.json").read_bytes())
    except (OSError, ValidationError):
        return None
    return doc if policy_sha256(doc) == sha else None


def prereg_of(env: GateEnv, sha: str) -> Preregistration | None:
    """The candidate's Preregistration evidence, or ``None`` when absent or unreadable."""
    for row in env.rows:
        if row.action == "prereg" and row.sha256 == sha and row.evidence_sha256:
            try:
                raw = (env.pdir / "evidence" / f"{row.evidence_sha256}.json").read_bytes()
                pre = Preregistration.model_validate_json(raw)
            except (OSError, ValidationError):
                return None
            return pre if pre.candidate_sha256 == sha else None
    return None


def index_states(env: GateEnv) -> dict[str, CampaignState]:
    """Campaign states with the sealed-query ledger applied (a retired sealed campaign becomes tune)."""
    events, _invalid, gap = store.read_index(env.sae / store.INDEX_FILE)
    if gap:
        raise GateError("campaigns.jsonl corrupted; rename it and re-run update", 1)
    first = campaign_state(events)
    sealed = [c for c, s in first.items() if s.split == "sealed"]
    return campaign_state(events, ledger.sealed_query_counts(env.pdir).counts_for(sealed))


def load_campaigns(
    env: GateEnv, split: Literal["tune", "sealed"], include_synth: bool
) -> list[ledger.LabelledCampaign]:
    """``ledger.labelled_campaigns`` with index corruption mapped to :class:`GateError`."""
    try:
        return ledger.labelled_campaigns(env.sae, env.key, env.runs, split, include_synth)
    except store.IndexCorrupt as e:
        raise GateError(str(e), 1) from e


def replay(cid: str, label: CampaignLabel, obs: list[Observation], cand: PolicyVersion, inc: PolicyVersion) -> Replay:
    """Detector and strategy traces for the candidate and the incumbent."""
    return Replay(cid, label, detector_trace(obs, cand), detector_trace(obs, inc),
                  strategy_trace(obs, cand), strategy_trace(obs, inc))  # fmt: skip


def switch_months(strat: list[tuple[int, Strategy]]) -> list[int]:
    """Month indices at which the strategy in force changed."""
    return [m for (_, a), (m, b) in pairwise(strat) if a != b]


def _chk(name: str, passed: bool, value: float | None = None, detail: str | None = None) -> GateCheck:
    return GateCheck(name=name, passed=passed, value=value, detail=detail)


def format_report(r: GateReport) -> str:
    """Human-readable report lines."""
    lines = [f"stage {r.stage} {'PASS' if r.passed else 'FAIL'} {r.candidate_sha256[:8]}"]
    for c in r.checks:
        val = f" = {c.value}" if c.value is not None else ""
        det = f"  ({c.detail})" if c.detail else ""
        lines.append(f"  {'ok  ' if c.passed else 'FAIL'} {c.name}{val}{det}")
    return "\n".join(lines)


def commit(env: GateEnv, report: GateReport, *, record: bool) -> int:
    """Write the evidence file, optionally append the ``stage*`` row, print the report; return the exit code."""
    data = canonical_json(report)
    digest = hashlib.sha256(data).hexdigest()
    try:
        store.write_atomic(env.pdir / "evidence" / f"{digest}.json", data)
        if record:
            action: Literal["stage1_pass", "stage1_fail", "stage2_pass", "stage2_fail"] = (
                f"stage{report.stage}_{'pass' if report.passed else 'fail'}"  # type: ignore[assignment]
            )
            row = RegistryRow(seq=len(env.rows), at=store.now(), action=action, sha256=report.candidate_sha256,
                              by="gate", evidence_sha256=digest)  # fmt: skip
            append_registry_row(env.pdir / "registry.jsonl", row)
    except RegistryError as e:
        raise GateError(str(e), 1) from e
    except OSError as e:
        raise GateError(f"write failed: {e}", 1) from e
    print(format_report(report))
    print(f"evidence {digest[:8]}" + ("" if record else f"  {SYNTH_NOTE}"))
    log.info("gate stage%d candidate=%s passed=%s recorded=%s", report.stage, report.candidate_sha256[:8],
             report.passed, record)  # fmt: skip
    return 0 if report.passed else 1


def _surface_diff(cand: PolicyVersion, parent: PolicyVersion) -> list[str]:
    a, b = cand.model_dump(mode="json"), parent.model_dump(mode="json")
    return sorted(k for k in a if k not in _MUTABLE and a[k] != b.get(k))


def _detector_hash(doc: PolicyVersion) -> str:
    return hashlib.sha256(canonical_json(doc.detector)).hexdigest()


def _sealed_budget(env: GateEnv, cand: PolicyVersion) -> GateCheck:
    mine = _detector_hash(cand)
    spent = {r.sha256 for r in env.rows if r.action in {"stage2_pass", "stage2_fail"}}
    for sha in sorted(spent):
        other = load_policy(env, sha)
        if other is not None and _detector_hash(other) == mine:
            return _chk("sealed_budget", False, detail=f"same detector as {sha[:8]}, which already used a sealed query")
    return _chk("sealed_budget", True)


def _static_checks(env: GateEnv, sha: str, cand: PolicyVersion, tune_states: list[CampaignState]) -> list[GateCheck]:
    out: list[GateCheck] = []
    parent = load_policy(env, cand.parent_sha256) if cand.parent_sha256 else None
    out.append(_chk("parent", parent is not None, detail=None if parent else "parent missing, unreadable or None"))
    if parent is None:
        out.append(_chk("mutable_surface", False, detail="no parent to diff against"))
    else:
        diff = _surface_diff(cand, parent)
        out.append(_chk("mutable_surface", not diff, detail=f"changed outside detector: {diff}" if diff else None))
    states = index_states(env)
    versions = {v for s in tune_states for v in s.game_versions}
    versions |= {v for c in cand.tuning.campaign_ids if c in states for v in states[c].game_versions}
    bad = sorted(v for v in versions if not pin_matches(cand.game_version_pin, v))
    out.append(_chk("pin", not bad, detail=f"{cand.game_version_pin} does not match {bad}" if bad else None))
    sealed = {c for c, s in states.items() if s.split == "sealed"}
    leak = sorted(set(cand.tuning.campaign_ids) & sealed)
    out.append(_chk("sealed_split", not leak, detail=f"tuned on sealed campaign(s) {leak}" if leak else None))
    seeds = [s for s in cand.tuning.synth_seeds if s >= SEED_LIMIT]
    out.append(_chk("seed_range", not seeds, detail=f"synth seed(s) >= {SEED_LIMIT}" if seeds else None))
    out.append(_sealed_budget(env, cand))
    return out


def _repo_suite(repo: Path) -> GateCheck:
    codes: list[tuple[str, int]] = []
    for name, cmd in REPO_COMMANDS:
        try:
            rc = subprocess.run(cmd, cwd=repo, capture_output=True, timeout=REPO_TIMEOUT_S, check=False).returncode
        except (OSError, subprocess.TimeoutExpired):
            rc = -1
        codes.append((name, rc))
    failed = sum(1 for _, rc in codes if rc != 0)
    return _chk("repo_suite", failed == 0, float(failed), " ".join(f"{n}={rc}" for n, rc in codes))


def _replay_checks(
    cand: PolicyVersion, inc: PolicyVersion, min_effect: float | None, tune: list[ledger.LabelledCampaign],
    repo: Path | None,
) -> list[GateCheck]:
    if not tune:
        msg = "no tune campaigns (live-labelled); nothing to replay"
        return [_chk(n, False, detail=msg) for n in ("gate_replay", "heldin_log_loss_delta", "feel_switches",
                                                      "feel_commitment")]  # fmt: skip
    reps: list[Replay] = []
    identical, error = True, None
    for lc in tune:
        try:
            first = replay(lc.state.campaign_id, lc.label, lc.observations, cand, inc)
            again = detector_trace(lc.observations, cand), strategy_trace(lc.observations, cand)
        except Exception as e:
            error = f"{lc.state.campaign_id}: {type(e).__name__}"
            log.exception("gate replay crashed")
            break
        identical = identical and (first.cand, first.cand_strat) == again
        reps.append(first)
    note = "repo suite not run" if repo is None else "repo suite see repo_suite"
    if error is not None or not identical:
        detail = f"replay error {error}" if error else "two replays differ (non-deterministic)"
        return [_chk("gate_replay", False, detail=f"{detail}; {note}")] + [
            _chk(n, False, detail="needs a clean replay") for n in ("heldin_log_loss_delta", "feel_switches",
                                                                    "feel_commitment")]  # fmt: skip
    out = [_chk("gate_replay", True, float(len(reps)), f"{len(reps)} campaign(s) replayed twice, identical; {note}")]
    sc = [(fold_score(r.cand, r.label), fold_score(r.inc, r.label)) for r in reps]
    delta = sum(c.log_loss - i.log_loss for c, i in sc) / len(sc)
    if min_effect is None:
        out.append(_chk("heldin_log_loss_delta", False, detail="no preregistration (min_effect unknown)"))
    elif not math.isfinite(delta):
        out.append(_chk("heldin_log_loss_delta", False, detail="non-finite log loss (unscored campaign)"))
    else:
        out.append(_chk("heldin_log_loss_delta", delta <= -min_effect, delta, HELDIN_DETAIL))
    ece_ok = sum(1 for c, _ in sc if c.ece <= ECE_MAX)
    ctr_ok = sum(1 for c, _ in sc if c.counter_ok)
    need = math.ceil(0.8 * len(sc))
    worst = max((c.ece for c, _ in sc if math.isfinite(c.ece)), default=0.0)
    out.append(_chk("v1_constraints", True, worst, f"informational; ECE<={ECE_MAX} on {ece_ok}/{len(sc)}, "
                    f"counter_ok on {ctr_ok}/{len(sc)} (V-1 wants >= {need})"))  # fmt: skip
    extra = [len(switch_months(r.cand_strat)) - len(switch_months(r.inc_strat)) for r in reps]
    out.append(_chk("feel_switches", all(x <= 1 for x in extra), float(max(extra)),
                    "candidate switches minus incumbent, worst campaign (<= 1)"))  # fmt: skip
    gaps = [b - a for r in reps for a, b in pairwise(switch_months(r.cand_strat))]
    out.append(_chk("feel_commitment", all(g >= COMMIT_MONTHS for g in gaps), float(min(gaps, default=0)),
                    f"shortest gap between candidate switches in months (>= {COMMIT_MONTHS})"))  # fmt: skip
    return out


def run_stage1(env: GateEnv, sha: str, repo: Path | None = None, include_synth: bool = False) -> GateReport:
    """Run the Stage 1 checks for ``sha`` and return the (unwritten) report.

    Raises:
        GateError: no propose row, or Stage 1 already recorded for the sha (exit 2).
    """
    acts = env.acts(sha)
    if "propose" not in acts:
        raise GateError(f"{sha[:8]} has no propose row", 2)
    if any(a.startswith("stage1_") for a in acts):
        raise GateError(f"{sha[:8]} already has a Stage 1 row", 2)
    pre = prereg_of(env, sha)
    inc_sha = pre.incumbent_sha256 if pre else env.incumbent
    cand = load_policy(env, sha)
    checks: list[GateCheck]
    if cand is None:
        checks = [_chk("schema", False, detail="policy file missing, invalid or name != policy_sha256")]
    else:
        checks = [_chk("schema", True)]
        inc = load_policy(env, inc_sha)
        tune = load_campaigns(env, "tune", include_synth)
        checks += _static_checks(env, sha, cand, [t.state for t in tune])
        checks.append(_chk("prereg", pre is not None, detail=None if pre else "no readable preregistration for sha"))
        if inc is None:
            checks.append(_chk("incumbent", False, detail="incumbent policy missing or invalid"))
        else:
            checks += _replay_checks(cand, inc, float(pre.min_effect) if pre else None, tune, repo)
        if repo is not None:
            checks.append(_repo_suite(repo))
    return GateReport(stage=1, candidate_sha256=sha, incumbent_sha256=inc_sha, gate_version=GATE_VERSION,
                      checks=tuple(checks), passed=all(c.passed for c in checks), created_at=store.now())  # fmt: skip


def cmd_stage1(args: argparse.Namespace, ctx: _Ctx) -> int:
    """``sae gate stage1 <sha> [--repo DIR] [--include-synth]``."""
    env = load_env(ctx)
    repo = Path(args.repo) if args.repo else None
    if repo is not None and not repo.is_dir():
        raise GateError(f"--repo {repo} is not a directory", 2)
    report = run_stage1(env, args.sha, repo, args.include_synth)
    return commit(env, report, record=not args.include_synth)
