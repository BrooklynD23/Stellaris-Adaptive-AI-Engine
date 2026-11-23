"""Dashboard HTTP server: state, events, SSE and the owner verdict endpoint. Binds 127.0.0.1 only.

Security model (the verdict endpoint starts a process when a browser asks it to):

* every request needs ``Host`` equal to ``127.0.0.1:<port>`` or ``localhost:<port>`` (DNS-rebinding guard);
* every POST needs header ``X-SAE-Token`` equal to the per-start random token (``hmac.compare_digest``); when an
  ``Origin`` header is present it must be one of the allowed origins; the body is at most 16 KB of JSON object;
* a verdict is validated completely before any process starts; the argv is a list (no shell) and each value and
  the note are single ``--opt=...`` items, so user text can never be read as a flag;
* static files are served from one directory; traversal, dotfiles and symlinks leaving it are refused.

Runs under the repo venv interpreter (the snapshot imports ``sae.m0.results``). Never imported by ``src/sae``.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import os
import secrets
import select
import signal
import socket
import socketserver
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final, cast
from urllib.parse import unquote, urlsplit

from gates import Gate, load_gates

from devdash import events as ev
from devdash.gateindex import default_scrub, from_m0, update_index
from devdash.state import snapshot
from sae.m0.checks import BY_ID

__all__ = ["make_gh", "make_server", "remove_server_info", "run", "serve_forever_with_idle", "write_server_info"]

_LOG = logging.getLogger(__name__)

Runner = Callable[..., "subprocess.CompletedProcess[str]"]
Clock = Callable[[], float]

BODY_MAX: Final = 16 * 1024
VALUE_MAX: Final = 200
NOTE_MAX: Final = 500
STDERR_MAX: Final = 2000
EVENTS_MAX: Final = 2000
GH_TIMEOUT_S: Final = 10
GH_CACHE_S: Final = 60.0
RUNNER_TIMEOUT_S: Final = 60
SSE_MAX_CLIENTS: Final = 8
STATE_INTERVAL_S: Final = 2.0
PING_INTERVAL_S: Final = 5.0
TOKEN_PLACEHOLDER: Final = "__SAE_TOKEN__"
_STATIC_DIR: Final = Path(__file__).resolve().parent.parent / "static"
_TYPES: Final[dict[str, str]] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}
_CSP: Final = "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"


def make_gh(
    runner: Runner = subprocess.run, clock: Clock = time.monotonic, ttl: float = GH_CACHE_S
) -> Callable[[list[str]], str | None]:
    """Return a ``gh`` caller for ``state.snapshot``: runs ``gh <argv>`` (10 s timeout), caches by argv for ``ttl`` s.

    Any failure (``gh`` missing, non-zero exit, timeout) gives None, and that None is cached too, so a missing ``gh``
    is not retried on every poll.
    """
    cache: dict[tuple[str, ...], tuple[float, str | None]] = {}
    lock = threading.Lock()

    def call(argv: list[str]) -> str | None:
        key = tuple(argv)
        with lock:
            hit = cache.get(key)
            if hit is not None and clock() - hit[0] < ttl:
                return hit[1]
        out: str | None = None
        try:
            proc = runner(["gh", *argv], capture_output=True, text=True, timeout=GH_TIMEOUT_S)
            if proc.returncode == 0:
                out = proc.stdout
        except (OSError, subprocess.SubprocessError) as exc:
            _LOG.info("gh unavailable: %s", exc)
        with lock:
            cache[key] = (clock(), out)
        return out

    return call


def _bound_port(srv: socketserver.BaseServer) -> int:
    """Return the TCP port the server is bound to."""
    return int(cast(tuple[str, int], srv.server_address)[1])


def _sig(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


class _Hub:
    """Shared server state: the two event tails, a recent-event log for SSE, the token and activity tracking."""

    def __init__(
        self, repo: Path, dev: Path, *, runner: Runner, now: Callable[[], datetime], clock: Clock, static_dir: Path
    ) -> None:
        self.repo, self.dev, self.runner = repo, dev, runner
        self.now, self.clock, self.static_dir = now, clock, static_dir
        self.token = secrets.token_hex(16)
        self.tails = {w: ev.Tail(ev.events_path(dev, w), maxlen=EVENTS_MAX) for w in ("wsl", "win")}
        self.cond = threading.Condition()
        self.log: deque[tuple[int, dict[str, Any]]] = deque(maxlen=EVENTS_MAX)
        self.seq = 0
        self.invalid = 0
        self.clients = 0
        self.last_activity = clock()
        self.stopping = threading.Event()
        self.verdict_lock = threading.Lock()
        self.gh = make_gh(clock=clock)

    def touch(self) -> None:
        """Record activity now (an event arrived or an SSE client is connected)."""
        self.last_activity = self.clock()

    def idle_seconds(self) -> float:
        """Seconds since the last activity; zero while an SSE client is connected."""
        if self.clients > 0:
            return 0.0
        return max(0.0, self.clock() - self.last_activity)

    def poll_once(self) -> int:
        """Poll both tails, then rotate (the server is the only rotator). Returns the number of new events."""
        fresh: list[dict[str, Any]] = []
        with self.cond:
            for writer, tail in self.tails.items():
                new, bad = tail.poll()  # immediately before rotating, as events.rotate_if_needed requires
                fresh.extend(new)
                self.invalid += bad
                try:
                    ev.rotate_if_needed(tail.path)
                except OSError as exc:
                    _LOG.warning("cannot rotate %s log: %s", writer, exc)
            fresh.sort(key=lambda e: str(e["ts"]))
            for event in fresh:
                self.seq += 1
                self.log.append((self.seq, event))
            if fresh:
                self.cond.notify_all()
        if fresh:
            self.touch()
        return len(fresh)

    def merged(self) -> list[dict[str, Any]]:
        """Recent events of both writers, sorted by ``ts``."""
        with self.cond:
            items = [e for tail in self.tails.values() for e in tail.events]
        items.sort(key=lambda e: str(e["ts"]))
        return items

    def state(self) -> dict[str, Any]:
        """Build the dashboard snapshot from the merged tails."""
        return snapshot(self.repo, self.dev, now=self.now(), gh=self.gh, events=self.merged())

    def input_sig(self) -> tuple[Any, ...]:
        """Cheap signature of the snapshot's file inputs; SSE sends a fresh ``state`` when it changes."""
        return (
            self.seq,
            _sig(self.dev / "env.json"),
            _sig(self.dev / "gates.json"),
            _sig(self.dev / "enabled"),
            _sig(self.repo / "docs" / "dev" / "tasks.json"),
            _sig(self.repo / "m0_results"),
            int(self.now().timestamp() // 60),  # minute bucket: time-derived flags (stale) refresh once a minute
        )

    def poll_loop(self, interval: float) -> None:
        """Background loop; survives any error in one poll."""
        while not self.stopping.wait(interval):
            try:
                self.poll_once()
            except Exception:
                _LOG.exception("event poll failed")

    def append_event(self, kind: str, msg: str, ref: str) -> None:
        """Write an owner event to the WSL log. Failure is logged, not raised (the action itself succeeded)."""
        try:
            ev.append(ev.make_event("owner", kind, msg, ref=ref), ev.events_path(self.dev, "wsl"))
        except (OSError, ValueError) as exc:
            _LOG.warning("owner event not written: %s", exc)


class _Server(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying the :class:`_Hub`; stops the poller on ``server_close``."""

    daemon_threads = True
    allow_reuse_address = os.name != "nt"
    hub: _Hub
    poller: threading.Thread

    def server_bind(self) -> None:
        """Bind; on Windows take the port exclusively so no other process can share it."""
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def server_close(self) -> None:
        """Stop the poller and close the socket. Safe before ``hub`` exists (``TCPServer`` calls it if bind fails)."""
        hub = getattr(self, "hub", None)
        if hub is not None:
            hub.stopping.set()
            with hub.cond:
                hub.cond.notify_all()
        super().server_close()
        poller = getattr(self, "poller", None)
        if poller is not None:
            poller.join(2)


class _Bad(Exception):
    """A request-validation failure carrying its HTTP status."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _gate(ref: str) -> Gate | None:
    try:
        return next((g for g in load_gates() if g.id == ref), None)
    except (ValueError, OSError) as exc:
        _LOG.error("gate registry unreadable: %s", exc)
        return None


def _text(value: object, name: str, limit: int, *, required: bool) -> str:
    if not isinstance(value, str):
        raise _Bad(400, f"{name} must be a string")
    if required and not value:
        raise _Bad(400, f"{name} must not be empty")
    if len(value) > limit:
        raise _Bad(400, f"{name} is longer than {limit} characters")
    if not value.isprintable():
        raise _Bad(400, f"{name} contains non-printable characters")
    return value


def _validate_verdict(gate: Gate | None, body: Mapping[str, Any]) -> tuple[str, dict[str, str], str]:
    """Validate a verdict body completely; return ``(verdict, values, note)`` or raise :class:`_Bad` (400)."""
    if gate is None or not gate.judged or gate.id not in BY_ID:
        raise _Bad(400, "ref is not a judged gate")
    verdict = body.get("verdict")
    if verdict not in ("pass", "fail"):
        raise _Bad(400, "verdict must be 'pass' or 'fail'")
    raw_values = body.get("values", {})
    if not isinstance(raw_values, dict):
        raise _Bad(400, "values must be an object")
    allowed = {e["key"] for e in gate.evidence}
    values: dict[str, str] = {}
    for key, value in raw_values.items():
        if not isinstance(key, str) or key not in allowed:
            raise _Bad(400, f"unknown evidence key {str(key)[:40]!r}")
        values[key] = _text(value, f"value for {key}", VALUE_MAX, required=True)
    note = _text(body.get("note", ""), "note", NOTE_MAX, required=False)
    return str(verdict), values, note


def _user_dir(dev: Path) -> str | None:
    """Return ``user_dir`` from ``<dev>/env.json`` when present and usable, else None. Raises _Bad(500) if unsafe."""
    try:
        raw = json.loads((dev / "env.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = raw.get("user_dir") if isinstance(raw, dict) else None
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("-") or "\x00" in value:
        raise _Bad(500, "env.json user_dir is not a usable path")
    return value


class _Handler(BaseHTTPRequestHandler):
    """Request handler. All routing and validation lives here; state lives on ``self.hub``."""

    server_version = "SAEDevDash"
    sys_version = ""
    timeout = 15

    @property
    def hub(self) -> _Hub:
        return cast(_Server, self.server).hub

    def log_message(self, format: str, *args: Any) -> None:
        _LOG.debug("%s - %s", self.address_string(), format % args)

    # -- plumbing -----------------------------------------------------------------------------------------------

    def _port(self) -> int:
        return _bound_port(self.server)

    def _origins(self) -> set[str]:
        port = self._port()
        return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def _headers_common(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", _CSP)

    def _send(self, status: int, body: bytes, ctype: str, *, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self._headers_common()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj: object) -> None:
        self._send(status, json.dumps(obj, separators=(",", ":")).encode("utf-8"), "application/json")

    def _fail(self, status: int, reason: str) -> None:
        self._json(status, {"ok": False, "error": reason})

    def _host_ok(self) -> bool:
        hosts = self.headers.get_all("Host") or []
        port = self._port()
        return len(hosts) == 1 and hosts[0] in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _same_site(self) -> bool:
        """Refuse browser requests that did not come from this page (``Sec-Fetch-Site`` / ``Origin`` checks)."""
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site not in ("same-origin", "none"):
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in self._origins()

    def _token_ok(self) -> bool:
        supplied = self.headers.get("X-SAE-Token", "")
        return hmac.compare_digest(supplied.encode("utf-8", "replace"), self.hub.token.encode("ascii"))

    # -- GET ----------------------------------------------------------------------------------------------------

    def do_GET(self) -> None:
        """Serve the page, static files and the read-only API."""
        try:
            if not self._host_ok():
                self._fail(403, "bad Host header")
                return
            path = urlsplit(self.path)
            route = path.path
            if route.startswith("/api/") and not self._same_site():
                self._fail(403, "cross-site request refused")
                return
            if route in ("/", "/index.html"):
                self._index()
            elif route.startswith("/static/"):
                self._static(route[len("/static/") :])
            elif route == "/api/health":
                self._json(200, {"ok": True, "pid": os.getpid()})
            elif route == "/api/state":
                self._json(200, self.hub.state())
            elif route == "/api/events":
                self._events(path.query)
            elif route == "/api/stream":
                self._stream()
            else:
                self._fail(404, "not found")
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception:
            _LOG.exception("GET %s failed", self.path)
            with contextlib.suppress(OSError):
                self._fail(500, "internal error")

    def _index(self) -> None:
        page = self._read_static("index.html")
        if page is None:
            self._fail(404, "index.html not found")
            return
        body = page.decode("utf-8", "replace").replace(TOKEN_PLACEHOLDER, self.hub.token).encode("utf-8")
        self._send(200, body, _TYPES[".html"])

    def _read_static(self, rel: str) -> bytes | None:
        """Return the bytes of ``rel`` under the static dir, or None when missing or not allowed."""
        root = self.hub.static_dir
        text = unquote(rel)
        parts = text.split("/")
        if not text or "\x00" in text or "\\" in text or any(p in ("", ".", "..") or p.startswith(".") for p in parts):
            return None
        try:
            base = root.resolve(strict=True)
            target = (base / text).resolve(strict=True)
            if not target.is_relative_to(base) or not target.is_file():
                return None
            return target.read_bytes()
        except OSError:
            return None

    def _static(self, rel: str) -> None:
        data = self._read_static(rel)
        if data is None:
            self._fail(404, "not found")
            return
        suffix = Path(unquote(rel)).suffix.lower()
        self._send(200, data, _TYPES.get(suffix, "application/octet-stream"))

    def _events(self, query: str) -> None:
        since: str | None = None
        for pair in query.split("&"):
            name, _, value = pair.partition("=")
            if name == "since":
                since = unquote(value)
        if since is not None and (len(since) > 40 or not since.isprintable()):
            self._fail(400, "bad since")
            return
        items = self.hub.merged()
        if since:
            items = [e for e in items if str(e["ts"]) > since]
        self._json(200, {"events": items[-EVENTS_MAX:]})

    def _stream(self) -> None:
        hub = self.hub
        with hub.cond:
            if hub.clients >= SSE_MAX_CLIENTS:
                full = True
            else:
                full = False
                hub.clients += 1
                cursor = hub.seq
        if full:
            self._fail(503, f"more than {SSE_MAX_CLIENTS} stream clients")
            return
        try:
            self._stream_open(hub, cursor)
        finally:
            with hub.cond:
                hub.clients -= 1
            hub.touch()

    def _stream_open(self, hub: _Hub, cursor: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._headers_common()
        self.end_headers()
        self.close_connection = True
        try:
            last_sig = hub.input_sig()
            self._sse("state", hub.state())
            last_state = last_ping = time.monotonic()
            while not hub.stopping.is_set():
                if self._client_closed():
                    return
                with hub.cond:
                    if hub.seq == cursor:
                        hub.cond.wait(0.1)
                    fresh = [(n, e) for n, e in hub.log if n > cursor]
                    if fresh and fresh[0][0] > cursor + 1:
                        _LOG.debug("sse client fell behind by %d events", fresh[0][0] - cursor - 1)
                    if fresh:
                        cursor = fresh[-1][0]
                for _, event in fresh:
                    self._sse("event", event)
                moment = time.monotonic()
                sig = hub.input_sig()
                if sig != last_sig and moment - last_state >= STATE_INTERVAL_S:
                    last_sig, last_state = sig, moment
                    self._sse("state", hub.state())
                if moment - last_ping >= PING_INTERVAL_S:
                    last_ping = moment
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return

    def _client_closed(self) -> bool:
        """Return True when the peer has closed its end (non-blocking), so the client slot is freed at once."""
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            if not readable:
                return False
            return bool(self.connection.recv(1, socket.MSG_PEEK) == b"")
        except (OSError, ValueError):
            return True

    def _sse(self, name: str, data: object) -> None:
        payload = json.dumps(data, separators=(",", ":"))
        self.wfile.write(f"event: {name}\ndata: {payload}\n\n".encode())
        self.wfile.flush()

    # -- POST ---------------------------------------------------------------------------------------------------

    def do_POST(self) -> None:
        """Owner actions: mark a gate done, or record a verdict (starts ``sae m0 record``)."""
        try:
            if not self._host_ok():
                self._fail(403, "bad Host header")
                return
            origin = self.headers.get("Origin")
            if origin is not None and origin not in self._origins():
                self._fail(403, "bad Origin")
                return
            if not self._token_ok():
                self._fail(403, "bad token")
                return
            parts = urlsplit(self.path).path.split("/")
            if len(parts) != 5 or parts[:3] != ["", "api", "owner"] or parts[4] not in ("done", "verdict"):
                self._fail(404, "not found")
                return
            ref, action = parts[3], parts[4]
            body = self._body()
            gate = _gate(ref)
            if action == "done":
                if gate is None:
                    raise _Bad(400, "ref is not a registry gate")
                self.hub.append_event("done", "marked done", ref)
                self._json(200, {"ok": True})
            else:
                self._verdict(ref, gate, body)
        except _Bad as bad:
            self._fail(bad.status, bad.reason)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception:
            _LOG.exception("POST %s failed", self.path)
            with contextlib.suppress(OSError):
                self._fail(500, "internal error")

    def _body(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise _Bad(400, "chunked bodies are not accepted")
        raw_len = self.headers.get("Content-Length", "")
        if not raw_len.isascii() or not raw_len.isdigit():
            raise _Bad(411, "Content-Length required")
        length = int(raw_len)
        if length > BODY_MAX:
            self.close_connection = True
            raise _Bad(413, f"body is larger than {BODY_MAX} bytes")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError) as exc:
            raise _Bad(400, "body is not valid JSON") from exc
        if not isinstance(data, dict):
            raise _Bad(400, "body must be a JSON object")
        return data

    def _verdict(self, ref: str, gate: Gate | None, body: Mapping[str, Any]) -> None:
        hub = self.hub
        verdict, values, note = _validate_verdict(gate, body)
        user_dir = _user_dir(hub.dev)
        argv = ["uv", "run", "sae", "m0"]
        if user_dir is not None:
            argv += ["--user-dir", user_dir]
        argv += ["record", ref, verdict, *(f"--value={k}={v}" for k, v in values.items()), f"--note={note}"]
        with hub.verdict_lock:
            try:
                proc = hub.runner(argv, cwd=hub.repo, capture_output=True, text=True, timeout=RUNNER_TIMEOUT_S)
            except (OSError, subprocess.SubprocessError) as exc:
                _LOG.warning("verdict runner failed: %s", exc)
                self._json(502, {"ok": False, "stderr": f"{type(exc).__name__}: {exc}"[-STDERR_MAX:]})
                return
            if proc.returncode != 0:
                self._json(502, {"ok": False, "stderr": (proc.stderr or "")[-STDERR_MAX:]})
                return
            hub.append_event(verdict, f"{ref} recorded {verdict}", ref)
        try:
            update_index(hub.dev, from_m0(hub.repo / "m0_results", default_scrub))
        except (OSError, ValueError) as exc:
            _LOG.warning("gate index not updated after verdict: %s", exc)
        self._json(200, {"ok": True})


def make_server(
    repo: Path,
    dev: Path,
    port: int,
    *,
    runner: Runner = subprocess.run,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    static_dir: Path | None = None,
    poll_interval: float = 0.5,
    clock: Clock = time.monotonic,
) -> ThreadingHTTPServer:
    """Bind ``127.0.0.1:<port>`` (0 = ephemeral; read the real port from ``server_address``) and return the server.

    Writes a fresh token (32 hex characters, mode 0600) to ``<dev>/token`` and starts the event poller thread. The
    caller runs ``serve_forever`` (or :func:`serve_forever_with_idle`) and finally ``server_close``. ``runner`` runs
    the verdict command; ``static_dir`` defaults to ``tools/devdash/static``; ``clock`` drives the idle timer.
    """
    hub = _Hub(repo, dev, runner=runner, now=now, clock=clock, static_dir=static_dir or _STATIC_DIR)
    dev.mkdir(parents=True, exist_ok=True)
    srv = _Server(("127.0.0.1", port), _Handler)
    srv.hub = hub
    try:
        token_path = dev / "token"
        fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            with contextlib.suppress(OSError, AttributeError):  # before writing: an old file keeps its old mode
                os.fchmod(handle.fileno(), 0o600)
            handle.write(hub.token)
        hub.poll_once()
        srv.poller = threading.Thread(target=hub.poll_loop, args=(poll_interval,), name="devdash-poll", daemon=True)
        srv.poller.start()
    except BaseException:
        srv.socket.close()
        raise
    return srv


def serve_forever_with_idle(
    srv: ThreadingHTTPServer, idle_hours: float = 12.0, *, check_interval: float = 30.0
) -> None:
    """Serve until shut down or until there has been no event and no SSE client for ``idle_hours``.

    The idle timer uses the ``clock`` given to :func:`make_server`. Returns after the serving thread has stopped.
    """
    hub = cast(_Server, srv).hub
    limit = idle_hours * 3600.0
    thread = threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.1}, name="devdash-serve", daemon=True
    )
    thread.start()
    try:
        while thread.is_alive():
            thread.join(check_interval)
            if hub.idle_seconds() >= limit:
                _LOG.info("no events and no client for %.1f h; shutting down", idle_hours)
                break
    finally:
        srv.shutdown()
        thread.join(5)


def write_server_info(srv: ThreadingHTTPServer, dev: Path) -> None:
    """Write ``<dev>/server.json`` = ``{"pid","port","started"}`` (atomic replace). Call after binding."""
    info = {
        "pid": os.getpid(),
        "port": _bound_port(srv),
        "started": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    tmp = dev / f".server.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(info), encoding="utf-8")
    os.replace(tmp, dev / "server.json")


def remove_server_info(dev: Path) -> None:
    """Remove ``<dev>/server.json`` when it names this process (a newer server's file is left alone)."""
    path = dev / "server.json"
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError, AttributeError):
        return


def run(repo: Path, dev: Path, port: int, *, idle_hours: float = 12.0) -> int:
    """Run the server in the foreground (the ``serve`` command). Returns 0 on a clean stop, 1 if it cannot bind."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        srv = make_server(repo, dev, port)
    except OSError as exc:
        _LOG.error("cannot start on port %d: %s (port in use?)", port, exc)
        return 1
    stopper = threading.Thread(target=srv.shutdown, daemon=True)

    def on_term(_sig: int, _frame: object) -> None:
        if not stopper.is_alive():
            stopper.start()

    signal.signal(signal.SIGTERM, on_term)
    try:
        write_server_info(srv, dev)
        _LOG.info("dashboard on http://127.0.0.1:%d/ (pid %d)", srv.server_address[1], os.getpid())
        serve_forever_with_idle(srv, idle_hours)
    except KeyboardInterrupt:
        pass
    finally:
        remove_server_info(dev)
        srv.server_close()
    return 0
