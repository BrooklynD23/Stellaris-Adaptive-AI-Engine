"""``sae ctl`` (C8 section 5, C9): write a command into a running daemon's inbox and wait for the result."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import re
import secrets
import sys
import time
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sae.contracts.c8_config import (
    CONFIG_ENV,
    CURRENT_RUN_FILE,
    SaeConfig,
    deep_merge,
    env_layer,
    resolve_config,
    set_layer,
)
from sae.contracts.c9_control import RESULT_TIMEOUT_S, ControlCommand, ControlResult
from sae.contracts.common import Strategy
from sae.control.runlive import is_run_live
from sae.daemon.runs_dir import default_runs_dir

log = logging.getLogger(__name__)

__all__ = ["RUN_ID_RE", "load_config", "main"]

RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-(live|replay|synth|release)-[0-9a-f]{4}$")  # C5 RunId
_POLL_S = 0.5


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--config", help="TOML config file (or SAE_CONFIG)")
    common.add_argument("--preset", choices=["low_impact", "balanced", "dev"])
    common.add_argument("--set", dest="sets", action="append", metavar="K=V", help="dotted key override")
    common.add_argument("--runs-dir", help="runs directory")
    common.add_argument("--run", help="run id (default: runs/CURRENT)")
    common.add_argument("--reason", help="free-text reason (max 500 chars)")
    p = argparse.ArgumentParser(prog="sae ctl", description="Control a running SAE daemon.", parents=[common])
    sub = p.add_subparsers(dest="sub", required=True)
    sub.add_parser("pause", parents=[common], help="stop deciding and emitting")
    sub.add_parser("resume", parents=[common], help="leave level S")
    force = sub.add_parser("force-strategy", parents=[common], help="override the counter table")
    force.add_argument("strategy", choices=[s.value for s in Strategy])
    force.add_argument("--months", type=int, default=24, help="override length in game months (1-120)")
    sub.add_parser("clear-force", parents=[common], help="clear an active override")
    sub.add_parser("status", parents=[common], help="read-only snapshot")
    return p


def load_config(ns: argparse.Namespace) -> SaeConfig:
    file_layer: dict[str, Any] = {}
    path = getattr(ns, "config", None) or os.environ.get(CONFIG_ENV)
    if path:
        with open(path, "rb") as f:
            file_layer = tomllib.load(f)
    cli: dict[str, Any] = {}
    if getattr(ns, "preset", None):
        cli["preset"] = ns.preset
    if getattr(ns, "runs_dir", None):
        cli["paths"] = {"runs_dir": ns.runs_dir}
    cli = deep_merge(cli, set_layer(getattr(ns, "sets", [])))
    return resolve_config(file_layer, env_layer(os.environ), cli)


def _fail(msg: str, code: int) -> int:
    print(f"sae ctl: {msg}", file=sys.stderr)
    return code


def _run_id(base: Path, explicit: str | None) -> str | None:
    """Validated run id from ``--run`` or ``runs/CURRENT``'s first token; ``None`` if unusable."""
    if explicit is not None:
        token = explicit
    else:
        try:
            parts = (base / CURRENT_RUN_FILE).read_text(encoding="utf-8").split()
        except OSError:
            return None
        if not parts:
            return None
        token = parts[0]
    return token if RUN_ID_RE.fullmatch(token) else None


def _build(ns: argparse.Namespace) -> dict[str, Any]:
    if ns.sub == "force-strategy":
        return {"cmd": "force_strategy", "strategy": ns.strategy, "months": ns.months}
    if ns.sub == "clear-force":
        return {"cmd": "force_strategy", "strategy": None}
    return {"cmd": ns.sub}


def _operator() -> str:
    try:
        name = getpass.getuser()
    except (OSError, KeyError, ImportError):
        name = ""
    return (name or "unknown")[:64]


def _command_id(now: datetime, inbox: Path, done: Path, sleep: Callable[[float], None],
                clock: Callable[[], datetime]) -> tuple[str, datetime]:
    """``c-<UTC>-<6 hex>``; regenerated after 1 s while inbox/ or done/ holds the same ``c-<ts>`` prefix."""
    while True:
        prefix = f"c-{now.strftime('%Y%m%dT%H%M%SZ')}"
        taken = any(
            n.lstrip(".").startswith(prefix) for d in (inbox, done) if d.is_dir() for n in os.listdir(d)
        )
        if not taken:
            return f"{prefix}-{secrets.token_hex(3)}", now
        sleep(1.0)
        now = clock()


def _print_result(r: ControlResult) -> None:
    lines = [f"command_id: {r.command_id}", f"accepted: {str(r.accepted).lower()}"]
    if r.error:
        lines.append(f"error: {r.error}")
    if r.detail:
        lines.append(f"detail: {r.detail}")
    if r.applied_at is not None:
        lines.append(f"applied_at: {r.applied_at}")
    if r.status is not None:
        for key, value in r.status.model_dump(mode="json").items():
            lines.append(f"{key}: {value}")
    print("\n".join(lines))


def _wait(path: Path, mono: Callable[[], float], sleep: Callable[[float], None]) -> ControlResult | None:
    deadline = mono() + RESULT_TIMEOUT_S
    while True:
        try:
            return ControlResult.model_validate_json(path.read_bytes())
        except (OSError, ValidationError):
            pass  # absent, or (non-atomic writer) half-written: keep polling
        if mono() >= deadline:
            return None
        sleep(_POLL_S)


def main(
    command: str,
    argv: list[str],
    *,
    mono: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    live: Callable[[Path, str], bool] = lambda base, rid: is_run_live(base, rid, grace_s=0.0),
) -> int:
    """Entry point called by ``sae.cli``. Exit 0 accepted, 1 rejected/timeout/no daemon, 2 usage, 3 config."""
    try:
        ns = _parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    try:
        cfg = load_config(ns)
    except (ValidationError, ValueError, tomllib.TOMLDecodeError, OSError) as e:
        return _fail(f"invalid config: {e}", 3)
    base = Path(cfg.paths.runs_dir) if cfg.paths.runs_dir else default_runs_dir(os.environ, sys.platform)
    explicit = getattr(ns, "run", None)
    run_id = _run_id(base, explicit)
    if run_id is None:
        return _fail("invalid --run value" if explicit is not None else "no running daemon", 1)
    control = base / run_id / "control"
    inbox, done = control / "inbox", control / "done"
    if not inbox.is_dir() or not live(base, run_id):  # C9 2.1 (proposal 016): never leave a command for a later daemon
        return _fail("no running daemon", 1)
    stamp = now()
    try:
        cid, issued = _command_id(stamp, inbox, done, sleep, now)
        cmd = ControlCommand.model_validate(
            {
                "command_id": cid,
                "issued_at": issued,
                "operator": _operator(),
                "reason": getattr(ns, "reason", ""),
                "command": _build(ns),
            }
        )
    except ValidationError as e:
        return _fail(f"invalid command: {e.errors(include_url=False)[0]['msg']}", 2)
    except OSError as e:
        return _fail(f"cannot write command: {e}", 1)
    tmp = inbox / f".{cid}.json.tmp"
    try:
        tmp.write_text(cmd.model_dump_json(), encoding="utf-8")
        os.replace(tmp, inbox / f"{cid}.json")
    except OSError as e:
        return _fail(f"cannot write command: {e}", 1)
    result = _wait(done / f"{cid}.result.json", mono, sleep)
    if result is None:
        return _fail("no result; is the daemon running?", 1)
    _print_result(result)
    return 0 if result.accepted else 1
