"""``sae policy propose|prereg|approve|reject|promote|rollback|pin|show`` (proposal 029 section 2, C10 sections 5-6).

Every human row goes through ``append_registry_row`` (TTY guard) plus a typed confirmation of the first 8 hex of
the policy sha. The CLI never edits config and never starts a daemon. Exit codes: 0 ok, 1 runtime (I/O, lock,
corrupt registry), 2 usage or refused, 3 invalid config.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from sae.contracts.c10_profile import (
    CHAIN,
    PolicyVersion,
    Preregistration,
    RegistryAction,
    RegistryError,
    RegistryRow,
    RegistryState,
    append_registry_row,
    canonical_json,
    fold_registry,
    pin_matches,
    policy_sha256,
)
from sae.core.policy import load_builtin
from sae.profile import ledger, store
from sae.profile.cli import _context, _Ctx
from sae.profile.index import campaign_state

log = logging.getLogger(__name__)

__all__ = ["main"]

MAX_POLICY_BYTES = 1024 * 1024
_HEX64 = re.compile(r"[0-9a-f]{64}")
_BREAKS = frozenset({"stage1_fail", "stage2_fail", "reject"})


def _err(msg: str, code: int) -> int:
    print(f"sae policy: {msg}", file=sys.stderr)
    return code


@dataclass(frozen=True)
class _Reg:
    """The registry as read for one command."""

    pdir: Path
    rows: list[RegistryRow]
    state: RegistryState
    builtin: str

    @property
    def path(self) -> Path:
        return self.pdir / "registry.jsonl"

    def acts(self, sha: str) -> list[str]:
        """Chain and break actions recorded for ``sha``, in order."""
        return [r.action for r in self.rows if r.sha256 == sha and (r.action in CHAIN or r.action in _BREAKS)]


def _read_rows(path: Path) -> list[RegistryRow]:
    rows: list[RegistryRow] = []
    if not path.exists():
        return rows
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(RegistryRow.model_validate_json(line))
        except ValueError:
            skipped += 1
    if skipped:
        log.warning("registry: skipped %d invalid line(s)", skipped)
    return rows


def _load(ctx: _Ctx) -> _Reg | int:
    pdir = ctx.sae / "policies"
    builtin = load_builtin().sha256
    try:
        rows = _read_rows(pdir / "registry.jsonl")
        state = fold_registry(rows, builtin)
    except (OSError, UnicodeDecodeError) as e:
        return _err(f"registry unreadable: {e}", 1)
    except RegistryError as e:
        return _err(f"registry error: {e}", 1)
    return _Reg(pdir, rows, state, builtin)


def _by() -> str:
    try:
        name = getpass.getuser()
    except (KeyError, OSError):
        name = ""
    return "human:" + (re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:64] or "user")


def _interactive() -> bool:
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def _commit(
    reg: _Reg,
    action: RegistryAction,
    sha: str,
    *,
    evidence: str | None = None,
    previous: str | None = None,
    reason: str | None = None,
    prepare: Callable[[], None] | None = None,
) -> int:
    """Validate, ask for the typed confirmation, run ``prepare`` (file writes), append one human row."""
    try:
        row = RegistryRow(
            seq=len(reg.rows), at=store.now(), action=action, sha256=sha, by=_by(), evidence_sha256=evidence,
            previous_sha256=previous, reason=reason,
        )  # fmt: skip
        fold_registry([*reg.rows, row], reg.builtin)  # dry run: refuse what the daemon would reject
    except ValidationError as e:
        return _err(f"invalid row: {e}", 2)
    except RegistryError as e:
        return _err(f"refused: {e}", 2)
    if not _interactive():
        return _err("human registry rows need an interactive terminal (TTY on stdin and stdout)", 2)
    try:
        typed = input(f"{action} {sha}: type the first 8 hex to confirm: ")
    except (EOFError, OSError):
        return _err("no confirmation read; nothing written", 2)
    if typed.strip().lower() != sha[:8]:
        return _err("confirmation does not match; nothing written", 2)
    try:
        fresh = _read_rows(reg.path)
        fold_registry([*fresh, row.model_copy(update={"seq": len(fresh)})], reg.builtin)
        if len(fresh) != len(reg.rows):
            raise RegistryError("registry length changed")
    except RegistryError:
        return _err("registry changed while waiting; re-run", 2)
    try:
        if prepare is not None:
            prepare()
        written = append_registry_row(reg.path, row)
    except RegistryError as e:
        return _err(str(e), 1)
    except OSError as e:
        return _err(f"write failed: {e}", 1)
    print(f"{action} {sha[:8]} (registry seq {written.seq})")
    return 0


def _write_evidence(reg: _Reg, data: bytes) -> str:
    """Write ``evidence/<sha256 of bytes>.json`` atomically (idempotent); return the sha."""
    digest = hashlib.sha256(data).hexdigest()
    store.write_atomic(reg.pdir / "evidence" / f"{digest}.json", data)
    return digest


def _has(reg: _Reg, sha: str) -> bool:
    return any(r.action == "propose" and r.sha256 == sha for r in reg.rows)


def _load_policy(reg: _Reg, sha: str) -> PolicyVersion | None:
    try:
        doc = PolicyVersion.model_validate_json((reg.pdir / f"{sha}.json").read_bytes())
    except (OSError, ValidationError):
        return None
    return doc if policy_sha256(doc) == sha else None


# --- verbs --------------------------------------------------------------------------------------------------


def _cmd_propose(args: argparse.Namespace, ctx: _Ctx) -> int:
    path = Path(args.file)
    try:
        if path.stat().st_size > MAX_POLICY_BYTES:
            return _err("policy file too large", 2)
        data = path.read_bytes()
    except OSError as e:
        return _err(f"cannot read {path}: {e}", 2)
    try:
        doc = PolicyVersion.model_validate_json(data)
    except ValidationError as e:
        return _err(f"invalid policy: {e}", 2)
    sha = policy_sha256(doc)
    if _HEX64.fullmatch(path.stem) and path.stem != sha:
        return _err(f"file name {path.stem[:8]} does not match its hash {sha[:8]}", 2)
    reg = _load(ctx)
    if isinstance(reg, int):
        return reg
    if _has(reg, sha):
        return _err(f"{sha[:8]} is already proposed", 2)
    return _commit(reg, "propose", sha, prepare=lambda: store.write_atomic(reg.pdir / f"{sha}.json", data))


def _cmd_prereg(args: argparse.Namespace, ctx: _Ctx) -> int:
    reg = _load(ctx)
    if isinstance(reg, int):
        return reg
    if not _has(reg, args.sha):
        return _err(f"{args.sha[:8]} has no propose row", 2)
    if "prereg" in reg.acts(args.sha):
        return _err(f"{args.sha[:8]} already has a prereg row", 2)
    try:
        prereg = Preregistration(
            candidate_sha256=args.sha, incumbent_sha256=reg.state.current or reg.builtin, kind="detector_replay",
            primary_metric=args.metric, min_effect=args.min_effect, non_inferiority_margin=args.margin,
            budget=args.budget, min_n=args.min_n, created_at=store.now(),
        )  # fmt: skip
    except ValidationError as e:
        return _err(f"invalid preregistration: {e}", 2)
    data = canonical_json(prereg)
    digest = hashlib.sha256(data).hexdigest()

    def write() -> None:
        _write_evidence(reg, data)

    return _commit(reg, "prereg", args.sha, evidence=digest, prepare=write)


def _cmd_approve(args: argparse.Namespace, ctx: _Ctx) -> int:
    reg = _load(ctx)
    if isinstance(reg, int):
        return reg
    if reg.acts(args.sha) != list(CHAIN[:-1]):
        return _err(f"{args.sha[:8]} needs propose, prereg, stage1_pass, stage2_pass in order, with no fail", 2)
    return _commit(reg, "approve", args.sha)


def _cmd_reject(args: argparse.Namespace, ctx: _Ctx) -> int:
    reg = _load(ctx)
    if isinstance(reg, int):
        return reg
    if not _has(reg, args.sha):
        return _err(f"{args.sha[:8]} has no propose row", 2)
    return _commit(reg, "reject", args.sha, reason=args.reason)


def _pin_mismatch(ctx: _Ctx, doc: PolicyVersion) -> list[str]:
    """Game versions of the policy's tuning campaigns that its ``game_version_pin`` does not match."""
    events, _invalid, _gap = store.read_index(ctx.sae / store.INDEX_FILE)
    states = campaign_state(events)
    versions = {v for cid in doc.tuning.campaign_ids if cid in states for v in states[cid].game_versions}
    return sorted(v for v in versions if not pin_matches(doc.game_version_pin, v))


