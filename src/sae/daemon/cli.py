"""``sae daemon`` (C8 section 5): config resolution, run dir, CURRENT lock, then the engine."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sae.contracts.c6_adapter import AdapterMode
from sae.contracts.c8_config import (
    CONFIG_ENV,
    RESOLVED_FILE,
    SaeConfig,
    deep_merge,
    env_layer,
    new_run_id,
    resolve_config,
    set_layer,
)
from sae.core.state import EngineState
from sae.daemon import wiring
from sae.daemon.loop import run_engine
from sae.daemon.runs import RunLockError, acquire_current, release_current, write_resolved
from sae.daemon.runs_dir import default_runs_dir
from sae.daemon.statefile import StateFileError, load_state

log = logging.getLogger(__name__)

__all__ = ["main"]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sae daemon", description="Run the SAE daemon.")
    p.add_argument("--config", help="TOML config file (or SAE_CONFIG)")
    p.add_argument("--preset", choices=["low_impact", "balanced", "dev"])
    p.add_argument("--set", dest="sets", action="append", default=[], metavar="K=V", help="dotted key override")
    p.add_argument("--runs-dir", help="runs directory")
    p.add_argument("--adapter", choices=["live", "release"])
    p.add_argument("--resume", metavar="RUN_ID", help="resume a run (only --runs-dir may accompany it)")
    return p


def _config(args: argparse.Namespace) -> SaeConfig:
    file_layer: dict[str, Any] = {}
    path = args.config or os.environ.get(CONFIG_ENV)
    if path:
        with open(path, "rb") as f:
            file_layer = tomllib.load(f)
    cli: dict[str, Any] = {}
    if args.preset:
        cli["preset"] = args.preset
    if args.adapter:
        cli["adapter"] = args.adapter
    if args.runs_dir:
        cli["paths"] = {"runs_dir": args.runs_dir}
    cli = deep_merge(cli, set_layer(args.sets))
    return resolve_config(file_layer, env_layer(os.environ), cli)


def _base(cfg: SaeConfig) -> Path:
    return Path(cfg.paths.runs_dir) if cfg.paths.runs_dir else default_runs_dir(os.environ, sys.platform)


def _fail(msg: str, code: int) -> int:
    print(f"sae daemon: {msg}", file=sys.stderr)
    return code


def _prepare(args: argparse.Namespace) -> tuple[SaeConfig, str, EngineState | None] | int:
    """Resolve config and run id; return an exit code on failure."""
    if args.resume is None:
        try:
            cfg = _config(args)
        except (ValidationError, ValueError, tomllib.TOMLDecodeError, OSError) as e:
            return _fail(f"invalid config: {e}", 3)
        return cfg, new_run_id(cfg.adapter, datetime.now(UTC)), None
    base = Path(args.runs_dir) if args.runs_dir else default_runs_dir(os.environ, sys.platform)
    run_dir = base / args.resume
    try:
        cfg = SaeConfig.model_validate_json((run_dir / RESOLVED_FILE).read_text(encoding="utf-8"))
        state = load_state(run_dir / "state.json")
    except (OSError, ValueError, StateFileError) as e:  # ValidationError is a ValueError
        return _fail(f"cannot resume {args.resume}: {e}", 1)
    return cfg.model_copy(update={"paths": cfg.paths.model_copy(update={"runs_dir": str(base)})}), args.resume, state


def main(command: str, argv: list[str]) -> int:
    """Entry point called by ``sae.cli``. Returns a C8 exit code (0 ok, 1 runtime, 2 usage, 3 config)."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    if args.resume is not None and any(
        (args.config, args.preset, args.sets, args.adapter)
    ):
        return _fail("--resume only accepts --runs-dir", 2)
    prepared = _prepare(args)
    if isinstance(prepared, int):
        return prepared
    cfg, run_id, state = prepared
    if cfg.adapter not in (AdapterMode.LIVE, AdapterMode.RELEASE):
        return _fail(f"adapter {cfg.adapter.value} is not runnable here (use sae replay / sae synth)", 2)
    base = _base(cfg)
    run_dir = base / run_id
    try:
        factory = wiring.live_adapter_factory(cfg)
        acquire_current(base, run_id, args.resume is not None)
    except wiring.ComponentUnavailable as e:
        return _fail(str(e), 1)
    except RunLockError as e:
        return _fail(str(e), 1)
    try:
        for sub in ("control/inbox", "control/done"):
            (run_dir / sub).mkdir(parents=True, exist_ok=True)
        deps = wiring.default_deps(cfg, run_id, resume=state is not None)
        if state is None:
            write_resolved(run_dir, cfg)
        code = asyncio.run(run_engine(cfg, factory, run_id, deps, run_dir, state))
    except (wiring.ComponentUnavailable, wiring.ParserNotFound, wiring.PolicyChanged) as e:
        code = _fail(str(e), 1)
    except Exception as e:  # no traceback may escape the CLI
        log.exception("daemon failed")
        code = _fail(f"{type(e).__name__}: {e}", 1)
    finally:
        release_current(base, run_id)
    return code
