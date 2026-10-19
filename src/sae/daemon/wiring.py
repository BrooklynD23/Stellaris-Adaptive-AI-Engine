"""Composition root: the ONLY T4 module that touches other teams' packages (lazily, via importlib).

Expected factories (UNVERIFIED until proposal 004 / T5 / T6 / T2 confirm; M0/integration would import each):

* ``sae.bus.open_sink(run_dir: Path, run_id: str, config: SaeConfig, *, resume: bool = False) -> BusSink``
  (writes ``run_started`` unless ``resume``; C5 section 1, C7 section 4.6)
* ``sae.governor.make_governor(config.governor, min_level, preset, emit_period=) -> Governor``
* ``sae.platform.make_sensors() -> Sensors`` and ``sae.platform.make_platform() -> Platform``
* ``sae.control.open_inbox(run_dir: Path, started_at=None) -> ControlInbox`` (``started_at`` C9 v1.1)
* ``sae.adapters.make_adapter(config, parser, god_sink, *, health=None) -> GameAdapter`` (live / release)

All factories are resolved before any is called. A missing module or attribute raises :class:`ComponentUnavailable`.
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import GameAdapter, GodSink, SaveParser
from sae.contracts.c8_config import SaeConfig
from sae.daemon.ports import AdapterFactory, DaemonDeps
from sae.daemon.runs import make_reexec
from sae.daemon.runs_dir import default_runs_dir

#: C6 section 1.8 adapter health hook (proposal 009).
HealthFn = Callable[[HealthKind, str | None], None]

__all__ = ["ComponentUnavailable", "ParserNotFound", "default_deps", "live_adapter_factory"]


class ParserNotFound(Exception):
    """C8 section 3 (proposal 014-T2): no ``sae-parse`` via config, PATH or the repo build."""

    def __init__(self) -> None:
        super().__init__("sae-parse not found, set paths.parser_exe")


class ComponentUnavailable(Exception):
    """A factory owned by another team is not importable yet."""

    def __init__(self, component: str, owner_team: str) -> None:
        super().__init__(f"{component} not available yet (owner {owner_team})")
        self.component = component
        self.owner_team = owner_team


def _factory(module: str, attr: str, owner: str) -> Any:
    try:
        mod = importlib.import_module(module)
    except ImportError as e:
        raise ComponentUnavailable(f"{module}.{attr}", owner) from e
    fn = getattr(mod, attr, None)
    if fn is None:
        raise ComponentUnavailable(f"{module}.{attr}", owner)
    return fn


def _takes(fn: Any, name: str) -> bool:
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def default_deps(config: SaeConfig, run_id: str, resume: bool = False) -> DaemonDeps:
    """Build the production :class:`DaemonDeps`; ``resume`` suppresses a second ``run_started``."""
    base = Path(config.paths.runs_dir) if config.paths.runs_dir else default_runs_dir(os.environ, sys.platform)
    run_dir = base / run_id
    open_sink = _factory("sae.bus", "open_sink", "T6")
    make_governor = _factory("sae.governor", "make_governor", "T5")
    make_sensors = _factory("sae.platform", "make_sensors", "T5")
    make_platform = _factory("sae.platform", "make_platform", "T5")
    open_inbox = _factory("sae.control", "open_inbox", "T6")
    resolve_parser_exe = _factory("sae.adapters.paths", "resolve_parser_exe", "T2")
    parser_exe: str | None = resolve_parser_exe(config.paths)  # C8 section 3, proposal 014-T2
    if parser_exe is None:
        raise ParserNotFound  # before anything is opened: no orphan run_started
    sink = open_sink(run_dir, run_id, config, resume=resume)
    governor = make_governor(config.governor, config.min_level, config.preset,
                             emit_period=config.bridge.emit_period)
    sensors = make_sensors()
    platform = make_platform()
    platform.lower_self_priority()  # once at daemon start (C7 section 8); never the game
    started_at = datetime.now(UTC)
    # C9 v1.1 section 3.1 (proposal 016): pass the session start once T6's open_inbox takes it.
    inbox = open_inbox(run_dir, started_at=started_at) if _takes(open_inbox, "started_at") else open_inbox(run_dir)
    return DaemonDeps(
        sink=sink, governor=governor, sensors=sensors, platform=platform, inbox=inbox,
        parser_exe=parser_exe, monotonic=time.monotonic,
        reexec=make_reexec(run_id, base), started_at=started_at,
    )


def live_adapter_factory(config: SaeConfig) -> AdapterFactory:
    """Adapter factory for live/release mode (called by the loop with the governed parser and bus sink)."""
    make = _factory("sae.adapters", "make_adapter", "T2")

    def build(parser: SaveParser, god_sink: GodSink, *, health: HealthFn | None = None) -> GameAdapter:
        adapter: GameAdapter = make(config, parser, god_sink, health=health)  # C6 section 1.8 (proposal 009)
        return adapter

    return build
