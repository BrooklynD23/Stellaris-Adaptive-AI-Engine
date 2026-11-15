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
            assert kw == {"stdin": subprocess.DEVNULL, "timeout": 600, "check": False}
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


_PLAY_UNDER_PTY = """
import sys
import sae.daemon.cli
from sae.profile import launcher
launcher.UPDATE_TIMEOUT_S = int(sys.argv.pop(1))
sae.daemon.cli.main = lambda command, argv: print("DAEMON STARTED", flush=True) or 0
sys.exit(launcher.main("play", sys.argv[1:]))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="pty is POSIX only; Windows runs the NUL test below")
def test_play_on_a_tty_never_prompts_and_leaves_split_pending(tmp_path: Path) -> None:
    """B3: with a TTY on stdin, a split candidate must not block ``sae play``; it stays pending.

    The update timeout is cut from 600 s to ``stall_s``: a prompt shows up as a TimeoutExpired failure. It is
    not 5 s because two cold interpreter starts on a slow filesystem (WSL /mnt/c) already take ~7 s.
    """
    import os
    import pty
    import select
    import signal
    import time

    from sae.profile import store

    from ._fixtures import T, live_key, make_run, month, run_id

    runs = tmp_path / "runs"
    runs.mkdir()
    make_run(runs, run_id(1), "live", T(2026, 10, 1), live_key(1), [month("2200.01.01"), month("2260.01.01")])
    make_run(runs, run_id(2), "live", T(2026, 10, 2), live_key(1), [month("2201.01.01")])  # split candidate
    argv = [*base(tmp_path, "strict"), "--runs-dir", str(runs)]
    env = {k: v for k, v in os.environ.items() if not k.startswith("SAE_")}
    master, slave = pty.openpty()
    stall_s = 30
    proc = subprocess.Popen([sys.executable, "-c", _PLAY_UNDER_PTY, str(stall_s), *argv], stdin=slave, stdout=slave,
                            stderr=slave, env=env, start_new_session=True)
    os.close(slave)
    out = b""
    deadline = time.monotonic() + 2 * stall_s
    try:
        while time.monotonic() < deadline:  # read until EIO: every slave end (play and its update child) closed
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
        exited = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        exited = None
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)  # the blocked update grandchild shares the session
            proc.wait()
        os.close(master)
    text = out.decode(errors="replace")
    assert exited == 0 and "DAEMON STARTED" in text, text
    assert "profile update failed" not in text and "[n]ew game" not in text, text
    assert f"split candidate left pending: run {run_id(2)}" in text
    rows = store.read_index(tmp_path / "sae" / "campaigns.jsonl")[0]
    assert [r.event for r in rows] == ["seen"]  # run 2 was neither joined nor split


@pytest.mark.skipif(sys.platform != "win32", reason="Windows NUL stdin path; the pty test above covers POSIX")
def test_play_with_nul_stdin_on_windows_leaves_split_pending(tmp_path: Path) -> None:
    """B3 on Windows: ``sae play`` with ``stdin=NUL`` finishes and leaves the split candidate pending.

    The prompt text is not asserted absent: ``isatty(NUL)`` may be True on Windows, so the prompt can print and
    ``input()`` hits EOF.
    """
    import os

    from sae.profile import store

    from ._fixtures import T, live_key, make_run, month, run_id

    runs = tmp_path / "runs"
    runs.mkdir()
    make_run(runs, run_id(1), "live", T(2026, 10, 1), live_key(1), [month("2200.01.01"), month("2260.01.01")])
    make_run(runs, run_id(2), "live", T(2026, 10, 2), live_key(1), [month("2201.01.01")])  # split candidate
    argv = [*base(tmp_path, "strict"), "--runs-dir", str(runs)]
    env = {k: v for k, v in os.environ.items() if not k.startswith("SAE_")}
    stall_s = 30
    proc = subprocess.run([sys.executable, "-c", _PLAY_UNDER_PTY, str(stall_s), *argv], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=2 * stall_s, check=False)
    text = proc.stdout.decode(errors="replace")
    assert proc.returncode == 0 and "DAEMON STARTED" in text, text
    assert "profile update failed" not in text, text
    assert f"split candidate left pending: run {run_id(2)}" in text
    rows = store.read_index(tmp_path / "sae" / "campaigns.jsonl")[0]
    assert [r.event for r in rows] == ["seen"]  # run 2 was neither joined nor split
