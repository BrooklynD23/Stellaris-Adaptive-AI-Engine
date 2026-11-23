"""Tests for devdash.ensure: single instance, foreign port, stdlib-only import, stop.

These spawn real ``dd.py serve`` processes on free ports. The fixture stops them in teardown, also on failure.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from devdash import REPO, ensure


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def cleanup(dev: Path) -> Iterator[None]:
    try:
        yield
    finally:
        ensure.stop(dev)
        info = dev / "server.json"
        if info.exists():
            try:
                os.kill(int(json.loads(info.read_text(encoding="utf-8"))["pid"]), signal.SIGKILL)
            except (OSError, ValueError, KeyError):
                pass


def test_ensure_single_instance(dev: Path, cleanup: None) -> None:
    port = _free_port()
    url = ensure.ensure_running(REPO, dev, port)
    assert url == f"http://127.0.0.1:{port}/"
    first = json.loads((dev / "server.json").read_text(encoding="utf-8"))
    again = ensure.ensure_running(REPO, dev, port)
    second = json.loads((dev / "server.json").read_text(encoding="utf-8"))
    assert again == url and first["pid"] == second["pid"] and first["port"] == port
    assert ensure.stop(dev) is True
    assert not (dev / "server.json").exists()
    assert ensure.stop(dev) is False


def test_ensure_foreign_port_raises(dev: Path, cleanup: None) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        with pytest.raises(RuntimeError, match=f"port {port} in use"):
            ensure.ensure_running(REPO, dev, port)
    assert not (dev / "server.json").exists()


def test_ensure_bound_not_listening_raises(dev: Path, cleanup: None) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        with pytest.raises(RuntimeError, match=f"port {port} in use"):
            ensure.ensure_running(REPO, dev, port)


def test_stop_refuses_stale_pid(dev: Path) -> None:
    dev.mkdir(parents=True)
    (dev / "server.json").write_text(json.dumps({"pid": os.getpid(), "port": _free_port(), "started": "x"}))
    assert ensure.stop(dev) is False  # health does not confirm this pid, so it must not be signalled
    assert not (dev / "server.json").exists()


def test_stop_without_info(dev: Path) -> None:
    assert ensure.stop(dev) is False


def test_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SAE_DEVDASH_PORT", raising=False)
    assert ensure.default_port() == 8765
    monkeypatch.setenv("SAE_DEVDASH_PORT", "9123")
    assert ensure.default_port() == 9123
    monkeypatch.setenv("SAE_DEVDASH_PORT", "junk")
    assert ensure.default_port() == 8765


def test_ensure_is_stdlib_only() -> None:
    """R3: hooks import ensure under bare python3, so it must not pull in server/state/gateindex/gates/sae."""
    code = f"import sys; sys.path.insert(0, {str(REPO / 'tools' / 'devdash')!r}); import devdash.ensure"
    proc = subprocess.run(
        [sys.executable, "-I", "-X", "importtime", "-c", code], capture_output=True, text=True, check=True, timeout=30
    )
    loaded = {line.split("|")[-1].strip() for line in proc.stderr.splitlines() if line.startswith("import time:")}
    assert "devdash.ensure" in loaded
    banned = {"devdash.server", "devdash.state", "devdash.gateindex", "gates", "sae"}
    assert not banned & loaded
    assert not {m for m in loaded if m.startswith("sae.")}


def test_ensure_concurrent_single_instance(dev: Path, cleanup: None) -> None:
    import threading

    for _ in range(5):
        port = _free_port()
        results: list[str | BaseException] = []

        def call(port: int = port, results: list[str | BaseException] = results) -> None:
            try:
                results.append(ensure.ensure_running(REPO, dev, port))
            except BaseException as exc:
                results.append(exc)

        threads = [threading.Thread(target=call) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert results == [f"http://127.0.0.1:{port}/"] * 2
        info = json.loads((dev / "server.json").read_text(encoding="utf-8"))
        # Both callers may spawn; the loser fails to bind and exits shortly after (slower under disk stalls), so wait
        # for it to go: exactly the recorded server must remain.
        pids: list[str] = []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            out = subprocess.run(["pgrep", "-f", f"[d]d.py serve --port {port}"], capture_output=True, text=True)
            pids = out.stdout.split()
            if len(pids) <= 1:
                break
            time.sleep(0.05)
        assert pids == [str(info["pid"])]
        assert ensure.stop(dev) is True


def test_ensure_cli_rejects_port_zero() -> None:
    from devdash import cli

    assert cli.main(["ensure", "--port", "0"]) == 2


def test_ensure_early_exit_raises_fast(dev: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    monkeypatch.setattr(ensure, "_command", lambda repo, port: [sys.executable, "-c", "raise SystemExit(3)"])
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="exited with code 3"):
        ensure.ensure_running(REPO, dev, _free_port())
    assert time.monotonic() - start < 1.5


def test_ensure_lost_bind_waits_bounded(dev: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A spawn that exits with "port in use?" on a free port waits for a concurrent winner, then raises within ~4 s."""
    dev.mkdir(parents=True, exist_ok=True)
    (dev / "server.log").write_text("old line: port in use?\n", encoding="utf-8")
    code = "import sys; print('cannot start (port in use?)'); sys.exit(1)"
    monkeypatch.setattr(ensure, "_command", lambda repo, port: [sys.executable, "-c", code])
    port = _free_port()
    start = time.monotonic()
    with pytest.raises(RuntimeError, match=f"port {port} in use"):
        ensure.ensure_running(REPO, dev, port)
    assert time.monotonic() - start < 4.5


def test_ensure_stale_log_in_use_ignored(dev: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An old "in use" line in the append-only log must not be mistaken for this spawn's failure."""
    dev.mkdir(parents=True, exist_ok=True)
    (dev / "server.log").write_text("old line: port in use?\n", encoding="utf-8")
    monkeypatch.setattr(ensure, "_command", lambda repo, port: [sys.executable, "-c", "raise SystemExit(3)"])
    with pytest.raises(RuntimeError, match="exited with code 3"):
        ensure.ensure_running(REPO, dev, _free_port())