def _cmd_promote(args: argparse.Namespace, ctx: _Ctx) -> int:
    reg = _load(ctx)
    if isinstance(reg, int):
        return reg
    if args.sha not in reg.state.approved:
        return _err(f"{args.sha[:8]} has no complete approved chain", 2)
    doc = _load_policy(reg, args.sha)
    if doc is None:
        return _err(f"policy file {args.sha[:8]}.json missing, invalid or hash mismatch", 2)
    if bad := _pin_mismatch(ctx, doc):
        return _err(f"game_version_pin {doc.game_version_pin} does not match campaign version(s) {bad}", 2)
    if (reg.state.current or reg.builtin) == args.sha:
        return _err(f"{args.sha[:8]} is already current", 2)
    return _commit(reg, "promote", args.sha, previous=reg.state.current)


def _rollback(reg: _Reg, target: str, reason: str | None) -> int:
    if (reg.state.current or reg.builtin) == target:
        return _err(f"{target[:8]} is already current", 2)
    return _commit(reg, "rollback", target, previous=reg.state.current, reason=reason)


def _cmd_rollback(args: argparse.Namespace, ctx: _Ctx) -> int:
    reg = _load(ctx)
    if isinstance(reg, int):
        return reg
    target: str | None = args.sha
    if target is None:
        moves = [r for r in reg.rows if r.action in {"promote", "rollback"}]
        if not moves:
            return _err("nothing to roll back: no promote or rollback row", 2)
        target = moves[-1].previous_sha256 or reg.builtin
    return _rollback(reg, target, args.reason)


