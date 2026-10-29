"""Tests for the Claude Code hooks: ``hooks.handle``, ``dd.py hook`` and ``.claude/settings.json``."""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from devdash import REPO, cli, events, hooks

_DD = REPO / "tools" / "devdash" / "dd.py"
_SETTINGS = REPO / ".claude" / "settings.json"
_BASH = {"tool_name": "Bash", "tool_input": {"command": "export SECRET=abc && ls", "description": "list files"}}


def _read(dev: Path) -> list[dict[str, Any]]:
    got, invalid = events.read_events([events.events_path(dev)])
    assert invalid == 0
    return got


def _run_dd(event: str, payload: str, dev: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, SAE_DEVDASH_DIR=str(dev))
    return subprocess.run(
        [sys.executable, str(_DD), "hook", event], input=payload, capture_output=True, text=True, env=env, timeout=10
    )


def test_disabled_is_silent(dev: Path) -> None:
    proc = _run_dd("pre-tool", json.dumps(_BASH), dev)
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert not events.events_path(dev).exists()


def test_bash_logs_description_not_command(dev: Path) -> None:
    events.enable(dev)
    assert hooks.handle("pre-tool", _BASH, dev, REPO) == ""
    got = _read(dev)
    assert [e["msg"] for e in got] == ["list files"]
    assert "SECRET" not in events.events_path(dev).read_text(encoding="utf-8")


def test_bash_without_description_is_skipped(dev: Path) -> None:
    events.enable(dev)
    hooks.handle("pre-tool", {"tool_name": "Bash", "tool_input": {"command": "ls"}}, dev, REPO)
    assert not events.events_path(dev).exists()


def test_agent_start_and_stop(dev: Path) -> None:
    events.enable(dev)
    start = {"tool_name": "Agent", "tool_input": {"subagent_type": "sae-scout", "description": "scan docs"}}
    hooks.handle("pre-tool", start, dev, REPO)
    hooks.handle("pre-tool", {"tool_name": "Task", "tool_input": {"command": "x"}}, dev, REPO)
    hooks.handle("subagent-stop", {"agent_type": "sae-scout", "session_id": "s1"}, dev, REPO)
    hooks.handle("subagent-stop", {"subagent_type": "sae-reviewer"}, dev, REPO)
    hooks.handle("subagent-stop", {}, dev, REPO)
    got = _read(dev)
    assert (got[0]["source"], got[0]["kind"], got[0]["ref"], got[0]["msg"]) == (
        "sae-scout",
        "start",
        "agent:sae-scout",
        "scan docs",
    )
    assert (got[1]["source"], got[1]["ref"]) == ("agent", "agent:agent")
    assert got[1]["msg"] == "agent started"
    assert [(e["kind"], e["ref"]) for e in got[2:]] == [
        ("done", "agent:sae-scout"),
        ("done", "agent:sae-reviewer"),
        ("done", "agent:agent"),
    ]
    assert got[2]["msg"] == "agent finished"
    assert got[2]["session"] == "s1"


def test_session_start_prints_url(dev: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events.enable(dev)
    monkeypatch.setattr(hooks.ensure, "ensure_running", lambda repo, dev, port: "http://127.0.0.1:9999/")
    out = hooks.handle("session-start", {"session_id": "s"}, dev, REPO)
    assert out == "SAE dashboard: http://127.0.0.1:9999/"
    assert _read(dev)[0]["msg"] == "session started"


def test_session_start_failure_is_reported(dev: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events.enable(dev)

    def boom(repo: Path, dev: Path, port: int) -> str:
        raise RuntimeError("port 1 in use")

    monkeypatch.setattr(hooks.ensure, "ensure_running", boom)
    assert hooks.handle("session-start", {}, dev, REPO) == "SAE dashboard: not started (port 1 in use)"


def test_bad_stdin_exit_0(dev: Path) -> None:
    events.enable(dev)
    for payload in ("", "not json", "[1,2]", "null"):
        proc = _run_dd("pre-tool", payload, dev)
        assert proc.returncode == 0
        assert proc.stdout == ""
    assert not events.events_path(dev).exists()


def test_cli_hook_main_returns_0(dev: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    events.enable(dev)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_BASH)))
    assert cli.main(["hook", "pre-tool"]) == 0
    assert len(_read(dev)) == 1


