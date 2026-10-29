"""Single-instance startup and shutdown of the dashboard server. STDLIB-ONLY (ruling R3).

Hooks import this module under a bare ``python3``, so it must not import ``devdash.server``, ``devdash.state``,
``devdash.gateindex``, ``gates`` or ``sae``. It talks to the server only over HTTP (``/api/health``) and through
``<dev>/server.json``, which the server writes itself after it has bound its port.

The server needs the repo venv (it imports ``sae.m0.results``), so :func:`ensure_running` spawns
``<repo>/.venv/bin/python`` and falls back to ``uv run python3``; never the caller's interpreter.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

__all__ = ["DEFAULT_PORT", "default_port", "ensure_running", "stop"]

DEFAULT_PORT = 8765
PORT_ENV = "SAE_DEVDASH_PORT"
_HEALTH_TIMEOUT = 0.5
_START_WAIT = 3.0
_SPAWN_WAIT = 2.5
_TERM_WAIT = 1.0
_STOP_WAIT = 3.0


def default_port() -> int:
    """Return ``$SAE_DEVDASH_PORT`` when it is an integer in 1..65535, else :data:`DEFAULT_PORT`."""
    raw = os.environ.get(PORT_ENV, "")
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        print(f"devdash: ignoring invalid {PORT_ENV}={raw!r}", file=sys.stderr)
        return DEFAULT_PORT
    return port


def _health(port: int) -> dict[str, Any] | None:
    """Return the ``/api/health`` JSON of a healthy devdash on ``port`` (``ok`` true and an int ``pid``), else None."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=_HEALTH_TIMEOUT)
    try:
        conn.request("GET", "/api/health")
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        data = json.loads(resp.read(4096))
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        conn.close()
    if isinstance(data, dict) and data.get("ok") is True and type(data.get("pid")) is int:
        return data
    return None


def _read_info(dev: Path) -> dict[str, Any] | None:
    """Read ``<dev>/server.json``; None when missing or malformed (``pid`` and ``port`` must be ints)."""
    try:
        data = json.loads((dev / "server.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and type(data.get("pid")) is int and type(data.get("port")) is int:
        return data
    return None


def _alive(pid: int) -> bool:
    """Return True while ``pid`` runs. A finished child of this process is reaped first so it does not look alive."""
    if pid <= 0:
        return False
    with contextlib.suppress(ChildProcessError, OSError, AttributeError):
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _port_free(port: int) -> bool:
    """Return True when 127.0.0.1:``port`` can be bound (a bound or listening socket makes it False)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _command(repo: Path, port: int) -> list[str]:
    """Return the argv that starts the server: the repo venv python, or ``uv run python3`` when it is missing."""
    script = str(repo / "tools" / "devdash" / "dd.py")
    venv = repo / ".venv" / "bin" / "python"
    if not venv.exists():
        venv = repo / ".venv" / "Scripts" / "python.exe"
    prefix = [str(venv)] if venv.exists() else ["uv", "run", "python3"]
    return [*prefix, script, "serve", "--port", str(port)]


def _log_tail(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-400:].strip()
    except OSError:
        return ""


def _await_devdash(dev: Path, port: int) -> None:
    """Poll until a healthy devdash whose pid matches ``server.json`` answers on ``port``, else raise.

    Raises ``RuntimeError("port <n> in use")`` when none appears within the start wait.
    """
    deadline = time.monotonic() + _START_WAIT
    while True:
        health = _health(port)
        info = _read_info(dev)
        if health is not None and info is not None and info["pid"] == health["pid"]:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"port {port} in use")
        time.sleep(0.05)


def ensure_running(repo: Path, dev: Path, port: int) -> str:
    """Make sure one dashboard server answers on ``port`` and return its URL.

    A running server (``server.json`` pid alive and ``/api/health`` answering with that pid within 0.5 s) is reused.
    Anything else on the port raises ``RuntimeError("port <n> in use")``. Otherwise the server is spawned detached
    (``start_new_session``), its output goes to ``<dev>/server.log``, and the call waits up to 3 s for health.
    Raises ``RuntimeError`` when the server exits early or never becomes healthy (the spawned process is then
    terminated so nothing is left behind).
    """
    url = f"http://127.0.0.1:{port}/"
    health = _health(port)
    if health is not None:
        info = _read_info(dev)
        if info is not None and info["pid"] == health["pid"] and _alive(info["pid"]):
            return url
        _await_devdash(dev, port)
        return url
    if not _port_free(port):
        _await_devdash(dev, port)  # a concurrent starter may be about to answer
        return url
    dev.mkdir(parents=True, exist_ok=True)
    log_path = dev / "server.log"
    env = dict(os.environ, SAE_DEVDASH_DIR=str(dev))
    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            _command(repo, port),
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    deadline = time.monotonic() + _SPAWN_WAIT
    while True:
        health = _health(port)
        if health is not None:
            info = _read_info(dev)
            if info is not None and info["pid"] == health["pid"]:
                return url
            if proc.poll() is None and health["pid"] == proc.pid:
                return url
        if proc.poll() is not None or time.monotonic() >= deadline:
            break  # exited early (after the final health check above), or out of time
        time.sleep(0.05)
    if proc.poll() is None:
        _terminate(proc.pid)
        try:
            proc.wait(timeout=_TERM_WAIT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=_TERM_WAIT)
        raise RuntimeError(f"server did not answer on port {port} within {_SPAWN_WAIT:.1f} s; see {log_path}")
    tail = _log_tail(log_path)
    if "in use" in tail.lower() or not _port_free(port):
        raise RuntimeError(f"port {port} in use")
    raise RuntimeError(f"server exited with code {proc.returncode}; {log_path}: {tail}")


def _terminate(pid: int) -> None:
    """Send SIGTERM to ``pid``'s process group when it leads one, else to the process."""
    try:
        if os.name != "nt" and os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def stop(dev: Path) -> bool:
    """Stop the server recorded in ``<dev>/server.json``. Returns True when a running server was stopped.

    The pid is signalled only when ``/api/health`` on the recorded port reports that same pid, so a stale file
    whose pid was recycled by another process is never killed. A stale or unconfirmed file is removed and False is
    returned.
    """
    info = _read_info(dev)
    if info is None:
        return False
    pid, port = int(info["pid"]), int(info["port"])
    health = _health(port)
    if health is None or health["pid"] != pid or not _alive(pid):
        with contextlib.suppress(OSError):
            (dev / "server.json").unlink()
        return False
    _terminate(pid)
    deadline = time.monotonic() + _STOP_WAIT
    while time.monotonic() < deadline:
        if not _alive(pid):
            break
        time.sleep(0.05)
    else:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
        time.sleep(0.1)
        _alive(pid)
    with contextlib.suppress(OSError):
        (dev / "server.json").unlink()
    return True
