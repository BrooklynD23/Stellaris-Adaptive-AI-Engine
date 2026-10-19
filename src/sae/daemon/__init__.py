"""sae.daemon (owner: T4): the daemon loop and its C8 entry point. Import-light on purpose."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from sae.contracts.c6_adapter import GameAdapter
from sae.contracts.c8_config import SaeConfig
from sae.daemon.loop import run_engine
from sae.daemon.ports import AdapterFactory
from sae.daemon.runs import RunLockError, acquire_current, release_current
from sae.daemon.runs_dir import default_runs_dir

__all__ = ["run", "run_engine"]


async def run(config: SaeConfig, adapter: GameAdapter | AdapterFactory, run_id: str) -> int:
    """C8 entry point (proposal 004): build default deps lazily and run the engine. Returns the exit code."""
    try:
        wiring = importlib.import_module("sae.daemon.wiring")
    except ImportError:
        print("sae daemon: wiring not available", file=sys.stderr)
        return 1
    base = Path(config.paths.runs_dir) if config.paths.runs_dir else default_runs_dir(os.environ, sys.platform)
    try:
        # C8 section 6: the daemon owns runs/CURRENT in every mode, so `sae ctl` reaches replay/synth runs too.
        acquire_current(base, run_id, resume=False)
    except RunLockError as e:
        print(f"sae daemon: {e}", file=sys.stderr)
        return 1
    try:
        deps = wiring.default_deps(config, run_id)
        return await run_engine(config, adapter, run_id, deps, base / run_id)
    finally:
        release_current(base, run_id)
