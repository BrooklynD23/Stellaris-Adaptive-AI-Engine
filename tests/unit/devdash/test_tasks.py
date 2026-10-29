"""Tests for the task board file (``devdash.tasks``) and the ``dd.py task`` command."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from devdash import REPO, cli, events, tasks

_GATE = re.compile(r"^(X\d-G\d+|M0-\d+|R-\d)$")
_T0 = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 10, 28, 13, 0, 0, tzinfo=UTC)


def test_create_requires_title_and_status(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    with pytest.raises(ValueError, match="title"):
        tasks.set_task(path, "a", status="pending")
    with pytest.raises(ValueError, match="status"):
        tasks.set_task(path, "a", title="A")
    assert not path.exists()
    made = tasks.set_task(path, "a", title="A", status="pending", now=_T0)
    assert made["id"] == "a"
    assert made["updated"] == "2026-10-28T12:00:00Z"
    assert made["note"] is None
    assert made["blocked_on"] is None


def test_update_keeps_other_fields(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    tasks.set_task(path, "a", title="A", status="blocked", note="n", tag="t", link="l", blocked_on="b", now=_T0)
    got = tasks.set_task(path, "a", status="done", now=_T1)
    assert got["title"] == "A"
    assert got["note"] == "n"
    assert got["tag"] == "t"
    assert got["link"] == "l"
    assert got["blocked_on"] is None  # leaving "blocked" clears blocked_on
    assert got["status"] == "done"
    assert got["updated"] == "2026-10-28T13:00:00Z"
    assert tasks.load_tasks(path) == [got]


def test_bad_status_rejected(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    with pytest.raises(ValueError, match="status"):
        tasks.set_task(path, "a", title="A", status="nope")
    path.write_text(json.dumps([{"id": "a", "title": "A", "status": "bogus"}]), encoding="utf-8")
    with pytest.raises(ValueError, match=r"tasks\.json"):
        tasks.load_tasks(path)


def test_bad_json_names_file(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match=r"tasks\.json"):
        tasks.load_tasks(path)
    assert tasks.load_tasks(tmp_path / "missing.json") == []


def test_file_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    tasks.set_task(path, "b", title="B", status="pending", now=_T0)
    tasks.set_task(path, "a", title="A", status="done", now=_T0)
    first = path.read_bytes()
    tasks.set_task(path, "a", title="A", status="done", now=_T0)
    assert path.read_bytes() == first
    tasks.set_task(path, "a", title="A", status="done", now=_T1)
    strip = re.compile(rb'"updated": "[^"]*"')
    assert strip.sub(b"", path.read_bytes()) == strip.sub(b"", first)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert text.startswith('[\n  {\n    "id": "a"')
    assert [t["id"] for t in tasks.load_tasks(path)] == ["a", "b"]
    assert not list(tmp_path.glob("*.tmp"))


def test_seed_file_valid() -> None:
    path = tasks.tasks_file(REPO)
    assert path == REPO / "docs" / "dev" / "tasks.json"
    got = tasks.load_tasks(path)
    ids = [t["id"] for t in got]
    assert len(ids) == len(set(ids))
    assert not [i for i in ids if _GATE.match(i)]
    assert {"sae-mvp", "devdash", "v1-calibration", "v5-fog-real-saves"} <= set(ids)


def test_cli_task_creates_and_emits(dev: Path, tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    assert cli.main(["enable"]) == 0
    code = cli.main(["task", "t1", "--file", str(path), "--status", "pending", "--title", "T one", "--tag", "x"])
    assert code == 0
    assert cli.main(["task", "t1", "--file", str(path), "--status", "done", "--link", "http://l"]) == 0
    got = tasks.load_tasks(path)
    assert got[0]["status"] == "done"
    assert got[0]["title"] == "T one"
    assert got[0]["link"] == "http://l"
    logged, _ = events.read_events([events.events_path(dev)])
    assert [e["msg"] for e in logged] == ["task t1 → pending", "task t1 → done"]
    assert logged[0]["ref"] == "t1"
    assert logged[0]["kind"] == "info"


def test_cli_task_writes_when_disabled(dev: Path, tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    assert cli.main(["task", "t1", "--file", str(path), "--status", "pending", "--title", "T"]) == 0
    assert len(tasks.load_tasks(path)) == 1
    assert not events.events_path(dev).exists()


def test_cli_task_errors(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    assert cli.main(["task", "t1", "--file", str(path), "--status", "pending"]) == 1
    assert cli.main(["task", "t1", "--file", str(path), "--status", "bad", "--title", "T"]) == 2


def test_blank_title_rejected(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    tasks.set_task(path, "a", title="A", status="pending", now=_T0)
    with pytest.raises(ValueError, match="title"):
        tasks.set_task(path, "a", title="  ")
    with pytest.raises(ValueError, match="title"):
        tasks.set_task(path, "b", title="", status="pending")
    assert tasks.load_tasks(path)[0]["title"] == "A"
    assert cli.main(["task", "a", "--file", str(path), "--title", ""]) == 2


def test_empty_string_clears_optional_fields(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    tasks.set_task(path, "a", title="A", status="blocked", note="n", blocked_on="b", now=_T0)
    got = tasks.set_task(path, "a", note="", blocked_on="new", now=_T1)
    assert got["note"] is None
    assert got["blocked_on"] == "new"
    got = tasks.set_task(path, "a", status="pending", blocked_on="still", now=_T1)
    assert got["blocked_on"] == "still"


def test_load_rejects_duplicates_and_non_strings(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    row = {"id": "a", "title": "A", "status": "done"}
    path.write_text(json.dumps([row, row]), encoding="utf-8")
    with pytest.raises(ValueError, match=r"tasks\.json.*'a'"):
        tasks.load_tasks(path)
    path.write_text(json.dumps([{**row, "note": 5}]), encoding="utf-8")
    with pytest.raises(ValueError, match=r"tasks\.json.*'a'.*note"):
        tasks.load_tasks(path)


_WRITER = (
    "import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from devdash import tasks;"
    "[tasks.set_task(Path(sys.argv[2]), f'{sys.argv[3]}-{n}', title='T', status='pending') for n in range(5)]"
)


def _run_writers(path: Path) -> None:
    tool = str(REPO / "tools" / "devdash")
    procs = [subprocess.Popen([sys.executable, "-c", _WRITER, tool, str(path), f"p{i}"]) for i in range(4)]
    assert [p.wait(timeout=60) for p in procs] == [0, 0, 0, 0]
    assert len(tasks.load_tasks(path)) == 20


def test_concurrent_writers_all_survive(tmp_path: Path) -> None:
    _run_writers(tmp_path / "tasks.json")


def test_concurrent_writers_all_survive_in_repo_tree() -> None:
    """Same as above, but under the repo checkout (a Windows drive mounted into WSL when run from /mnt)."""
    work = Path(tempfile.mkdtemp(prefix=".sae-dev-test-", dir=REPO))
    try:
        _run_writers(work / "tasks.json")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_lock_timeout_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "tasks.json"
    fd = os.open(tmp_path / "tasks.json.lock", os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        monkeypatch.setattr(tasks, "LOCK_WAIT_SECONDS", 0.2)
        with pytest.raises(TimeoutError, match="locked by another writer"):
            tasks.set_task(path, "a", title="A", status="pending")
        assert cli.main(["task", "a", "--file", str(path), "--title", "A", "--status", "pending"]) == 1
    finally:
        os.close(fd)
    assert not path.exists()


def test_lock_released_when_holder_dies(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    code = (
        "import fcntl, os, sys, time;"
        "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR); fcntl.flock(fd, fcntl.LOCK_EX);"
        "print('ready', flush=True); time.sleep(60)"
    )
    holder = subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "tasks.json.lock")], stdout=subprocess.PIPE)
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == b"ready"
        holder.kill()
        holder.wait(timeout=10)
    finally:
        holder.kill()
        if holder.stdout is not None:
            holder.stdout.close()
    tasks.set_task(path, "a", title="A", status="pending")
    assert [t["id"] for t in tasks.load_tasks(path)] == ["a"]
