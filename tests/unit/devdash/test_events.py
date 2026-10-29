"""Tests for ``devdash.events``: event shape, redaction, append and read, torn lines, rotation, tailing and the flag."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from devdash import REPO, cli, dev_dir, events
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
_TOOLS = REPO / "tools" / "devdash"
_PROPERTY = settings(
    derandomize=True,
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _at(seconds: float) -> datetime:
    """Return a timestamp ``seconds`` after the fixed test epoch."""
    return _T0 + timedelta(seconds=seconds)


def _line(event: dict[str, object]) -> bytes:
    """Serialise an event the way ``append`` does, including the trailing newline."""
    return (json.dumps(event, separators=(",", ":")) + "\n").encode("ascii")


# --- event construction -------------------------------------------------------------------------------------------


def test_make_event_shape() -> None:
    event = events.make_event("orchestrator", "start", "hello", now=_T0)
    assert list(event) == ["v", "ts", "source", "kind", "msg"]
    assert event == {
        "v": 1,
        "ts": "2026-10-29T12:00:00.000Z",
        "source": "orchestrator",
        "kind": "start",
        "msg": "hello",
    }


def test_make_event_optional_fields_only_when_given() -> None:
    bare = events.make_event("ci", "info", "m", now=_T0)
    assert "ref" not in bare and "pct" not in bare and "session" not in bare
    full = events.make_event("ci", "progress", "half", ref="X3-G5", pct=0, session="abc", now=_T0)
    assert full["ref"] == "X3-G5"
    assert full["pct"] == 0
    assert full["session"] == "abc"
    assert list(full) == ["v", "ts", "source", "kind", "msg", "ref", "pct", "session"]


def test_make_event_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown event kind"):
        events.make_event("x", "bogus", "m")


@pytest.mark.parametrize("pct", [101, -1, True])
def test_make_event_rejects_bad_pct(pct: int) -> None:
    with pytest.raises(ValueError, match="pct"):
        events.make_event("x", "progress", "m", pct=pct, now=_T0)


@pytest.mark.parametrize("source", ["", "   "])
def test_make_event_empty_source_raises(source: str) -> None:
    with pytest.raises(ValueError, match="source"):
        events.make_event(source, "info", "m", now=_T0)


def test_make_event_naive_timestamp_raises() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        events.make_event("x", "info", "m", now=datetime(2026, 10, 29, 12, 0, 0))


def test_make_event_timestamp_is_utc_with_milliseconds() -> None:
    plus_two = timezone(timedelta(hours=2))
    event = events.make_event("x", "info", "m", now=datetime(2026, 10, 29, 14, 0, 0, 123999, tzinfo=plus_two))
    assert event["ts"] == "2026-10-29T12:00:00.123Z"


def test_msg_redacted_and_cut() -> None:
    event = events.make_event("x", "info", str(Path.home()) + "/a\nb" + "z" * 300)
    msg = str(event["msg"])
    assert msg.startswith("<HOME>/a b")
    assert len(msg) == 200


def test_msg_flattens_control_characters() -> None:
    event = events.make_event("x", "info", "a\r\nb\tc\x00d", now=_T0)
    assert event["msg"] == "a b c d"


def test_redact_replaces_home_in_both_slash_styles() -> None:
    text = "C:/Users/me/x and C:\\Users\\me\\y"
    assert events.redact(text, home="C:\\Users\\me") == "<HOME>/x and <HOME>\\y"


@pytest.mark.parametrize("home", ["/", "", "\\"])
def test_redact_never_replaces_a_bare_root(home: str) -> None:
    assert events.redact("/a/b", home=home) == "/a/b"


def test_redact_uses_the_real_home_by_default() -> None:
    assert events.redact(str(Path.home()) + "/x") == "<HOME>/x"


# --- append, read and torn lines ----------------------------------------------------------------------------------


def test_append_read_roundtrip(dev: Path) -> None:
    path = events.events_path(dev)
    for seq in (2, 0, 1):  # written out of order on purpose
        events.append(events.make_event("x", "info", f"m{seq}", now=_at(seq)), path)
    got, invalid = events.read_events([path])
    assert [event["msg"] for event in got] == ["m0", "m1", "m2"]
    assert invalid == 0


def test_torn_line_skipped(dev: Path) -> None:
    path = events.events_path(dev)
    events.append(events.make_event("x", "info", "first", now=_at(0)), path)
    with path.open("ab") as handle:
        handle.write(b'{"v":1,"ts"\n')  # a torn fragment, terminated by a newline
    events.append(events.make_event("x", "info", "second", now=_at(1)), path)
    got, invalid = events.read_events([path])
    assert [event["msg"] for event in got] == ["first", "second"]
    assert invalid == 1


def test_unterminated_fragment_counted_by_full_read(dev: Path) -> None:
    path = events.events_path(dev)
    events.append(events.make_event("x", "info", "only", now=_at(0)), path)
    with path.open("ab") as handle:
        handle.write(b'{"v":1,"ts"')  # no newline: a write still in progress or torn at the end
    got, invalid = events.read_events([path])
    assert [event["msg"] for event in got] == ["only"]
    assert invalid == 1


def test_invalid_shapes_are_counted_not_returned(dev: Path) -> None:
    path = events.events_path(dev)
    good = events.make_event("x", "info", "good", now=_at(0))
    bad_lines = [
        [],
        {**good, "v": 2},
        {**good, "v": True},
        {**good, "ts": "2026-10-29 12:00:00"},
        {**good, "kind": "bogus"},
        {**good, "pct": "half"},
        {**good, "msg": None},
    ]
    payload = _line(good) + b"".join(_line(line) if isinstance(line, dict) else b"[]\n" for line in bad_lines)
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    got, invalid = events.read_events([path])
    assert [event["msg"] for event in got] == ["good"]
    assert invalid == len(bad_lines)


def test_read_events_since_is_strictly_after(dev: Path) -> None:
    path = events.events_path(dev)
    for seq in range(3):
        events.append(events.make_event("x", "info", f"m{seq}", now=_at(seq)), path)
    got, _ = events.read_events([path], since_ts="2026-10-29T12:00:01.000Z")
    assert [event["msg"] for event in got] == ["m2"]


def test_missing_files_are_fine(dev: Path) -> None:
    assert events.read_events([dev / "absent.jsonl"]) == ([], 0)


def test_merge_wsl_and_win_by_timestamp(dev: Path) -> None:
    wsl = events.events_path(dev, "wsl")
    win = events.events_path(dev, "win")
    events.append(events.make_event("wsl-a", "info", "a", now=_at(0)), wsl)
    events.append(events.make_event("win-b", "info", "b", now=_at(1)), win)
    events.append(events.make_event("wsl-c", "info", "c", now=_at(2)), wsl)
    events.append(events.make_event("win-d", "info", "d", now=_at(3)), win)
    got, invalid = events.read_events([wsl, win])
    assert [event["source"] for event in got] == ["wsl-a", "win-b", "wsl-c", "win-d"]
    assert invalid == 0


def test_events_path_names() -> None:
    dev = Path("/example/.sae-dev")
    assert events.events_path(dev) == dev / "events.jsonl"
    assert events.events_path(dev, "win") == dev / "events.win.jsonl"
    with pytest.raises(ValueError, match="unknown writer"):
        events.events_path(dev, "mac")


# --- concurrency --------------------------------------------------------------------------------------------------

_WORKER = (
    "import sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from devdash import events\n"
    "target, tag, count = Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])\n"
    "for seq in range(count):\n"
    "    events.append(events.make_event('worker', 'progress', f'{tag}-{seq}', pct=seq % 101), target)\n"
)


@pytest.fixture(params=["tmp", "repo"])
def shared_dir(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Path]:
    """A directory for the concurrency test: pytest's tmp_path, or a drvfs directory inside the repo.

    The repo variant is removed in teardown even when the test fails.
    """
    if request.param == "tmp":
        yield tmp_path
        return
    scratch = REPO / f".sae-dev-test-{uuid.uuid4().hex}"
    scratch.mkdir()
    try:
        yield scratch
    finally:
        shutil.rmtree(scratch)


def test_concurrent_append(shared_dir: Path) -> None:
    """Eight separate processes each append 200 events to one file: 1600 valid lines, none torn or glued."""
    workers, per_worker = 8, 200
    path = shared_dir / "events.jsonl"
    procs = [
        subprocess.Popen(
            [sys.executable, "-I", "-c", _WORKER, str(_TOOLS), str(path), f"w{index}", str(per_worker)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for index in range(workers)
    ]
    for proc in procs:
        _, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err.decode(errors="replace")
    raw = path.read_bytes()
    assert raw.count(b"\n") == workers * per_worker
    got, invalid = events.read_events([path])
    assert invalid == 0
    assert len(got) == workers * per_worker
    assert len({event["msg"] for event in got}) == workers * per_worker


# --- rotation -----------------------------------------------------------------------------------------------------


def test_rotation_rolls_when_full_and_append_never_rotates(dev: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "ROTATE_BYTES", 200)
    path = events.events_path(dev)
    for seq in range(10):
        events.append(events.make_event("x", "info", f"m{seq}", now=_at(seq)), path)
    assert path.stat().st_size >= 200
    assert not (dev / "events.1.jsonl").exists()

    first = events.rotate_if_needed(path)
    assert first == dev / "events.1.jsonl"
    assert first.exists()
    assert not path.exists()

    for seq in range(10, 20):
        events.append(events.make_event("x", "info", f"m{seq}", now=_at(seq)), path)
    assert events.rotate_if_needed(path) == dev / "events.2.jsonl"

    events.append(events.make_event("x", "info", "tiny", now=_at(30)), path)
    assert events.rotate_if_needed(path) is None
    assert path.exists()


def test_rotation_names_the_win_log_separately(dev: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "ROTATE_BYTES", 200)
    win = events.events_path(dev, "win")
    for seq in range(10):
        events.append(events.make_event("win", "info", f"w{seq}", now=_at(seq)), win)
    assert events.rotate_if_needed(win) == dev / "events.win.1.jsonl"


def test_rotation_missing_file_and_bad_name(dev: Path) -> None:
    assert events.rotate_if_needed(dev / "events.jsonl") is None
    with pytest.raises(ValueError, match="not a JSON Lines log"):
        events.rotate_if_needed(dev / "events.txt")


# --- tailing ------------------------------------------------------------------------------------------------------


def test_tail_incremental(dev: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = events.events_path(dev)
    tail = events.Tail(path, maxlen=3)
    assert tail.poll() == ([], 0)

    for seq in range(2):
        events.append(events.make_event("x", "info", f"m{seq}", now=_at(seq)), path)
    fresh, invalid = tail.poll()
    assert [event["msg"] for event in fresh] == ["m0", "m1"]
    assert invalid == 0

    events.append(events.make_event("x", "info", "m2", now=_at(2)), path)
    fresh, invalid = tail.poll()
    assert [event["msg"] for event in fresh] == ["m2"]
    assert invalid == 0

    # A partial line is held back until its newline arrives.
    complete = _line(events.make_event("x", "info", "m3", now=_at(3)))
    with path.open("ab") as handle:
        handle.write(complete[:12])
    assert tail.poll() == ([], 0)
    with path.open("ab") as handle:
        handle.write(complete[12:])
    fresh, invalid = tail.poll()
    assert [event["msg"] for event in fresh] == ["m3"]
    assert invalid == 0

    # Rotation: a new inode, so the new file is read from byte 0.
    monkeypatch.setattr(events, "ROTATE_BYTES", 1)
    assert events.rotate_if_needed(path) is not None
    events.append(events.make_event("x", "info", "m4", now=_at(4)), path)
    fresh, invalid = tail.poll()
    assert [event["msg"] for event in fresh] == ["m4"]
    assert invalid == 0
    assert len(tail.events) <= 3
    assert [event["msg"] for event in tail.events] == ["m2", "m3", "m4"]


def test_tail_skips_torn_line_and_still_delivers_the_next_valid_line(dev: Path) -> None:
    path = events.events_path(dev)
    tail = events.Tail(path)
    events.append(events.make_event("x", "info", "before", now=_at(0)), path)
    with path.open("ab") as handle:
        handle.write(b'{"v":1,"ts"\n')
    events.append(events.make_event("x", "info", "after", now=_at(1)), path)
    fresh, invalid = tail.poll()
    assert [event["msg"] for event in fresh] == ["before", "after"]
    assert invalid == 1


def test_tail_restarts_after_truncation(dev: Path) -> None:
    path = events.events_path(dev)
    tail = events.Tail(path)
    events.append(events.make_event("x", "info", "old-1", now=_at(0)), path)
    events.append(events.make_event("x", "info", "old-2", now=_at(1)), path)
    assert len(tail.poll()[0]) == 2
    path.write_bytes(b"")
    events.append(events.make_event("x", "info", "new", now=_at(2)), path)
    fresh, invalid = tail.poll()
    assert [event["msg"] for event in fresh] == ["new"]
    assert invalid == 0


def test_tail_drops_oversized_line_and_resumes(dev: Path) -> None:
    path = events.events_path(dev)
    tail = events.Tail(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * (64 * 1024 + 10))
    assert tail.poll() == ([], 1)
    with path.open("ab") as handle:
        handle.write(b"still junk, no newline yet")
    assert tail.poll() == ([], 0)
    with path.open("ab") as handle:
        handle.write(b"\n")
    events.append(events.make_event("x", "info", "resumed", now=_at(0)), path)
    fresh, invalid = tail.poll()
    assert [event["msg"] for event in fresh] == ["resumed"]
    assert invalid == 0


def test_tail_rejects_nonpositive_maxlen(dev: Path) -> None:
    with pytest.raises(ValueError, match="maxlen"):
        events.Tail(events.events_path(dev), maxlen=0)


@_PROPERTY
@given(
    count=st.integers(min_value=0, max_value=12),
    cuts=st.lists(st.integers(min_value=1, max_value=200), max_size=30),
)
def test_tail_matches_full_read_for_any_chunking(count: int, cuts: list[int]) -> None:
    """Whatever the chunk boundaries, polling yields the same events as a full read of the finished file."""
    originals = [events.make_event("x", "info", f"m{seq}", now=_at(seq)) for seq in range(count)]
    payload = b"".join(_line(event) for event in originals)
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "events.jsonl"
        tail = events.Tail(path, maxlen=2000)
        seen: list[dict[str, object]] = []
        invalid_total = 0
        position = 0
        for size in cuts:
            piece = payload[position : position + size]
            position += len(piece)
            if piece:
                with path.open("ab") as handle:
                    handle.write(piece)
            fresh, invalid = tail.poll()
            seen.extend(fresh)
            invalid_total += invalid
        with path.open("ab") as handle:
            handle.write(payload[position:])
        fresh, invalid = tail.poll()
        seen.extend(fresh)
        invalid_total += invalid
        full, full_invalid = events.read_events([path])
    assert seen == originals
    assert full == originals
    assert invalid_total == 0
    assert full_invalid == 0
    assert list(tail.events) == originals


# --- enabled flag and emit ----------------------------------------------------------------------------------------


def test_emit_noop_when_disabled(dev: Path) -> None:
    assert events.emit("x", "info", "m") is False
    assert not dev.exists()
    assert cli.main(["enable"]) == 0
    assert events.emit("x", "info", "m") is True
    got, _ = events.read_events([events.events_path(dev)])
    assert [event["msg"] for event in got] == ["m"]
    assert cli.main(["disable"]) == 0
    assert events.emit("x", "info", "after") is False


def test_enable_and_disable_toggle_the_flag(dev: Path) -> None:
    assert not events.is_enabled(dev)
    events.enable(dev)
    assert events.is_enabled(dev)
    events.enable(dev)  # idempotent
    events.disable(dev)
    assert not events.is_enabled(dev)
    events.disable(dev)  # missing flag is not an error


def test_emit_writer_win_goes_to_the_win_file(dev: Path) -> None:
    events.enable(dev)
    assert events.emit("win-run", "done", "finished", writer="win") is True
    assert events.events_path(dev, "win").exists()
    assert not events.events_path(dev).exists()


def test_emit_never_raises_on_bad_input(dev: Path) -> None:
    events.enable(dev)
    assert events.emit("x", "bogus", "m") is False
    assert events.emit("", "info", "m") is False
    assert events.emit("x", "progress", "m", pct=200) is False
    assert not events.events_path(dev).exists()


def test_emit_returns_false_when_the_write_fails(dev: Path) -> None:
    events.enable(dev)
    events.events_path(dev).mkdir(parents=True)  # a directory where the log file should be
    assert events.emit("x", "info", "m") is False


def test_msg_flattens_c1_and_unicode_line_separators() -> None:
    raw = "a\x85b\x9fc\u2028d\u2029e\x00f\x7fg"
    assert events.make_event("x", "info", raw)["msg"] == "a b c d e f g"


# --- paths --------------------------------------------------------------------------------------------------------


def test_dev_dir_honours_the_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SAE_DEVDASH_DIR", str(tmp_path / "override"))
    assert dev_dir() == tmp_path / "override"
    assert dev_dir(tmp_path) == tmp_path / "override"


def test_dev_dir_defaults_under_the_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SAE_DEVDASH_DIR", raising=False)
    assert dev_dir() == REPO / ".sae-dev"
    assert dev_dir(tmp_path) == tmp_path / ".sae-dev"


def test_repo_points_at_the_checkout_root() -> None:
    assert (REPO / "pyproject.toml").is_file()
    assert (REPO / "tools" / "devdash" / "dd.py").is_file()


# --- property tests for the validators and the message rules ------------------------------------------------------


@_PROPERTY
@given(msg=st.text(max_size=400))
def test_msg_invariants_hold_for_any_text(msg: str) -> None:
    text = str(events.make_event("x", "info", msg, now=_T0)["msg"])
    assert len(text) <= events.MSG_MAX
    assert not any(ord(char) < 32 or ord(char) == 127 for char in text)


@_PROPERTY
@given(
    msg=st.text(max_size=300),
    pct=st.none() | st.integers(min_value=0, max_value=100),
    ref=st.none() | st.text(max_size=20),
)
def test_written_events_are_recovered_exactly(msg: str, pct: int | None, ref: str | None) -> None:
    event = events.make_event("x", "progress", msg, pct=pct, ref=ref, now=_T0)
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "events.jsonl"
        events.append(event, path)
        got, invalid = events.read_events([path])
    assert invalid == 0
    assert got == [event]


@_PROPERTY
@given(lines=st.lists(st.text(max_size=80).filter(lambda text: "\n" not in text), max_size=20))
def test_read_events_never_raises_on_arbitrary_lines(lines: list[str]) -> None:
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "events.jsonl"
        path.write_bytes("".join(line + "\n" for line in lines).encode("utf-8"))
        got, invalid = events.read_events([path])
    non_blank = sum(1 for line in lines if line.encode("utf-8").strip())
    assert invalid + len(got) == non_blank
    assert all(event["v"] == 1 for event in got)
