"""The task board file ``docs/dev/tasks.json``. Stdlib-only.

Agents update it through ``dd.py task``; the dashboard's Tasks view reads it. Game gates never live here.
Each task is an object with the keys in :data:`FIELDS`; optional ones are ``None`` when unset, and ``updated``
is an RFC3339 UTC timestamp.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["FIELDS", "STATUSES", "load_tasks", "set_task", "tasks_file"]

STATUSES: tuple[str, ...] = ("done", "in_progress", "pending", "blocked")
"""Allowed task statuses."""

FIELDS: tuple[str, ...] = ("id", "title", "note", "status", "tag", "link", "blocked_on", "updated")
"""Keys of every task object, in file order."""


def tasks_file(repo: Path) -> Path:
    """Return the task board path for a repository root."""
    return repo / "docs" / "dev" / "tasks.json"


def _check(task: object, path: Path) -> dict[str, Any]:
    """Validate one loaded entry and return it with every field present."""
    if not isinstance(task, dict):
        raise ValueError(f"{path}: task entries must be objects, got {type(task).__name__}")
    ident = task.get("id")
    if not isinstance(ident, str) or not ident:
        raise ValueError(f"{path}: task without a string id: {task!r}")
    if task.get("status") not in STATUSES:
        raise ValueError(f"{path}: task {ident!r} has bad status {task.get('status')!r}; expected one of {STATUSES}")
    if not isinstance(task.get("title"), str) or not task["title"]:
        raise ValueError(f"{path}: task {ident!r} needs a title")
    for key in ("note", "tag", "link", "blocked_on", "updated"):
        if task.get(key) is not None and not isinstance(task[key], str):
            raise ValueError(f"{path}: task {ident!r} field {key!r} must be a string or null")
    return {key: task.get(key) for key in FIELDS}


def load_tasks(path: Path) -> list[dict[str, Any]]:
    """Read the task list. Returns ``[]`` if the file is missing; raises ``ValueError`` naming the file if invalid."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of tasks")
    items = [_check(entry, path) for entry in data]
    seen: set[str] = set()
    for item in items:
        if item["id"] in seen:
            raise ValueError(f"{path}: duplicate task id {item['id']!r}")
        seen.add(item["id"])
    return items


# fcntl is POSIX-only. That is fine here: only WSL/Linux processes write the task board (the Windows side only reads
# it), so a kernel advisory lock is enough. The kernel drops the lock when its holder dies, so there is no stale state.
LOCK_WAIT_SECONDS = 5.0
LOCK_RETRY_SECONDS = 0.05


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold an exclusive ``flock`` on ``<path>.lock`` for the block (the lock file is never deleted).

    Retries every 50 ms up to ``LOCK_WAIT_SECONDS``, then raises ``TimeoutError``.
    """
    import fcntl  # POSIX-only; imported here so the module imports on Windows (spec D8)

    lock = path.with_name(path.name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{path}: task file is locked by another writer") from None
                time.sleep(LOCK_RETRY_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _write(path: Path, items: list[dict[str, Any]]) -> None:
    """Write the list sorted by id, 2-space indent, trailing newline, atomically (mkstemp then replace)."""
    items = sorted(items, key=lambda t: str(t["id"]))
    text = json.dumps(items, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def set_task(
    path: Path,
    task_id: str,
    *,
    status: str | None = None,
    title: str | None = None,
    note: str | None = None,
    tag: str | None = None,
    link: str | None = None,
    blocked_on: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create or update one task and return it.

    Creating needs ``title`` and ``status``. On update only the fields given change; for the optional fields
    (``note``, ``tag``, ``link``, ``blocked_on``) ``""`` clears to null. Moving away from ``blocked`` clears
    ``blocked_on`` unless a new one is given. ``updated`` is set to ``now`` (default: current UTC time). The
    read-modify-write holds ``<path>.lock`` (flock). Raises ``ValueError`` on a bad status, a blank title or a missing
    create field, and ``TimeoutError`` if the lock is held for more than 5 s.
    """
    if status is not None and status not in STATUSES:
        raise ValueError(f"bad status {status!r}; expected one of {STATUSES}")
    if title is not None and not title.strip():
        raise ValueError("title must not be blank")
    with _locked(path):
        items = load_tasks(path)
        existing = next((t for t in items if t["id"] == task_id), None)
        if existing is None:
            if not title:
                raise ValueError(f"creating task {task_id!r} needs a title")
            if status is None:
                raise ValueError(f"creating task {task_id!r} needs a status")
            existing = {key: None for key in FIELDS}
            existing["id"] = task_id
            items.append(existing)
        elif status is not None and status != "blocked" and existing["status"] == "blocked" and blocked_on is None:
            existing["blocked_on"] = None
        for key, value in {"status": status, "title": title}.items():
            if value is not None:
                existing[key] = value
        for key, value in {"note": note, "tag": tag, "link": link, "blocked_on": blocked_on}.items():
            if value is not None:
                existing[key] = value or None
        stamp = (now if now is not None else datetime.now(UTC)).astimezone(UTC)
        existing["updated"] = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        _write(path, items)
        return existing
