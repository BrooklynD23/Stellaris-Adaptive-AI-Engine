"""``sae play`` (proposals 025 D5, 033 D2): run the daemon, then refresh the profile. Spawns nothing else.

The daemon runs in this process. A re-exec (C7 section 4.6) on POSIX is ``os.execv``: this process becomes the
resumed ``sae daemon`` and the update never runs. On Windows the re-exec spawns a child, writes its pid to
``runs/CURRENT`` and returns 0; that CURRENT, still naming a live ``sae`` process other than this one, is the
signal to skip the update (C8 section 5 consequence 4).

UNVERIFIED (A-48): closing the console window on Windows may kill this launcher before the update runs;
M0 would test it by closing the window during a run and checking for the update's index rows.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from sae.contracts.c8_config import CURRENT_RUN_FILE, SaeConfig
from sae.daemon.runs import _is_sae_alive, _read_current
from sae.daemon.runs_dir import default_runs_dir
from sae.profile.cli import _CONFIG_ERRORS, config_from_args

__all__ = ["main"]

UPDATE_TIMEOUT_S = 600


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sae play", description="Run the SAE daemon, then update the profile.")
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


_HINT = "sae play: run sae profile update after closing the game"


def _daemon_runs_dir(args: argparse.Namespace, cfg: SaeConfig) -> Path:
    """The runs directory the daemon used (``sae.daemon.cli``: ``--resume`` ignores the config's runs_dir)."""
    if args.resume is None and cfg.paths.runs_dir:
        return Path(cfg.paths.runs_dir)
    return Path(args.runs_dir) if args.runs_dir else default_runs_dir(os.environ, sys.platform)


def _run_still_alive(runs_dir: Path) -> bool:
    """True when ``CURRENT`` names a live ``sae`` process other than this one (a Windows re-exec child)."""
    cur = _read_current(runs_dir / CURRENT_RUN_FILE)
    return cur is not None and cur[1] != os.getpid() and _is_sae_alive(cur[1])


def _run_update(args: argparse.Namespace) -> None:
    reason: str | None = None
    print("sae play: updating the profile (can take up to 10 minutes)...", flush=True)
    try:
        # stdin=DEVNULL: the update must never prompt; with no TTY ``_ask_split`` returns None and the
        # candidate stays pending for the next interactive update. Never ``--force``.
        done = subprocess.run(_update_argv(args), stdin=subprocess.DEVNULL, timeout=UPDATE_TIMEOUT_S, check=False)
        if done.returncode == 1:
            print(_HINT, file=sys.stderr)  # usual cause: the game is still running
        if done.returncode != 0:
            reason = f"exit code {done.returncode}"
    except (OSError, subprocess.TimeoutExpired) as e:
        reason = type(e).__name__ if isinstance(e, subprocess.TimeoutExpired) else str(e)
    if reason is not None:
        print(f"sae play: profile update failed ({reason})", file=sys.stderr)


def main(command: str, argv: list[str]) -> int:
    """Entry point for ``sae play``: daemon, then update unless a re-exec child lives. Returns the daemon code."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    cfg: SaeConfig | None = None
    try:
        cfg = config_from_args(args)
    except _CONFIG_ERRORS as e:  # any config problem: say so, skip the update, let the daemon report it
        print(f"sae play: invalid config ({e}); skipping the profile update", file=sys.stderr)
    from sae.daemon.cli import main as daemon_main

    code = daemon_main("daemon", argv)
    if cfg is None or cfg.profile.mode == "off":
        return code
    if _run_still_alive(_daemon_runs_dir(args, cfg)):
        print("sae play: the daemon re-executed and is still running; " + _HINT.removeprefix("sae play: "),
              file=sys.stderr)
        return code
    _run_update(args)
    return code
