"""Tests for devdash.server: endpoints, request validation, the verdict endpoint and SSE.

Every test that starts a server stops it in the fixture teardown (also on failure). The runner is always a fake, so no
process is started except in the test that parses argv with the real ``sae m0`` parser (parsing only, no run).
"""

from __future__ import annotations

import http.client
import json
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from devdash import events as ev
from devdash import server as srvmod
from gates import load_gates

GATES = load_gates()
JUDGED = next(g for g in GATES if g.judged)
UNJUDGED = next(g for g in GATES if not g.judged)
KEY = str(JUDGED.evidence[0]["key"])


class FakeRunner:
    """Records calls and returns a canned CompletedProcess."""

    def __init__(self, code: int = 0, stderr: str = "") -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.code = code
        self.stderr = stderr

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, self.code, stdout="", stderr=self.stderr)


class Live:
    """A running test server plus helpers."""

    def __init__(self, repo: Path, dev: Path, runner: FakeRunner, static: Path) -> None:
        self.repo, self.dev, self.runner = repo, dev, runner
        self.srv = srvmod.make_server(
            repo, dev, 0, runner=runner, static_dir=static, poll_interval=0.05, now=lambda: datetime.now(UTC)
        )
        self.port: int = self.srv.server_address[1]
        self.token = (dev / "token").read_text(encoding="utf-8")
        self.thread = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def request(
        self,
        method: str,
        path: str,
        body: object | bytes | None = None,
        *,
        headers: dict[str, str] | None = None,
        token: bool = True,
        host: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            hdrs = {"Host": host if host is not None else f"127.0.0.1:{self.port}"}
            if method == "POST" and token:
                hdrs["X-SAE-Token"] = self.token
            hdrs.update(headers or {})
            payload: bytes | None
            if body is None:
                payload = None
            elif isinstance(body, bytes):
                payload = body
            else:
                payload = json.dumps(body).encode()
            conn.request(method, path, body=payload, headers=hdrs)
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        finally:
            conn.close()

    def json(self, method: str, path: str, body: object | None = None, **kw: Any) -> tuple[int, Any]:
        status, _, data = self.request(method, path, body, **kw)
        return status, json.loads(data) if data else None

    def stop(self) -> None:
        self.srv.shutdown()
        self.thread.join(5)
        self.srv.server_close()


@pytest.fixture
def make_live(tmp_path: Path, dev: Path) -> Iterator[Callable[..., Live]]:
    started: list[Live] = []
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>token=__SAE_TOKEN__</html>", encoding="utf-8")
    (static / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()

    def factory(runner: FakeRunner | None = None) -> Live:
        live = Live(repo, dev, runner or FakeRunner(), static)
        started.append(live)
        return live

    try:
        yield factory
    finally:
        for live in started:
            live.stop()


@pytest.fixture
def live(make_live: Callable[..., Live]) -> Live:
    return make_live()


def _verdict(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"verdict": "pass", "values": {KEY: "ok"}, "note": "fine"}
    body.update(over)
    return body


def test_health_and_state(live: Live) -> None:
    status, data = live.json("GET", "/api/health")
    assert status == 200 and data["ok"] is True and isinstance(data["pid"], int)
    status, state = live.json("GET", "/api/state")
    assert status == 200 and "kpis" in state and "gates" in state
    assert (live.dev / "token").read_text(encoding="utf-8") == live.token
    assert len(live.token) == 32 and int(live.token, 16) >= 0


def test_index_substitutes_token_and_static_served(live: Live) -> None:
    status, headers, body = live.request("GET", "/")
    assert status == 200 and live.token.encode() in body and b"__SAE_TOKEN__" not in body
    assert headers["cache-control"] == "no-store"
    status, _, body = live.request("GET", "/static/app.js")
    assert status == 200 and body == b"console.log(1)"


def test_missing_static_dir_is_404(tmp_path: Path, dev: Path) -> None:
    srv = srvmod.make_server(tmp_path, dev, 0, static_dir=tmp_path / "nope")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        conn.request("GET", "/")
        assert conn.getresponse().status == 404
        conn.close()
    finally:
        srv.shutdown()
        thread.join(5)
        srv.server_close()


@pytest.mark.parametrize(
    "path",
    [
        "/static/../../pyproject.toml",
        "/static/../secret.txt",
        "/static/%2e%2e/secret.txt",
        "/static/%2e%2e%2fsecret.txt",
        "/static/..%5csecret.txt",
        "/static//etc/passwd",
        "/static/.hidden",
        "/static/",
        "/static/app.js%00.png",
    ],
)
def test_static_traversal_403_or_404(live: Live, path: str) -> None:
    status, _, body = live.request("GET", path)
    assert status in (403, 404)
    assert b"secret" not in body and b"[project]" not in body


def test_static_symlink_escape_rejected(live: Live, tmp_path: Path) -> None:
    link = tmp_path / "static" / "evil.txt"
    link.symlink_to(tmp_path / "secret.txt")
    status, _, body = live.request("GET", "/static/evil.txt")
    assert status in (403, 404) and b"secret" not in body


@pytest.mark.parametrize("bad", ["evil.com", "127.0.0.1:1", "localhost", "127.0.0.1", "localhost.evil.com:80", ""])
def test_bad_host_403(live: Live, bad: str) -> None:
    assert live.request("GET", "/api/health", host=bad)[0] == 403
    assert live.request("POST", "/api/owner/x/done", {}, host=bad)[0] == 403


def test_localhost_host_allowed(live: Live) -> None:
    assert live.request("GET", "/api/health", host=f"localhost:{live.port}")[0] == 200


def test_post_without_token_403(live: Live) -> None:
    assert live.request("POST", f"/api/owner/{JUDGED.id}/done", {}, token=False)[0] == 403
    assert live.request("POST", f"/api/owner/{JUDGED.id}/done", {}, headers={"X-SAE-Token": "0" * 32})[0] == 403
    assert live.request("POST", f"/api/owner/{JUDGED.id}/done", {}, headers={"X-SAE-Token": ""})[0] == 403
    assert live.runner.calls == []


def test_post_cross_origin_403(live: Live) -> None:
    status, _, _ = live.request("POST", f"/api/owner/{JUDGED.id}/done", {}, headers={"Origin": "http://evil.com"})
    assert status == 403


def test_mark_done_appends_event(live: Live) -> None:
    status, data = live.json("POST", f"/api/owner/{JUDGED.id}/done", {})
    assert status == 200 and data == {"ok": True}
    got, invalid = ev.read_events([ev.events_path(live.dev, "wsl")])
    assert invalid == 0
    assert [(e["source"], e["kind"], e["ref"], e["msg"]) for e in got] == [
        ("owner", "done", JUDGED.id, "marked done")
    ]


def test_mark_done_unknown_ref_400(live: Live) -> None:
    assert live.request("POST", "/api/owner/not-a-gate/done", {})[0] == 400
    assert live.request("POST", "/api/owner/..%2f..%2fx/done", {})[0] in (400, 404)
    assert not ev.events_path(live.dev, "wsl").exists()


def test_body_limits(live: Live) -> None:
    path = f"/api/owner/{JUDGED.id}/verdict"
    assert live.request("POST", path, b"x" * 20000)[0] == 413
    assert live.request("POST", path, b"[1,2]")[0] == 400
    assert live.request("POST", path, b"{not json")[0] == 400
    assert live.request("POST", path, b'"str"')[0] == 400
    assert live.runner.calls == []


@pytest.mark.parametrize(
    ("ref", "body"),
    [
        ("NOPE", _verdict()),
        (UNJUDGED.id, _verdict()),
        (JUDGED.id, _verdict(verdict="skip")),
        (JUDGED.id, _verdict(verdict=1)),
        (JUDGED.id, _verdict(values={"bogus_key": "x"})),
        (JUDGED.id, _verdict(values={KEY: "a\nb"})),
        (JUDGED.id, _verdict(values={KEY: "x" * 201})),
        (JUDGED.id, _verdict(values={KEY: ""})),
        (JUDGED.id, _verdict(values={KEY: 5})),
        (JUDGED.id, _verdict(values=[KEY])),
        (JUDGED.id, _verdict(note="a\x00b")),
        (JUDGED.id, _verdict(note="n" * 501)),
        (JUDGED.id, _verdict(note=5)),
        (JUDGED.id, {"values": {}}),
    ],
)
def test_verdict_rejects(live: Live, ref: str, body: dict[str, Any]) -> None:
    status, data = live.json("POST", f"/api/owner/{ref}/verdict", body)
    assert status == 400 and data["ok"] is False and data["error"]
    assert live.runner.calls == []
    assert not ev.events_path(live.dev, "wsl").exists()


def test_verdict_boundaries_accepted(live: Live) -> None:
    body = _verdict(values={KEY: "x" * 200}, note="n" * 500)
    assert live.json("POST", f"/api/owner/{JUDGED.id}/verdict", body)[0] == 200


def test_verdict_flaglike_value_is_one_argv_item(live: Live) -> None:
    body = _verdict(values={KEY: "--note=x"}, note="-x")
    assert live.json("POST", f"/api/owner/{JUDGED.id}/verdict", body)[0] == 200
    argv, kwargs = live.runner.calls[0]
    assert argv[:4] == ["uv", "run", "sae", "m0"]
    assert f"--value={KEY}=--note=x" in argv and "--note=-x" in argv
    assert kwargs["cwd"] == live.repo and kwargs["timeout"] == 60 and kwargs["capture_output"] is True
    assert kwargs["text"] is True
    from sae.m0 import cli as m0cli

    args = m0cli._parser().parse_args(argv[4:])
    assert args.check == JUDGED.id and args.status == "pass" and args.note == "-x"
    assert args.value == [f"{KEY}=--note=x"]


def test_verdict_user_dir_only_when_env_has_it(make_live: Callable[..., Live], dev: Path) -> None:
    first = make_live()
    first.json("POST", f"/api/owner/{JUDGED.id}/verdict", _verdict())
    assert "--user-dir" not in first.runner.calls[0][0]
    dev.mkdir(parents=True, exist_ok=True)
    (dev / "env.json").write_text(json.dumps({"user_dir": "/some/dir"}), encoding="utf-8")
    second = make_live()
    second.json("POST", f"/api/owner/{JUDGED.id}/verdict", _verdict())
    argv = second.runner.calls[0][0]
    assert argv[4:7] == ["--user-dir", "/some/dir", "record"]
    assert argv[7:9] == [JUDGED.id, "pass"]


def test_verdict_dash_user_dir_refused(make_live: Callable[..., Live], dev: Path) -> None:
    dev.mkdir(parents=True, exist_ok=True)
    (dev / "env.json").write_text(json.dumps({"user_dir": "--evil"}), encoding="utf-8")
    other = make_live()
    status, data = other.json("POST", f"/api/owner/{JUDGED.id}/verdict", _verdict())
    assert status == 500 and data["ok"] is False and other.runner.calls == []


@pytest.mark.parametrize("verdict", ["pass", "fail"])
def test_verdict_emits_closing_event(live: Live, verdict: str) -> None:
    status, data = live.json("POST", f"/api/owner/{JUDGED.id}/verdict", _verdict(verdict=verdict))
    assert status == 200 and data == {"ok": True}
    got, _ = ev.read_events([ev.events_path(live.dev, "wsl")])
    assert [(e["kind"], e["ref"], e["source"]) for e in got] == [(verdict, JUDGED.id, "owner")]


def test_verdict_runner_failure_502(make_live: Callable[..., Live]) -> None:
    live = make_live(FakeRunner(code=2, stderr="boom"))
    status, data = live.json("POST", f"/api/owner/{JUDGED.id}/verdict", _verdict())
    assert status == 502 and data["ok"] is False and "boom" in data["stderr"]
    assert not ev.events_path(live.dev, "wsl").exists()


def test_verdict_stderr_truncated_to_last_2000(make_live: Callable[..., Live]) -> None:
    live = make_live(FakeRunner(code=1, stderr="A" * 5000 + "END"))
    _, data = live.json("POST", f"/api/owner/{JUDGED.id}/verdict", _verdict())
    assert len(data["stderr"]) == 2000 and data["stderr"].endswith("END")


def test_verdict_runner_timeout_502(make_live: Callable[..., Live]) -> None:
    def boom(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, 60)

    live = make_live()
    live.srv.hub.runner = boom
    status, data = live.json("POST", f"/api/owner/{JUDGED.id}/verdict", _verdict())
    assert status == 502 and data["ok"] is False


def test_verdicts_are_serialized(make_live: Callable[..., Live]) -> None:
    active = 0
    peak = 0
    guard = threading.Lock()

    def slow(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.1)
        with guard:
            active -= 1
        return subprocess.CompletedProcess(argv, 0, "", "")

    live = make_live()
    live.srv.hub.runner = slow
    threads = [
        threading.Thread(target=live.json, args=("POST", f"/api/owner/{JUDGED.id}/verdict", _verdict()))
        for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert peak == 1


def _write_event(dev: Path, ts: str, msg: str, writer: str = "wsl") -> None:
    ev.append(
        ev.make_event("t", "info", msg, now=datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)),
        ev.events_path(dev, writer),
    )


def test_events_since(live: Live) -> None:
    _write_event(live.dev, "2026-01-01T00:00:01", "a")
    _write_event(live.dev, "2026-01-01T00:00:03", "c", "win")
    _write_event(live.dev, "2026-01-01T00:00:02", "b")
    live.srv.hub.poll_once()
    status, data = live.json("GET", "/api/events")
    assert status == 200 and [e["msg"] for e in data["events"]] == ["a", "b", "c"]
    _, data = live.json("GET", "/api/events?since=2026-01-01T00:00:01.000Z")
    assert [e["msg"] for e in data["events"]] == ["b", "c"]
    assert live.json("GET", "/api/events?since=" + "9" * 100)[0] == 400


def test_unknown_route_and_method(live: Live) -> None:
    assert live.request("GET", "/api/nope")[0] == 404
    assert live.request("POST", "/api/health", {})[0] in (404, 405)
    assert live.request("DELETE", "/api/state")[0] in (404, 405, 501)


def _read_sse(live: Live, want: int, *, after: Callable[[], None] | None = None) -> list[tuple[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", live.port, timeout=5)
    try:
        conn.request("GET", "/api/stream", headers={"Host": f"127.0.0.1:{live.port}"})
        resp = conn.getresponse()
        assert resp.status == 200 and resp.getheader("Content-Type", "").startswith("text/event-stream")
        out: list[tuple[str, Any]] = []
        name = ""
        triggered = False
        while len(out) < want:
            line = resp.fp.readline().decode().rstrip("\n")  # type: ignore[union-attr]
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                out.append((name, json.loads(line[6:])))
                if not triggered and after is not None:
                    triggered = True
                    after()
        return out
    finally:
        conn.close()


def test_sse_first_message_is_state(live: Live) -> None:
    (first,) = _read_sse(live, 1)
    assert first[0] == "state" and "kpis" in first[1]


def test_sse_survives_torn_line(live: Live) -> None:
    path = ev.events_path(live.dev, "wsl")

    def append() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as fh:
            fh.write(b'{"v":1,"ts":"torn\n')
        ev.append(ev.make_event("t", "info", "after-torn"), path)

    msgs = _read_sse(live, 2, after=append)
    assert msgs[0][0] == "state"
    assert msgs[1][0] == "event" and msgs[1][1]["msg"] == "after-torn"


def test_idle_exit_with_injected_clock(make_live: Callable[..., Live], tmp_path: Path, dev: Path) -> None:
    now = [1000.0]
    srv = srvmod.make_server(tmp_path, dev, 0, static_dir=tmp_path, poll_interval=0.05, clock=lambda: now[0])
    done = threading.Event()

    def run() -> None:
        srvmod.serve_forever_with_idle(srv, idle_hours=1.0, check_interval=0.02)
        done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        time.sleep(0.2)
        assert not done.is_set()
        now[0] += 3599
        time.sleep(0.2)
        assert not done.is_set()
        _write_event(dev, "2026-01-01T00:00:01", "activity")
        time.sleep(0.3)  # the new event counts as activity at the (frozen) current time
        now[0] += 3599
        time.sleep(0.2)
        assert not done.is_set()
        now[0] += 10
        assert done.wait(5)
    finally:
        srv.shutdown()
        thread.join(5)
        srv.server_close()


def test_hub_rotation_keeps_events(live: Live) -> None:
    path = ev.events_path(live.dev, "wsl")
    path.parent.mkdir(parents=True, exist_ok=True)
    pad = json.dumps(ev.make_event("t", "info", "p" * 150, now=datetime(2026, 1, 1, tzinfo=UTC)), separators=(",", ":"))
    with path.open("w", encoding="ascii") as fh:
        fh.write((pad + "\n") * (ev.ROTATE_BYTES // (len(pad) + 1) + 1))
    _write_event(live.dev, "2026-01-02T00:00:02", "last-before-rotation")
    live.srv.hub.poll_once()  # polls first, so the last line is seen, then rotates
    assert not path.exists()
    assert (live.dev / "events.1.jsonl").exists()
    _write_event(live.dev, "2026-01-02T00:00:03", "after-rotation")
    live.srv.hub.poll_once()
    _, data = live.json("GET", "/api/events?since=2026-01-02T00:00:00.000Z")
    assert [e["msg"] for e in data["events"]] == ["last-before-rotation", "after-rotation"]


def test_gh_cache_60s() -> None:
    calls: list[list[str]] = []
    clock = [0.0]

    def fake(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "out", "")

    gh = srvmod.make_gh(runner=fake, clock=lambda: clock[0])
    assert gh(["pr", "list"]) == "out" and gh(["pr", "list"]) == "out"
    assert len(calls) == 1 and calls[0][0] == "gh"
    clock[0] = 61.0
    gh(["pr", "list"])
    assert len(calls) == 2


def test_gh_failures_give_none() -> None:
    def missing(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh")

    def bad(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "no")

    def slow(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        assert kw["timeout"] == 10
        raise subprocess.TimeoutExpired(argv, 10)

    for runner in (missing, bad, slow):
        assert srvmod.make_gh(runner=runner)(["x"]) is None


def test_server_info_written_and_removed(live: Live) -> None:
    srvmod.write_server_info(live.srv, live.dev)
    info = json.loads((live.dev / "server.json").read_text(encoding="utf-8"))
    assert set(info) == {"pid", "port", "started"} and info["port"] == live.port
    srvmod.remove_server_info(live.dev)
    assert not (live.dev / "server.json").exists()


def test_second_bind_raises_oserror(live: Live, tmp_path: Path) -> None:
    with pytest.raises(OSError):
        srvmod.make_server(tmp_path, tmp_path / "dev2", live.port, static_dir=tmp_path)


def test_cross_site_api_get_refused(live: Live) -> None:
    assert live.request("GET", "/api/state", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert live.request("GET", "/api/health", headers={"Sec-Fetch-Site": "same-site"})[0] == 403
    assert live.request("GET", "/api/state", headers={"Origin": "http://evil.com"})[0] == 403
    assert live.request("GET", "/api/health", headers={"Sec-Fetch-Site": "same-origin"})[0] == 200
    assert live.request("GET", "/api/health", headers={"Sec-Fetch-Site": "none"})[0] == 200
    assert live.request("GET", "/api/health", headers={"Origin": f"http://localhost:{live.port}"})[0] == 200


def test_sse_client_cap(live: Live) -> None:
    conns = []
    try:
        for _ in range(srvmod.SSE_MAX_CLIENTS):
            c = http.client.HTTPConnection("127.0.0.1", live.port, timeout=5)
            c.request("GET", "/api/stream", headers={"Host": f"127.0.0.1:{live.port}"})
            assert c.getresponse().status == 200
            conns.append(c)
        assert live.request("GET", "/api/stream")[0] == 503
    finally:
        for c in conns:
            c.close()


def test_token_file_mode_fixed_before_write(tmp_path: Path, dev: Path) -> None:
    dev.mkdir(parents=True)
    (dev / "token").write_text("old-secret", encoding="utf-8")
    (dev / "token").chmod(0o644)
    srv = srvmod.make_server(tmp_path, dev, 0, static_dir=tmp_path)
    try:
        assert (dev / "token").stat().st_mode & 0o777 == 0o600
    finally:
        srv.server_close()


def test_judged_gates_are_known_to_m0() -> None:
    from sae.m0.checks import BY_ID

    assert {g.id for g in GATES if g.judged} <= set(BY_ID)


def test_input_sig_changes_each_minute(tmp_path: Path, dev: Path) -> None:
    moments = [datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)]
    srv = srvmod.make_server(tmp_path, dev, 0, static_dir=tmp_path, now=lambda: moments[0])
    try:
        before = srv.hub.input_sig()  # type: ignore[attr-defined]
        moments[0] += timedelta(seconds=30)
        assert srv.hub.input_sig() == before  # type: ignore[attr-defined]
        moments[0] += timedelta(seconds=40)
        assert srv.hub.input_sig() != before  # type: ignore[attr-defined]
    finally:
        srv.server_close()


def test_sse_slots_freed_on_disconnect(live: Live) -> None:
    started = time.monotonic()
    for _ in range(12):
        c = http.client.HTTPConnection("127.0.0.1", live.port, timeout=5)
        c.request("GET", "/api/stream", headers={"Host": f"127.0.0.1:{live.port}"})
        resp = c.getresponse()
        assert resp.status == 200
        resp.fp.readline()  # type: ignore[union-attr]
        c.close()
        time.sleep(0.25)
    assert time.monotonic() - started < 10
