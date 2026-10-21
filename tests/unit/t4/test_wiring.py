"""Composition root: unavailable components and a stubbed happy path."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from sae.contracts.c8_config import SaeConfig
from sae.daemon import wiring

from .fakes import FakeGovernor, FakeInbox, FakePlatform, FakeSensors, FakeSink


def test_missing_component_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate absent packages (None in sys.modules makes import raise ImportError).
    for name in ("sae.bus", "sae.adapters"):
        monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(wiring.ComponentUnavailable) as ei:
        wiring.default_deps(SaeConfig(), "r1")
    assert "not available yet (owner T6)" in str(ei.value)
    with pytest.raises(wiring.ComponentUnavailable, match="owner T2"):
        wiring.live_adapter_factory(SaeConfig())


def test_default_deps_with_stub_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sink = FakeSink()

    def mod(name: str, **attrs: Any) -> None:
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        monkeypatch.setitem(sys.modules, name, m)

    seen: dict[str, Any] = {}
    plat = FakePlatform()

    def gov(gcfg: Any, lvl: str, preset: str, *, emit_period: int | None = None) -> FakeGovernor:
        seen["emit_period"] = emit_period
        return FakeGovernor()

    mod("sae.bus", open_sink=lambda run_dir, run_id, cfg, resume=False: sink)
    mod("sae.governor", make_governor=gov)
    mod("sae.platform", make_sensors=lambda: FakeSensors(), make_platform=lambda: plat)
    mod("sae.control", open_inbox=lambda run_dir: FakeInbox())
    cfg = SaeConfig(paths={"runs_dir": str(tmp_path), "parser_exe": "px"},  # type: ignore[arg-type]
                    bridge={"emit_period": 3})  # type: ignore[arg-type]
    deps = wiring.default_deps(cfg, "r1")
    assert deps.sink is sink and deps.parser_exe == "px"
    assert seen["emit_period"] == 3  # C8 section 5.1 / C7 section 1
    assert plat.lowered == 1  # lower_self_priority once at start


def test_resume_passed_and_all_resolved_before_any_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def mod(name: str, **attrs: Any) -> None:
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        monkeypatch.setitem(sys.modules, name, m)

    mod("sae.bus", open_sink=lambda run_dir, run_id, cfg, resume=False: calls.append(resume) or FakeSink())
    mod("sae.governor", make_governor=lambda gcfg, lvl, preset, emit_period=None: FakeGovernor())
    mod("sae.platform", make_sensors=lambda: FakeSensors())  # make_platform missing
    mod("sae.control", open_inbox=lambda run_dir: FakeInbox())
    cfg = SaeConfig(paths={"runs_dir": str(tmp_path), "parser_exe": "px"})  # type: ignore[arg-type]
    with pytest.raises(wiring.ComponentUnavailable):
        wiring.default_deps(cfg, "r1", resume=True)
    assert calls == []  # sink never opened: no orphan run_started
    mod("sae.platform", make_sensors=lambda: FakeSensors(), make_platform=lambda: FakePlatform())
    wiring.default_deps(cfg, "r1", resume=True)
    assert calls == [True]


def _stub_others(monkeypatch: pytest.MonkeyPatch, opened: list[str]) -> None:
    def mod(name: str, **attrs: Any) -> None:
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        monkeypatch.setitem(sys.modules, name, m)

    mod("sae.bus", open_sink=lambda run_dir, run_id, cfg, resume=False: opened.append("sink") or FakeSink())
    mod("sae.governor", make_governor=lambda gcfg, lvl, preset, emit_period=None: FakeGovernor())
    mod("sae.platform", make_sensors=lambda: FakeSensors(), make_platform=lambda: FakePlatform())
    mod("sae.control", open_inbox=lambda run_dir: FakeInbox())


def test_parser_exe_from_t2_resolver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Proposal 014-T2: wiring uses sae.adapters.paths.resolve_parser_exe, never a bare 'sae-parse'."""
    opened: list[str] = []
    _stub_others(monkeypatch, opened)
    seen: list[Any] = []
    m = types.ModuleType("sae.adapters.paths")
    m.resolve_parser_exe = lambda paths: seen.append(paths) or "/opt/sae-parse"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sae.adapters.paths", m)
    cfg = SaeConfig(paths={"runs_dir": str(tmp_path)})  # type: ignore[arg-type]
    assert wiring.default_deps(cfg, "r1").parser_exe == "/opt/sae-parse"
    assert seen == [cfg.paths]


def test_parser_not_found_raises_before_opening_anything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    _stub_others(monkeypatch, opened)
    m = types.ModuleType("sae.adapters.paths")
    m.resolve_parser_exe = lambda paths: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sae.adapters.paths", m)
    with pytest.raises(wiring.ParserNotFound, match=r"sae-parse not found, set paths\.parser_exe"):
        wiring.default_deps(SaeConfig(paths={"runs_dir": str(tmp_path)}), "r1")  # type: ignore[arg-type]
    assert opened == []


def test_open_inbox_gets_session_start_when_supported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """C9 v1.1 section 3.1: the daemon's session start goes to T6's inbox and into DaemonDeps."""
    _stub_others(monkeypatch, [])
    got: dict[str, Any] = {}

    def open_inbox(run_dir: Path, started_at: Any = None) -> FakeInbox:
        got["started_at"] = started_at
        return FakeInbox()

    m = types.ModuleType("sae.control")
    m.open_inbox = open_inbox  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sae.control", m)
    deps = wiring.default_deps(SaeConfig(paths={"runs_dir": str(tmp_path), "parser_exe": "px"}), "r1")  # type: ignore[arg-type]
    assert got["started_at"] is not None and got["started_at"] == deps.started_at
