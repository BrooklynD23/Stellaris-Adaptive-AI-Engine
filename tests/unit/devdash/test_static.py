"""Static front-end checks: the three files in ``tools/devdash/static`` follow the page's safety and data rules.

These are text checks (no browser): no HTML-injection sinks in ``app.js``, no remote URLs, the token placeholder
appears once, the page loads only same-origin script and style (the server's CSP forbids inline script), and every
top-level snapshot key is consumed by ``app.js``. One test serves the real page through ``make_server``.
"""

from __future__ import annotations

import http.client
import re
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from devdash import server as srvmod
from devdash import state

STATIC = Path(__file__).resolve().parents[3] / "tools" / "devdash" / "static"
FILES = ("index.html", "app.js", "app.css")
NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _strip_js_comments(text: str) -> str:
    """Drop ``//`` line comments and ``/* */`` blocks so a sink named in a comment still counts as code-free."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


def test_files_exist() -> None:
    for name in FILES:
        assert (STATIC / name).is_file(), name


@pytest.mark.parametrize("sink", ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"])
def test_no_innerhtml_with_data(sink: str) -> None:
    assert sink not in _read("app.js"), f"app.js uses {sink}; render data with textContent/createElement"


def test_no_eval_like_sinks() -> None:
    code = _strip_js_comments(_read("app.js"))
    for pattern in (r"\beval\s*\(", r"new\s+Function\s*\(", r"setTimeout\s*\(\s*['\"]", r"setInterval\s*\(\s*['\"]"):
        assert re.search(pattern, code) is None, pattern


@pytest.mark.parametrize("name", FILES)
def test_static_no_external_urls(name: str) -> None:
    text = _read(name)
    assert "http://" not in text and "https://" not in text, name


def test_token_placeholder_present() -> None:
    page = _read("index.html")
    assert page.count("__SAE_TOKEN__") == 1
    assert re.search(r'<meta\s+name="sae-token"\s+content="__SAE_TOKEN__"\s*/?>', page)


def test_index_is_csp_compatible() -> None:
    page = _read("index.html")
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", page, flags=re.S | re.I)
    assert scripts, "index.html loads no script"
    for attrs, body in scripts:
        assert 'src="/static/app.js"' in attrs and not body.strip(), "inline <script> is blocked by the CSP"
    assert '<link rel="stylesheet" href="/static/app.css">' in page
    assert re.search(r"\son[a-z]+\s*=", page, flags=re.I) is None, "inline event handlers are blocked by the CSP"
    assert "<style" not in page.lower()


def test_every_snapshot_key_used(tmp_path: Path, dev: Path) -> None:
    snap = state.snapshot(tmp_path, dev, now=NOW)
    code = _read("app.js")
    missing = [key for key in snap if not re.search(rf"\b{re.escape(key)}\b", code)]
    assert not missing, f"app.js never reads snapshot keys {missing}"


def test_every_gate_status_has_a_label() -> None:
    code = _read("app.js")
    for status in ("pass", "fail", "run", "owner", "skip", "todo", "setup", "hardware", "later", "waiting"):
        assert re.search(rf"\b{status}\s*:\s*\{{", code), f"no STATUS entry for {status}"


def test_posts_carry_token_header() -> None:
    code = _read("app.js")
    assert "X-SAE-Token" in code and "application/json" in code
    assert "/api/stream" in code and "/api/state" in code


def test_light_scheme_and_narrow_layout() -> None:
    css = _read("app.css")
    assert "prefers-color-scheme: light" in css
    assert re.search(r"@media\s*\(max-width:\s*720px\)", css)


def test_index_served_with_token(tmp_path: Path, dev: Path) -> None:
    srv = srvmod.make_server(tmp_path, dev, 0, poll_interval=0.05)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        port = srv.server_address[1]
        token = (dev / "token").read_text(encoding="utf-8")
        bodies: dict[str, bytes] = {}
        for path in ("/", "/static/app.js", "/static/app.css"):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
                resp = conn.getresponse()
                assert resp.status == 200, path
                bodies[path] = resp.read()
            finally:
                conn.close()
        page = bodies["/"].decode("utf-8")
        assert f'content="{token}"' in page and "__SAE_TOKEN__" not in page
        assert bodies["/static/app.js"] == (STATIC / "app.js").read_bytes()
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(5)


def test_results_view_labels_parse_numbers_unverified_and_uses_textcontent() -> None:
    code = _read("app.js")
    assert "UNVERIFIED A-24 (measured on " in code
    assert "rss_bytes" in code
    assert "parseNote" in code
    assert "innerHTML" not in code
