"""``sae gate stage1|stage2`` (proposal 029 section 1; C10 sections 5-6; owner T7).

Exit codes: 0 pass, 1 stage fail or I/O error, 2 usage or refused, 3 invalid config (C8 section 5), 4 no result by
design (game-paired Stage 2). The CLI never edits config, never starts a daemon and writes only under
``<sae_dir>/policies`` (plus ``--out`` for a draft protocol).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys

from sae.bench.gate.stage1 import GateError, cmd_stage1
from sae.bench.gate.stage2 import cmd_stage2
from sae.profile.cli import _context

log = logging.getLogger(__name__)

__all__ = ["main"]

_HEX64 = re.compile(r"[0-9a-f]{64}")


def _sha(text: str) -> str:
    if not _HEX64.fullmatch(text):
        raise argparse.ArgumentTypeError("expected a full 64-character lowercase hex sha256")
    return text


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("sha", type=_sha)
    common.add_argument("--config", help="TOML config file (or SAE_CONFIG)")
    common.add_argument("--preset", choices=["low_impact", "balanced", "dev"])
    common.add_argument("--set", dest="sets", action="append", default=[], metavar="K=V", help="dotted key override")
    common.add_argument("--runs-dir", help="runs directory")
    common.add_argument("--include-synth", action="store_true", help="use synth campaigns: diagnostic, no registry row")
    p = argparse.ArgumentParser(prog="sae gate", description="Release gate (C10).")
    sub = p.add_subparsers(dest="verb", required=True)
    s1 = sub.add_parser("stage1", parents=[common])
    s1.add_argument("--repo", help="checkout in which to run pytest, mypy --strict and ruff")
    s2 = sub.add_parser("stage2", parents=[common])
    s2.add_argument("--objective", required=True, choices=["detector_replay", "beat_player", "challenge_band"])
    s2.add_argument("--out", help="draft protocol file (beat_player, challenge_band)")
    return p


def main(command: str, argv: list[str]) -> int:
    """Entry point for ``sae gate`` (``command`` is ``"gate"``)."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    ctx = _context(args)
    if isinstance(ctx, int):
        return ctx
    try:
        return cmd_stage1(args, ctx) if args.verb == "stage1" else cmd_stage2(args, ctx)
    except GateError as e:
        print(f"sae gate: {e}", file=sys.stderr)
        log.warning("gate refused: %s (exit %d)", e, e.code)
        return e.code
