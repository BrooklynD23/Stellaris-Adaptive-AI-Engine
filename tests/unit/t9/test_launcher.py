"""``sae play``: update first (never fatal), then the daemon with the original argv."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sae.profile import launcher


class Spy:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, result: Any = 0) -> None:
        self.calls: list[list[str]] = []
        self.daemon: list[tuple[str, list[str]]] = []
        self.result = result

        def run(argv: list[str], **kw: Any) -> Any:
            self.calls.append(argv)
            assert kw == {"timeout": 600, "check": False}
            if isinstance(self.result, BaseException):
                raise self.result
            return subprocess.CompletedProcess(argv, self.result)

        def daemon_main(command: str, argv: list[str]) -> int:
            self.daemon.append((command, argv))
            return 7

        monkeypatch.setattr(subprocess, "run", run)
        monkeypatch.setattr("sae.daemon.cli.main", daemon_main)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in list(os.environ):
        if name.startswith("SAE_"):
            monkeypatch.delenv(name)


def base(tmp_path: Path, mode: str) -> list[str]:
    return ["--set", f"paths.sae_dir={tmp_path / 'sae'}", "--set", f"profile.mode={mode}"]


def test_mode_off_spawns_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch)
    argv = base(tmp_path, "off")
    assert launcher.main("play", argv) == 7
    assert spy.calls == [] and spy.daemon == [("daemon", argv)]


def test_success_runs_update_then_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    spy = Spy(monkeypatch)
    argv = [*base(tmp_path, "strict"), "--preset", "dev", "--runs-dir", str(tmp_path / "r"), "--adapter", "live",
            "--config", str(tmp_path / "c.toml")]
    (tmp_path / "c.toml").write_text("")
    assert launcher.main("play", argv) == 7
    assert spy.calls == [[
        sys.executable, "-m", "sae", "profile", "update", "--config", str(tmp_path / "c.toml"), "--preset", "dev",
        "--set", f"paths.sae_dir={tmp_path / 'sae'}", "--set", "profile.mode=strict", "--runs-dir", str(tmp_path / "r"),
    ]]
    assert spy.daemon == [("daemon", argv)]  # original argv, --adapter included
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("result", "reason"),
    [(3, "exit code 3"), (OSError("no python"), "no python"),
     (subprocess.TimeoutExpired(["x"], 600), "TimeoutExpired")],
)
def test_update_failure_is_not_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str], result: Any, reason: str) -> None:
    spy = Spy(monkeypatch, result=result)
    argv = [*base(tmp_path, "strict"), "--resume", "20261001-120001-live-0001"]
    assert launcher.main("play", argv) == 7
    assert f"sae play: profile update failed ({reason}); starting the daemon anyway" in capsys.readouterr().err
    assert spy.daemon == [("daemon", argv)] and len(spy.calls) == 1


def test_config_error_skips_update_but_starts_daemon(monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    spy = Spy(monkeypatch)
    argv = ["--set", "profile.mode=bogus"]
    assert launcher.main("play", argv) == 7
    assert "invalid config" in capsys.readouterr().err and spy.calls == [] and spy.daemon == [("daemon", argv)]


def test_unknown_flag_is_2(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch)
    assert launcher.main("play", ["--bogus"]) == 2
    assert launcher.main("play", ["--help"]) == 0
    assert spy.calls == [] and spy.daemon == []
