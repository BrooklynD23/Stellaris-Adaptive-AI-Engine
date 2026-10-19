"""``sae ctl`` end to end against a fake daemon built on FileInbox."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sae.contracts.c9_control import ControlResult
from sae.control import FileInbox, open_inbox
from sae.control.cli import main

RUN = "20261019-031500-synth-0a1b"
T0 = datetime(2026, 10, 19, 3, 18, tzinfo=UTC)


class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def mono(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def run_ctl(base: Path, args: list[str], ft: FakeTime, hook: Callable[[], None] | None = None) -> int:
    def sleep(s: float) -> None:
        ft.sleep(s)
        if hook:
            hook()

    return main("ctl", ["--runs-dir", str(base), *args], mono=ft.mono, sleep=sleep, now=lambda: T0)


@pytest.fixture
def base(tmp_path: Path) -> Path:
    open_inbox(tmp_path / RUN)
    (tmp_path / "CURRENT").write_text(f"{RUN} {os.getpid()}", encoding="utf-8")
    return tmp_path


def answer(box: FileInbox, accept: bool) -> Callable[[], None]:
    def hook() -> None:
        for item in box.poll():
            cid = item.command.command_id if item.command else "unknown"
            box.complete(
                item,
                ControlResult(
                    command_id=cid, accepted=accept, error=None if accept else "not_running",
                    detail=None if accept else "nope", applied_at=None, status=None,
                ),
            )

    return hook


def test_accept(base: Path, capsys: pytest.CaptureFixture[str]) -> None:
    box = FileInbox(base / RUN)
    assert run_ctl(base, ["pause", "--reason", "lunch"], FakeTime(), answer(box, True)) == 0
    assert "accepted: true" in capsys.readouterr().out
    done = list((base / RUN / "control" / "done").glob("c-*.json"))
    assert len(done) == 2 and not list((base / RUN / "control" / "inbox").iterdir())


def test_reject(base: Path, capsys: pytest.CaptureFixture[str]) -> None:
    box = FileInbox(base / RUN)
    assert run_ctl(base, ["force-strategy", "baseline", "--months", "6"], FakeTime(), answer(box, False)) == 1
    assert "error: not_running" in capsys.readouterr().out


def test_clear_force_written(base: Path) -> None:
    box = FileInbox(base / RUN)
    seen: list[str] = []

    def hook() -> None:
        for item in box.poll():
            assert item.command is not None
            seen.append(item.command.model_dump_json())
            box.complete(
                item, ControlResult(command_id=item.handle, accepted=True, applied_at=None, status=None)
            )

    assert run_ctl(base, ["clear-force"], FakeTime(), hook) == 0
    assert len(seen) == 1 and '"strategy":null' in seen[0]


def test_timeout(base: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ft = FakeTime()
    assert run_ctl(base, ["status"], ft) == 1
    assert "no result; is the daemon running?" in capsys.readouterr().err
    assert ft.t >= 10.0 and set(ft.sleeps) == {0.5}


def test_real_thread_daemon(base: Path) -> None:
    box = FileInbox(base / RUN)
    hook = answer(box, True)
    stop = threading.Event()

    def daemon() -> None:
        while not stop.is_set():
            hook()
            stop.wait(0.01)

    th = threading.Thread(target=daemon)
    th.start()
    try:
        assert main("ctl", ["--runs-dir", str(base), "resume"], now=lambda: T0) == 0
    finally:
        stop.set()
        th.join(2)


@pytest.mark.parametrize("bad", ["../../etc", "x", "20261019-031500-synth-0a1b/../x"])
def test_bad_run_rejected(base: Path, bad: str) -> None:
    assert run_ctl(base, ["--run", bad, "pause"], FakeTime()) == 1


@pytest.mark.parametrize("content", [None, "", "../../etc 5", "garbage"])
def test_current_missing_or_malformed(tmp_path: Path, content: str | None) -> None:
    if content is not None:
        (tmp_path / "CURRENT").write_text(content, encoding="utf-8")
    assert run_ctl(tmp_path, ["pause"], FakeTime()) == 1


def test_missing_run_dir(tmp_path: Path) -> None:
    assert run_ctl(tmp_path, ["--run", RUN, "pause"], FakeTime()) == 1


def test_usage_and_config_errors(base: Path) -> None:
    assert main("ctl", ["--runs-dir", str(base), "force-strategy", "bogus"]) == 2
    assert main("ctl", []) == 2
    assert main("ctl", ["--runs-dir", str(base), "--set", "nokey", "pause"]) == 3
    assert main("ctl", ["--runs-dir", str(base), "force-strategy", "baseline", "--months", "0"]) == 2


def test_help_exits_zero() -> None:
    assert main("ctl", ["--help"]) == 0


def test_command_id_prefix_collision_regenerates(base: Path) -> None:
    inbox = base / RUN / "control" / "inbox"
    (inbox / ".c-20261019T031800Z-aaaaaa.json.tmp").write_text("x", encoding="utf-8")
    times = iter([T0, datetime(2026, 10, 19, 3, 18, 1, tzinfo=UTC)])
    ft = FakeTime()
    box = FileInbox(base / RUN)
    ids: list[str] = []

    def sleep(s: float) -> None:
        ft.sleep(s)
        for item in box.poll():
            assert item.command is not None
            ids.append(item.command.command_id)
            box.complete(item, ControlResult(command_id=item.handle, accepted=True, applied_at=None, status=None))

    code = main("ctl", ["--runs-dir", str(base), "status"], mono=ft.mono, sleep=sleep, now=lambda: next(times))
    assert code == 0 and ft.sleeps[0] == 1.0
    assert ids[0].startswith("c-20261019T031801Z-")


def _inbox_files(base: Path) -> list[str]:
    return sorted(p.name for p in (base / RUN / "control" / "inbox").iterdir())


def test_dead_pid_writes_nothing(base: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (base / "CURRENT").write_text(f"{RUN} 999999999", encoding="utf-8")
    assert run_ctl(base, ["pause"], FakeTime()) == 1
    assert "no running daemon" in capsys.readouterr().err
    assert _inbox_files(base) == []


def test_current_names_other_run_writes_nothing(base: Path) -> None:
    (base / "CURRENT").write_text(f"20261019-031500-live-ffff {os.getpid()}", encoding="utf-8")
    assert run_ctl(base, ["--run", RUN, "pause"], FakeTime()) == 1
    assert _inbox_files(base) == []


def test_empty_current_writes_nothing(base: Path) -> None:
    (base / "CURRENT").write_text("", encoding="utf-8")
    assert run_ctl(base, ["--run", RUN, "pause"], FakeTime()) == 1
    assert _inbox_files(base) == []
