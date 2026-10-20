"""``sae replay`` and ``sae synth`` (C8 section 5): resolve config, build an adapter factory, run the daemon."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import os
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sae.adapters._pull import parse_ack_model
from sae.adapters.health import HealthFn, log_health
from sae.adapters.paths import resolve_paths
from sae.adapters.replay import ReplayAdapter, parse_speed
from sae.adapters.synth import SynthAdapter, parse_schedule
from sae.contracts.c6_adapter import AdapterMode, GameAdapter, GodSink, SaveParser
from sae.contracts.c8_config import (
    CONFIG_ENV,
    SaeConfig,
    deep_merge,
    env_layer,
    new_run_id,
    resolve_config,
    set_layer,
)
from sae.contracts.common import normalize_game_date

log = logging.getLogger(__name__)

LOCAL_CONFIG = "sae.toml"

__all__ = ["main"]


def _parser(command: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"sae {command}", description=f"Run the SAE daemon on a {command} adapter.")
    p.add_argument("--config", help="TOML config file (or SAE_CONFIG)")
    p.add_argument("--preset", choices=["low_impact", "balanced", "dev"])
    p.add_argument("--set", dest="sets", action="append", default=[], metavar="K=V", help="dotted key override")
    p.add_argument("--runs-dir", help="runs directory")
    if command == "replay":
        p.add_argument("session_dir", help="recorded session directory")
        p.add_argument("--speed", default="max", help="realtime | <N>x | max")
        p.add_argument("--from", dest="from_", metavar="DATE", help="yield nothing before this game date")
        p.add_argument("--stop-at", metavar="DATE", help="end after the first observation past this date")
        p.add_argument("--ack-model", default="echo", help="echo | drop:<p> | delay:<months> | from_recording")
    else:
        p.add_argument("--archetype", default="balanced", help="a[@year][,a@year...]")
        p.add_argument("--years", type=int, default=30)
        p.add_argument("--seed", type=int, default=None, help="default: engine.seed")
        p.add_argument("--record", metavar="DIR", help="record the session (replayable); DIR must be empty or new")
    return p


def _config(args: argparse.Namespace, mode: AdapterMode) -> SaeConfig:
    file_layer: dict[str, Any] = {}
    path = args.config or os.environ.get(CONFIG_ENV)
    if not path and Path(LOCAL_CONFIG).is_file():  # C8 section 1: ./sae.toml if it exists
        path = LOCAL_CONFIG
    if path:
        with open(path, "rb") as f:
            file_layer = tomllib.load(f)
    cli: dict[str, Any] = {}
    if args.preset:
        cli["preset"] = args.preset
    if args.runs_dir:
        cli["paths"] = {"runs_dir": args.runs_dir}
    cli = deep_merge(cli, set_layer(args.sets))
    cli["adapter"] = mode.value
    return resolve_config(file_layer, env_layer(os.environ), cli)


def _fail(command: str, msg: str, code: int) -> int:
    print(f"sae {command}: {msg}", file=sys.stderr)
    return code


def _validate(command: str, args: argparse.Namespace) -> str | None:
    """Usage errors (exit 2) as a message, or None when the arguments are fine."""
    try:
        if command == "replay":
            if not Path(args.session_dir).is_dir():
                return f"session dir not found: {args.session_dir}"
            parse_speed(args.speed)
            parse_ack_model(args.ack_model)
            for d in (args.from_, args.stop_at):
                if d is not None:
                    normalize_game_date(d)
        else:
            parse_schedule(args.archetype)
            if args.years < 1:
                return "--years must be >= 1"
            rec = None if args.record is None else Path(args.record)
            if rec is not None and rec.exists() and (not rec.is_dir() or any(rec.iterdir())):
                return f"--record dir exists and is not empty: {rec}"
    except ValueError as exc:
        return str(exc)
    return None


def main(command: str, argv: list[str]) -> int:
    """Entry point called by ``sae.cli``. Returns a C8 exit code (0 ok, 1 runtime, 2 usage, 3 config)."""
    if command not in ("replay", "synth"):
        return _fail(command, "unknown command", 2)
    try:
        args = _parser(command).parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    problem = _validate(command, args)
    if problem is not None:
        return _fail(command, problem, 2)
    mode = AdapterMode.REPLAY if command == "replay" else AdapterMode.SYNTH
    try:
        cfg = _config(args, mode)
    except (ValidationError, ValueError, tomllib.TOMLDecodeError, OSError) as e:
        return _fail(command, f"invalid config: {e}", 3)
    exe = resolve_paths(cfg.paths).parser_exe
    if exe is None and command == "synth":
        return _fail(command, "sae-parse not found (set paths.parser_exe or build parser/)", 1)
    parser_exe = None if exe is None else str(exe)
    log.info("sae %s: parser_exe = %s", command, parser_exe or "not found")  # C8 section 3: logged once at start
    if parser_exe is not None and cfg.paths.parser_exe is None:
        # the daemon's governed parser runs paths.parser_exe (default "sae-parse" on PATH): hand it the resolved build
        cfg = cfg.model_copy(update={"paths": cfg.paths.model_copy(update={"parser_exe": parser_exe})})
    return _run(command, args, cfg, parser_exe)


def _run(command: str, args: argparse.Namespace, cfg: SaeConfig, parser_exe: str | None) -> int:
    try:
        daemon = importlib.import_module("sae.daemon")
        if cfg.paths.runs_dir:
            base = Path(cfg.paths.runs_dir)
        else:
            base = importlib.import_module("sae.daemon.runs_dir").default_runs_dir(os.environ, sys.platform)
        run_id = new_run_id(cfg.adapter, datetime.now(UTC))
        scratch = base / run_id / "scratch"

        def factory(parser: SaveParser, god: GodSink, *, health: HealthFn | None = None) -> GameAdapter:
            health = health if health is not None else log_health  # C6 section 1.8: T4 passes the hook
            if command == "replay":
                return ReplayAdapter(
                    Path(args.session_dir), parser, god, health=health, scratch_dir=scratch, speed=args.speed,
                    from_=args.from_, stop_at=args.stop_at, ack_model=args.ack_model, seed=cfg.engine.seed,
                    run_id=run_id,
                )
            assert parser_exe is not None
            seed = cfg.engine.seed if args.seed is None else args.seed
            return SynthAdapter(
                parse_schedule(args.archetype), seed, args.years, parser, god,
                health=health, parser_exe=parser_exe, scratch_dir=scratch,
                record_dir=None if args.record is None else Path(args.record), record=cfg.record, run_id=run_id,
            )

        return int(asyncio.run(daemon.run(cfg, factory, run_id)))
    except Exception as e:  # no traceback may escape the CLI
        log.error("%s failed: %s: %s", command, type(e).__name__, e)
        return _fail(command, f"{type(e).__name__}: {e}", 1)