def _cmd_pin(args: argparse.Namespace, ctx: _Ctx) -> int:
    reg = _load(ctx)
    if isinstance(reg, int):
        return reg
    code = 0 if (reg.state.current or reg.builtin) == reg.builtin else _rollback(reg, reg.builtin, "pin builtin")
    if code == 0:
        print('hard pin (survives later promotes): set policy.pin = "builtin" in your config')
    return code


def _cmd_show(_args: argparse.Namespace, ctx: _Ctx) -> int:
    reg = _load(ctx)
    if isinstance(reg, int):
        return reg
    print(f"built-in: {reg.builtin[:8]}")
    print(f"current: {(reg.state.current or reg.builtin)[:8]}{'' if reg.state.current else ' (built-in)'}")
    print("approved: " + (", ".join(sorted(s[:8] for s in reg.state.approved)) or "none"))
    shas = list(dict.fromkeys(r.sha256 for r in reg.rows if r.action in CHAIN or r.action in _BREAKS))
    for sha in shas:
        print(f"  {sha[:8]}  " + " > ".join(reg.acts(sha)))
    queries = ledger.sealed_query_counts(reg.pdir)
    print("sealed queries: " + (", ".join(f"{c}={n}" for c, n in sorted(queries.per_campaign.items())) or "none"))
    if queries.unreadable:
        print(f"unreadable stage2 evidence: {queries.unreadable} (counts against every sealed campaign)")
    return 0


# --- entry --------------------------------------------------------------------------------------------------


def _sha(text: str) -> str:
    if not _HEX64.fullmatch(text):
        raise argparse.ArgumentTypeError("expected a full 64-character lowercase hex sha256")
    return text


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="TOML config file (or SAE_CONFIG)")
    common.add_argument("--preset", choices=["low_impact", "balanced", "dev"])
    common.add_argument("--set", dest="sets", action="append", default=[], metavar="K=V", help="dotted key override")
    common.add_argument("--runs-dir", help="runs directory")
    p = argparse.ArgumentParser(prog="sae policy", description="Policy registry (C10).")
    sub = p.add_subparsers(dest="verb", required=True)
    sub.add_parser("propose", parents=[common]).add_argument("file")
    pr = sub.add_parser("prereg", parents=[common])
    pr.add_argument("sha", type=_sha)
    pr.add_argument("--metric", default="loco_log_loss", choices=["loco_log_loss", "detection_delay_months"])
    pr.add_argument("--min-effect", dest="min_effect", type=float, default=0.05)
    pr.add_argument("--margin", type=float, default=0.02)
    pr.add_argument("--budget", type=int, default=6)
    pr.add_argument("--min-n", dest="min_n", type=int, default=1)
    sub.add_parser("approve", parents=[common]).add_argument("sha", type=_sha)
    rj = sub.add_parser("reject", parents=[common])
    rj.add_argument("sha", type=_sha)
    rj.add_argument("--reason")
    sub.add_parser("promote", parents=[common]).add_argument("sha", type=_sha)
    rb = sub.add_parser("rollback", parents=[common])
    rb.add_argument("sha", nargs="?", type=_sha)
    rb.add_argument("--reason")
    sub.add_parser("pin", parents=[common]).add_argument("what", choices=["builtin"])
    sub.add_parser("show", parents=[common])
    return p


_HANDLERS = {
    "propose": _cmd_propose, "prereg": _cmd_prereg, "approve": _cmd_approve, "reject": _cmd_reject,
    "promote": _cmd_promote, "rollback": _cmd_rollback, "pin": _cmd_pin, "show": _cmd_show,
}  # fmt: skip


def main(command: str, argv: list[str]) -> int:
    """Entry point for ``sae policy`` (``command`` is ``"policy"``)."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    ctx = _context(args)
    if isinstance(ctx, int):
        return ctx
    return _HANDLERS[args.verb](args, ctx)
