"""CURRENT lock, resolved config, re-exec (U7). Windows branch is mock-tested only (not executed in CI here)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from sae.contracts.c8_config import SaeConfig
from sae.daemon import runs


def test_acquire_writes_current(tmp_path: Path) -> None:
    runs.acquire_current(tmp_path, "r1", False)
    assert (tmp_path / "CURRENT").read_text() == f"r1 {os.getpid()}"
    runs.release_current(tmp_path, "r1")
    assert not (tmp_path / "CURRENT").exists()


def test_alive_sae_pid_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "CURRENT").write_text("r0 999999")
    monkeypatch.setattr(runs, "_is_sae_alive", lambda pid: True)
    with pytest.raises(runs.RunLockError):
        runs.acquire_current(tmp_path, "r1", False)
    with pytest.raises(runs.RunLockError):  # resume of a different run is still refused
        runs.acquire_current(tmp_path, "r1", True)


def test_dead_pid_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "CURRENT").write_text("r0 999999")
    monkeypatch.setattr(runs, "_is_sae_alive", lambda pid: False)
    runs.acquire_current(tmp_path, "r1", False)
    assert (tmp_path / "CURRENT").read_text().startswith("r1 ")


def test_malformed_current_is_stale(tmp_path: Path) -> None:
    (tmp_path / "CURRENT").write_text("garbage")
    runs.acquire_current(tmp_path, "r1", False)


def test_resume_same_run_parent_pid_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "CURRENT").write_text(f"r1 {os.getppid()}")
    monkeypatch.setattr(runs, "_is_sae_alive", lambda pid: True)
    runs.acquire_current(tmp_path, "r1", True)
    assert (tmp_path / "CURRENT").read_text() == f"r1 {os.getpid()}"


def test_non_sae_process_not_a_lock(tmp_path: Path) -> None:
    # this pytest process cmdline has no "sae"-less guarantee; use a pid that cannot exist
    assert runs._is_sae_alive(2**22 + 12345) is False


def test_release_ignores_foreign_pid(tmp_path: Path) -> None:
    (tmp_path / "CURRENT").write_text("r1 1")
    runs.release_current(tmp_path, "r1")
    assert (tmp_path / "CURRENT").exists()


def test_resolved_and_sha(tmp_path: Path) -> None:
    cfg = SaeConfig()
    p = runs.write_resolved(tmp_path, cfg)
    assert SaeConfig.model_validate_json(p.read_text()) == cfg
    assert len(runs.config_sha256(cfg)) == 64
    assert runs.config_sha256(cfg) != runs.config_sha256(SaeConfig(preset="dev"))


def test_reexec_argv_exact(tmp_path: Path) -> None:
    assert runs.reexec_argv("r1", tmp_path) == [
        sys.executable, "-m", "sae", "daemon", "--resume", "r1", "--runs-dir", str(tmp_path)]


def test_make_reexec_posix_execv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "execv", lambda exe, argv: calls.append((exe, argv)))
    runs.make_reexec("r1", tmp_path)()
    assert calls == [(sys.executable, runs.reexec_argv("r1", tmp_path))]


def test_make_reexec_windows_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Child:
        pid = 4242

    spawned: list[list[str]] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(runs.subprocess, "Popen", lambda argv: spawned.append(argv) or Child())
    runs.make_reexec("r1", tmp_path)()
    assert spawned == [runs.reexec_argv("r1", tmp_path)]
    assert (tmp_path / "CURRENT").read_text() == "r1 4242"


def test_other_current_appears_between_check_and_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "CURRENT").write_text("r0 999999")
    alive: dict[int, bool] = {999999: False, 4242: True}
    monkeypatch.setattr(runs, "_is_sae_alive", lambda pid: alive.get(pid, False))
    real_rename = os.rename

    def rename_then_rival(src: Any, dst: Any) -> None:
        real_rename(src, dst)
        (tmp_path / "CURRENT").write_text("rival 4242")  # a faster starter wins the retry window

    monkeypatch.setattr(runs.os, "rename", rename_then_rival)
    with pytest.raises(runs.RunLockError):
        runs.acquire_current(tmp_path, "r1", False)
    assert (tmp_path / "CURRENT").read_text() == "rival 4242"


def test_lost_rename_race_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "CURRENT").write_text("r0 999999")
    monkeypatch.setattr(runs, "_is_sae_alive", lambda pid: False)

    def boom(src: Any, dst: Any) -> None:
        raise FileNotFoundError("someone else renamed it")

    monkeypatch.setattr(runs.os, "rename", boom)
    with pytest.raises(runs.RunLockError):
        runs.acquire_current(tmp_path, "r1", False)


def test_young_empty_current_is_live_old_is_stale(tmp_path: Path) -> None:
    (tmp_path / "CURRENT").write_text("")
    with pytest.raises(runs.RunLockError):
        runs.acquire_current(tmp_path, "r1", False)
    old = os.stat(tmp_path / "CURRENT").st_mtime - 60
    os.utime(tmp_path / "CURRENT", (old, old))
    runs.acquire_current(tmp_path, "r1", False)
    assert (tmp_path / "CURRENT").read_text().startswith("r1 ")


def test_no_hardlink_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def nolink(src: Any, dst: Any) -> None:
        raise NotImplementedError

    monkeypatch.setattr(runs.os, "link", nolink)
    runs.acquire_current(tmp_path, "r1", False)
    assert (tmp_path / "CURRENT").read_text() == f"r1 {os.getpid()}"
    assert [p.name for p in tmp_path.iterdir()] == ["CURRENT"]
