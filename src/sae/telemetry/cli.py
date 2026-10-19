"""``sae telemetry`` (C8 section 5): bus -> Rerun (gRPC URL or ``.rrd``). Never spawns a viewer."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from sae.contracts.c7_governor import TelemetryTier
from sae.control.cli import RUN_ID_RE, load_config
from sae.control.runlive import is_run_live
from sae.daemon.runs_dir import default_runs_dir
from sae.telemetry.stream import RerunSink, Sink, run_stream

log = logging.getLogger(__name__)

__all__ = ["main"]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sae telemetry", description="Stream a run's bus to Rerun (no viewer is spawned).")
    p.add_argument("--run", required=True, help="run id")
    dest = p.add_mutually_exclusive_group()
    dest.add_argument("--rerun-url", help="rerun+http://HOST:9876/proxy")
    dest.add_argument("--rrd", help="write a .rrd file")
    p.add_argument("--config", help="TOML config file (or SAE_CONFIG)")
    p.add_argument("--preset", choices=["low_impact", "balanced", "dev"])
    p.add_argument("--set", dest="sets", action="append", default=[], metavar="K=V", help="dotted key override")
    p.add_argument("--runs-dir", help="runs directory")
    return p


def _fail(msg: str, code: int) -> int:
    print(f"sae telemetry: {msg}", file=sys.stderr)
    return code


def main(command: str, argv: list[str], *, exit_hard: Callable[[int], object] = os._exit) -> int:
    """Entry point called by ``sae.cli``. Exit 0 done, 1 runtime/missing extra, 2 usage, 3 config."""
    try:
        ns = _parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    try:
        cfg = load_config(ns)
    except (ValidationError, ValueError, tomllib.TOMLDecodeError, OSError) as e:
        return _fail(f"invalid config: {e}", 3)
    if not RUN_ID_RE.fullmatch(ns.run):
        return _fail("invalid --run value", 2)
    tier = cfg.telemetry.tier
    if tier is TelemetryTier.T0:
        print("tier T0: bus only, nothing to stream")
        return 0
    base = Path(cfg.paths.runs_dir) if cfg.paths.runs_dir else default_runs_dir(os.environ, sys.platform)
    run_dir = base / ns.run
    if not (run_dir / "bus.jsonl").is_file():
        return _fail(f"no bus for run {ns.run}", 1)
    if not cfg.telemetry.enabled:
        print("sae telemetry: telemetry.enabled is false in config; running because invoked explicitly",
              file=sys.stderr)
    url = ns.rerun_url or (None if ns.rrd else cfg.telemetry.rerun_url)
    target = url or ns.rrd or cfg.telemetry.rrd_path or str(run_dir / "telemetry.rrd")
    try:
        sink: Sink = RerunSink(ns.run, target, url=url is not None)
    except ImportError:
        return _fail("install the telemetry extra: uv sync --extra telemetry", 1)
    except (RuntimeError, OSError) as e:
        return _fail(f"cannot open {target}: {e}", 1)
    code = 1
    try:
        code = run_stream(run_dir, sink, tier, is_live=lambda: is_run_live(base, ns.run))
    except KeyboardInterrupt:
        code = 130
    except (RuntimeError, OSError) as e:
        code = _fail(f"stream failed: {e}", 1)
    except Exception:
        log.exception("telemetry stream crashed")
        code = 1
    finally:
        # rerun's gRPC thread can keep the interpreter alive when the viewer is unreachable: exit hard.
        sys.stdout.flush()
        sys.stderr.flush()
        logging.shutdown()
        exit_hard(code)
    return code
