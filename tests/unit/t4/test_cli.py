"""``sae daemon`` CLI (U7): usage, config errors, resume, unavailable components, fake session."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from sae.daemon import cli, wiring

from .fakes import FakeAdapter, FakePlatform, make_deps


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    for k in list(os.environ):
        if k.startswith("SAE_"):
            monkeypatch.delenv(k)


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main("daemon", ["--help"]) == 0
    assert "--resume" in capsys.readouterr().out


def test_unknown_flag_is_usage() -> None:
    assert cli.main("daemon", ["--bogus"]) == 2


def test_resume_with_preset_is_usage(tmp_path: Path) -> None:
    assert cli.main("daemon", ["--resume", "r1", "--preset", "dev", "--runs-dir", str(tmp_path)]) == 2


def test_invalid_config_exits_3(tmp_path: Path) -> None:
    assert cli.main("daemon", ["--runs-dir", str(tmp_path), "--set", "engine.learning_rate=1"]) == 3


def test_bad_set_syntax_exits_3(tmp_path: Path) -> None:
    assert cli.main("daemon", ["--runs-dir", str(tmp_path), "--set", "nokv"]) == 3


def test_resume_missing_state_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "r1").mkdir()
    assert cli.main("daemon", ["--resume", "r1", "--runs-dir", str(tmp_path)]) == 1
    assert "cannot resume" in capsys.readouterr().err


def test_set_layer_is_the_canonical_c8_builder() -> None:
    from sae.contracts import c8_config  # proposal 016: one --set builder, no local copy

    assert cli.set_layer is c8_config.set_layer
    assert not hasattr(cli, "_set_layer")


def test_component_unavailable_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sae.adapters", None)  # simulate the T2 package missing
    assert cli.main("daemon", ["--runs-dir", str(tmp_path)]) == 1
    assert "sae daemon: sae.adapters.make_adapter not available yet (owner T2)" in capsys.readouterr().err
    assert not (tmp_path / "CURRENT").exists()


def test_happy_path_fake_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiring, "live_adapter_factory", lambda cfg: (lambda _p, _s: FakeAdapter()))
    monkeypatch.setattr(wiring, "default_deps", lambda cfg, run_id, resume=False: make_deps(FakePlatform()))
    assert cli.main("daemon", ["--runs-dir", str(tmp_path), "--set", "engine.seed=3"]) == 0
    (run_dir,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert (run_dir / "config.resolved.json").exists()
    assert (run_dir / "state.json").exists()
    assert (run_dir / "control" / "inbox").is_dir()
    assert not (tmp_path / "CURRENT").exists()
    # resume the same run
    assert cli.main("daemon", ["--resume", run_dir.name, "--runs-dir", str(tmp_path)]) == 0


def test_lock_refused_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiring, "live_adapter_factory", lambda cfg: (lambda _p, _s: FakeAdapter()))
    (tmp_path / "CURRENT").write_text("other 999999")
    monkeypatch.setattr("sae.daemon.runs._is_sae_alive", lambda pid: True)
    assert cli.main("daemon", ["--runs-dir", str(tmp_path)]) == 1
    assert (tmp_path / "CURRENT").read_text() == "other 999999"


def test_any_exception_exits_1_and_passes_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(wiring, "live_adapter_factory", lambda cfg: (lambda _p, _s: FakeAdapter()))
    seen: list[bool] = []

    def boom(cfg: Any, run_id: str, resume: bool = False) -> Any:
        seen.append(resume)
        raise RuntimeError("kaboom")

    monkeypatch.setattr(wiring, "default_deps", boom)
    assert cli.main("daemon", ["--runs-dir", str(tmp_path)]) == 1
    assert seen == [False] and "kaboom" in capsys.readouterr().err
    assert not (tmp_path / "CURRENT").exists()


def test_resume_with_changed_policy_sha_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    """C8 v1.3: the resolved config holds null engine.* so the policy decides; a new sha refuses the resume."""
    from sae.contracts.c8_config import SaeConfig
    from sae.daemon import policy_resolve

    monkeypatch.setattr(wiring, "live_adapter_factory", lambda cfg: (lambda _p, _s: FakeAdapter()))
    monkeypatch.setattr(wiring, "default_deps", lambda cfg, run_id, resume=False: make_deps(FakePlatform()))
    assert cli.main("daemon", ["--runs-dir", str(tmp_path)]) == 0
    (run_dir,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    resolved = SaeConfig.model_validate_json((run_dir / "config.resolved.json").read_text(encoding="utf-8"))
    assert resolved.engine.rho is None and resolved.engine.belief_threshold is None
    (run_dir / "bus.jsonl").write_text(json.dumps(
        {"type": "run_started", "payload": {"policy": {"sha256": "0" * 64, "source": "registry"}}}) + "\n")
    monkeypatch.undo()  # real default_deps: resolve_policy + check_resume
    monkeypatch.setattr(wiring, "live_adapter_factory", lambda cfg: (lambda _p, _s: FakeAdapter()))
    monkeypatch.setattr(policy_resolve, "recorded_sha", lambda *_a: "0" * 64)
    assert cli.main("daemon", ["--resume", run_dir.name, "--runs-dir", str(tmp_path)]) == 1
    assert "start a new run" in capsys.readouterr().err
