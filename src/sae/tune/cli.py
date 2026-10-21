"""``sae tune detector``: fit a detector policy on the tune-split labelled campaigns (proposal 029 section 1).

Exit codes: 0 ok (also when the identical policy was already proposed), 1 runtime (I/O, corrupt index or registry,
no feasible grid point), 2 usage or refused (fewer than 5 tune campaigns, game running, parent unusable),
3 invalid config. Reads only ``split == "tune"`` campaigns; never touches config or a running daemon.
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from sae.bench.gate.metrics import round2, strategy_trace
from sae.bus.reader import iter_events
from sae.contracts.c5_bus import BUS_FILE, OutcomeEvent, StrategySelectedEvent
from sae.contracts.c10_profile import (
    PolicyVersion,
    RegistryError,
    RegistryRow,
    TuningManifest,
    append_registry_row,
    canonical_json,
    fold_registry,
    policy_sha256,
)
from sae.core.policy import load_builtin
from sae.profile import ledger, policy_cli, store
from sae.profile.cli import _context, _Ctx, _game_running, _lower_priority
from sae.tune import fit

log = logging.getLogger(__name__)

__all__ = ["main"]

MIN_CAMPAIGNS = 5
TUNER_VERSION = "0.1.0"
SYNTH_NOTE = "synth: diagnostic only, not V-1 verification"
_NOTES_MAX = 2048


def _err(msg: str, code: int) -> int:
    print(f"sae tune: {msg}", file=sys.stderr)
    return code


def _g(x: float) -> str:
    return f"{round2(x):g}"


def _fold_line(f: fit.Fold) -> str:
    s = f.score
    return f"{f.campaign_id} ll={_g(s.log_loss)} ece={_g(s.ece)} counter={'ok' if s.counter_ok else 'no'}"


def _table(title: str, folds: Sequence[fit.Fold]) -> str:
    return "\n".join([title, *(f"  {_fold_line(f)}" for f in folds)])


def _v3(campaigns: Sequence[ledger.LabelledCampaign], runs_dir: Path, policy: PolicyVersion) -> str:
    """V-3 diagnostic: recorded switches and reward spread, plus switches when replayed under ``policy``."""
    switches, rewards, replay = 0, [], 0
    for lc in campaigns:
        for rid in lc.state.run_ids:
            bus = runs_dir / rid / BUS_FILE
            if not bus.exists():
                continue
            for ev in iter_events(bus):
                if isinstance(ev, StrategySelectedEvent) and ev.payload.switched:
                    switches += 1
                elif isinstance(ev, OutcomeEvent):
                    rewards.append(ev.payload.reward)
        seq = [s for _, s in strategy_trace(lc.observations, policy)]
        replay += sum(1 for a, b in itertools.pairwise(seq) if a != b)
    spread = (max(rewards) - min(rewards)) if rewards else 0.0
    ok = switches > 1 and bool(rewards) and spread > 0.04
    return (
        f"V-3 (diagnostic): recorded switches={switches} replay switches={replay} rewards n={len(rewards)} "
        f"spread={_g(spread)} -> {'signal' if ok else 'no signal'} (style-change check is manual)"
    )


def _notes(cand: fit.Candidate, base: Sequence[fit.Fold], v3: str, synth: bool) -> str:
    p = cand.point
    parts = [
        f"LOCO kappa={p.kappa:g} rho={p.rho:g} clip={p.clip:g} thr={p.threshold:g} mean_ll={_g(cand.mean_log_loss)}"
    ]
    parts += [_fold_line(f) for f in cand.folds]
    parts.append(f"parent LOCO-free mean_ll={_g(sum(f.score.log_loss for f in base) / len(base))}")
    parts += [v3, "CUSUM: diagnostic only"]
    if synth:
        parts.append(SYNTH_NOTE)
    return "; ".join(parts)[:_NOTES_MAX]


def _parent(ctx: _Ctx) -> tuple[PolicyVersion, policy_cli._Reg] | int:
    reg = policy_cli._load(ctx)
    if isinstance(reg, int):
        return reg
    if reg.state.current is None:
        return load_builtin().doc, reg
    doc = policy_cli._load_policy(reg, reg.state.current)
    if doc is None:
        return _err(f"current policy {reg.state.current[:8]} missing, invalid or hash mismatch", 2)
    return doc, reg


def _bump(version: str) -> str:
    major, minor, _patch = version.split(".")
    return f"{major}.{int(minor) + 1}.0"


def _record(reg: policy_cli._Reg, sha: str, data: bytes) -> int:
    """Write the policy file and the single ``propose`` row; idempotent for an already proposed sha."""
    store.write_atomic(reg.pdir / f"{sha}.json", data)
    if policy_cli._has(reg, sha):
        print(f"{sha[:8]} is already proposed; nothing appended")
        return 0
    row = RegistryRow(seq=len(reg.rows), at=store.now(), action="propose", sha256=sha, by="tuner")
    try:
        fold_registry([*reg.rows, row], reg.builtin)
        written = append_registry_row(reg.path, row)
    except RegistryError as e:
        return _err(str(e), 1)
    print(f"propose {sha[:8]} (registry seq {written.seq}, by tuner)")
    return 0


def _run(args: argparse.Namespace, ctx: _Ctx, grid: Sequence[fit.GridPoint] | None) -> int:
    campaigns = ledger.labelled_campaigns(ctx.sae, ctx.key, ctx.runs_dir, "tune", args.include_synth)
    if len(campaigns) < MIN_CAMPAIGNS:
        return _err(f"need {MIN_CAMPAIGNS} labelled tune campaigns, have {len(campaigns)}", 2)
    got = _parent(ctx)
    if isinstance(got, int):
        return got
    parent, reg = got
    prepared = [fit.prepare(c.state.campaign_id, c.label, c.observations) for c in campaigns]
    cands = fit.loco(prepared, parent, grid)
    best = fit.choose(cands)
    base = fit.score_parent(prepared, parent)
    if best is None:
        print(_table("no feasible grid point; best unconstrained fold scores:", fit.best_any(cands).folds))
        return _err(
            f"no feasible point (ECE <= {fit.ECE_MAX}, counter on >= {fit.counter_min(len(prepared))} folds)", 1
        )
    ids = {p.campaign_id for p in prepared}
    events, _bad, _gap = store.read_index(ctx.index)
    # created_at: newest event of a folded campaign, so rows appended later for other campaigns cannot change the sha
    created_at = max(e.at for e in events if getattr(e, "campaign_id", None) in ids)
    synth = bool(args.include_synth)
    detector = fit.detector_for(prepared, parent, best.point)
    manifest = TuningManifest(
        method="loco_shrinkage", likelihood_source="synth_aligned" if synth else "fitted_real",
        campaign_ids=tuple(p.campaign_id for p in prepared), kappa=best.point.kappa, tuner_version=TUNER_VERSION,
    )  # fmt: skip
    draft = PolicyVersion(
        version=_bump(parent.version), created_at=created_at, game_version_pin=parent.game_version_pin,
        parent_sha256=policy_sha256(parent), detector=detector, fixed=parent.fixed, tuning=manifest,
    )  # fmt: skip
    v3 = _v3(campaigns, ctx.runs_dir, draft)
    doc = draft.model_copy(update={"notes": _notes(best, base, v3, synth)})
    sha = policy_sha256(doc)
    print(
        _table(
            f"LOCO folds at kappa={best.point.kappa:g} rho={best.point.rho:g} clip={best.point.clip:g} "
            f"thr={best.point.threshold:g} (mean log loss {_g(best.mean_log_loss)}):",
            best.folds,
        )
    )
    print(v3)
    if synth:
        print(SYNTH_NOTE)
    print(f"policy sha256: {sha}")
    try:
        return _record(reg, sha, canonical_json(doc))
    except OSError as e:
        return _err(f"write failed: {e}", 1)


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="TOML config file (or SAE_CONFIG)")
    common.add_argument("--preset", choices=["low_impact", "balanced", "dev"])
    common.add_argument("--set", dest="sets", action="append", default=[], metavar="K=V", help="dotted key override")
    common.add_argument("--runs-dir", help="runs directory")
    p = argparse.ArgumentParser(prog="sae tune", description="Detector tuning (C10).")
    sub = p.add_subparsers(dest="verb", required=True)
    d = sub.add_parser("detector", parents=[common], help="fit and propose a detector policy")
    d.add_argument("--player", help="player key (default: profile.player_key)")
    d.add_argument("--force", action="store_true", help="run even if Stellaris is running")
    d.add_argument("--include-synth", action="store_true", help="test only: use synth and replay campaigns")
    return p


def main(command: str, argv: list[str], *, grid: Sequence[fit.GridPoint] | None = None) -> int:
    """Entry point for ``sae tune`` (``command`` is ``"tune"``); ``grid`` is injectable for tests."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    ctx = _context(args)
    if isinstance(ctx, int):
        return ctx
    if args.player:
        ctx = dataclasses.replace(ctx, key=args.player)
    if not args.force and _game_running():
        return _err("Stellaris appears to be running; close it or pass --force", 2)
    _lower_priority()
    try:
        return _run(args, ctx, grid)
    except store.IndexCorrupt as e:
        return _err(str(e), 1)
    except OSError as e:
        return _err(f"I/O error: {e}", 1)
