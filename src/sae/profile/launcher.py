"""``sae play`` (proposal 025 D5): refresh the profile, then start the daemon. Spawns nothing else."""

from __future__ import annotations

import argparse
import subprocess
import sys

from sae.contracts.c8_config import SaeConfig
from sae.profile.cli import _CONFIG_ERRORS, config_from_args

__all__ = ["main"]

UPDATE_TIMEOUT_S = 600


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sae play", description="Update the profile, then run the SAE daemon.")
    p.add_argument("--config", help="TOML config file (or SAE_CONFIG)")
    p.add_argument("--preset", choices=["low_impact", "balanced", "dev"])
    p.add_argument("--set", dest="sets", action="append", default=[], metavar="K=V", help="dotted key override")
    p.add_argument("--runs-dir", help="runs directory")
    p.add_argument("--adapter", choices=["live", "release"])
    p.add_argument("--resume", metavar="RUN_ID", help="resume a run")
    return p


def _update_argv(args: argparse.Namespace) -> list[str]:
    out = [sys.executable, "-m", "sae", "profile", "update"]
    if args.config:
        out += ["--config", args.config]
    if args.preset:
        out += ["--preset", args.preset]
    for item in args.sets:
        out += ["--set", item]
    if args.runs_dir:
        out += ["--runs-dir", args.runs_dir]
    return out


def _run_update(args: argparse.Namespace) -> None:
    reason: str | None = None
    try:
        done = subprocess.run(_update_argv(args), timeout=UPDATE_TIMEOUT_S, check=False)
        if done.returncode != 0:
            reason = f"exit code {done.returncode}"
    except (OSError, subprocess.TimeoutExpired) as e:
        reason = type(e).__name__ if isinstance(e, subprocess.TimeoutExpired) else str(e)
    if reason is not None:
        print(f"sae play: profile update failed ({reason}); starting the daemon anyway", file=sys.stderr)


def main(command: str, argv: list[str]) -> int:
    """Entry point for ``sae play``; the daemon's exit code is returned."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    cfg: SaeConfig | None = None
    try:
        cfg = config_from_args(args)
    except _CONFIG_ERRORS as e:  # any config problem: say so, skip the update, let the daemon report it
        print(f"sae play: invalid config ({e}); skipping the profile update", file=sys.stderr)
    if cfg is not None and cfg.profile.mode != "off":
        _run_update(args)
    from sae.daemon.cli import main as daemon_main

    return daemon_main("daemon", argv)