def test_hook_imports_stdlib_only(dev: Path) -> None:
    events.enable(dev)
    for event in ("pre-tool", "subagent-stop"):
        env = dict(os.environ, SAE_DEVDASH_DIR=str(dev))
        proc = subprocess.run(
            [sys.executable, "-I", "-X", "importtime", str(_DD), "hook", event],
            input=json.dumps(_BASH),
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        assert proc.returncode == 0
        mods = [line.rsplit("|", 1)[-1].strip() for line in proc.stderr.splitlines() if "|" in line]
        assert "devdash.hooks" in mods
        banned = [m for m in mods if m.split(".")[0] in {"pydantic", "sae", "server", "state", "gateindex", "gates"}]
        banned += [m for m in mods if m in {"devdash.server", "devdash.state", "devdash.gateindex"}]
        assert banned == []


def _settings() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    return data


def _command(event: str) -> str:
    cmd: str = _settings()["hooks"][event][0]["hooks"][0]["command"]
    return cmd


def test_settings_json_valid() -> None:
    data = _settings()
    assert list(data) == ["hooks"]
    assert set(data["hooks"]) == {"SessionStart", "PreToolUse", "SubagentStop"}
    assert data["hooks"]["PreToolUse"][0]["matcher"] == "Agent|Task|Bash"
    for event in data["hooks"]:
        assert "dd.py\" hook " in _command(event)
        assert "exit 0" in _command(event)


def _project(tmp_path: Path, *, enabled: bool) -> tuple[Path, Path]:
    proj = tmp_path / "proj"
    ignore = shutil.ignore_patterns("__pycache__")
    shutil.copytree(REPO / "tools" / "devdash", proj / "tools" / "devdash", ignore=ignore)
    dev = proj / ".sae-dev"
    dev.mkdir()
    if enabled:
        (dev / "enabled").write_text("", encoding="utf-8")
    return proj, dev


_SHELLS = [
    pytest.param("sh", id="sh"),
    pytest.param("bash", id="bash"),
    pytest.param(
        "dash", id="dash", marks=pytest.mark.skipif(shutil.which("dash") is None, reason="dash is not installed")
    ),
]


def _run_settings_cmd(proj: Path, dev: Path, shell: str) -> float:
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(proj), SAE_DEVDASH_DIR=str(dev))
    start = time.perf_counter()
    proc = subprocess.run(
        [shell, "-c", _command("PreToolUse")],
        input=json.dumps(_BASH),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    elapsed = time.perf_counter() - start
    assert proc.returncode == 0
    assert proc.stdout == ""
    return elapsed


@pytest.mark.parametrize("shell", _SHELLS)
def test_settings_commands_detach_and_fast(tmp_path: Path, shell: str) -> None:
    proj, dev = _project(tmp_path, enabled=True)
    times = [_run_settings_cmd(proj, dev, shell) for _ in range(3)]
    assert statistics.median(times) < 0.1
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and len(events.read_events([events.events_path(dev)])[0]) < 3:
        time.sleep(0.05)
    got, _ = events.read_events([events.events_path(dev)])
    assert len(got) == 3
    assert {e["msg"] for e in got} == {"list files"}


@pytest.mark.parametrize("shell", _SHELLS)
def test_settings_disabled_is_noop(tmp_path: Path, shell: str) -> None:
    proj, dev = _project(tmp_path, enabled=False)
    times = [_run_settings_cmd(proj, dev, shell) for _ in range(3)]
    assert statistics.median(times) < 0.5
    time.sleep(0.5)
    assert not events.events_path(dev).exists()


def test_session_start_error_is_one_redacted_line(dev: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events.enable(dev)
    home = str(Path.home())

    def boom(repo: Path, dev: Path, port: int) -> str:
        raise RuntimeError(f"server exited\nsee {home}/x/server.log:\n  Traceback\r\nboom")

    monkeypatch.setattr(hooks.ensure, "ensure_running", boom)
    out = hooks.handle("session-start", {}, dev, REPO)
    assert "\n" not in out
    assert home not in out
    assert out.startswith("SAE dashboard: not started (")
