"""FileInbox: parsing, at-most-once, crash recovery, end-to-end with a fake daemon."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sae.contracts.c9_control import MAX_COMMAND_BYTES, ControlResult
from sae.control import FileInbox, open_inbox
from sae.daemon.ports import ControlInbox

CID = "c-20261019T031800Z-a1b2c3"
ISSUED = datetime(2026, 10, 19, 3, 18, tzinfo=UTC)
START = ISSUED - timedelta(hours=1)  # daemon session started before the fixture commands


def body(cid: str = CID, cmd: str = "pause") -> str:
    return (
        f'{{"v":1,"command_id":"{cid}","issued_at":"2026-10-19T03:18:00Z","operator":"me",'
        f'"command":{{"cmd":"{cmd}"}}}}'
    )


def ok(cid: str) -> ControlResult:
    return ControlResult(command_id=cid, accepted=True, applied_at=None, status=None)


@pytest.fixture
def box(tmp_path: Path) -> FileInbox:
    open_inbox(tmp_path)
    return FileInbox(tmp_path, started_at=START, now=lambda: ISSUED + timedelta(seconds=1))


def put(tmp_path: Path, name: str, text: str | bytes) -> Path:
    p = tmp_path / "control" / "inbox" / name
    if isinstance(text, bytes):
        p.write_bytes(text)
    else:
        p.write_text(text, encoding="utf-8")
    return p


def test_satisfies_port(box: FileInbox) -> None:
    port: ControlInbox = box
    assert port.poll() == []


def test_valid_then_at_most_once_then_complete(tmp_path: Path, box: FileInbox) -> None:
    put(tmp_path, f"{CID}.json", body())
    (item,) = box.poll()
    assert item.command is not None and item.command.command_id == CID and item.handle == CID
    assert box.poll() == []
    box.complete(item, ok(CID))
    done = tmp_path / "control" / "done"
    assert (done / f"{CID}.result.json").exists() and (done / f"{CID}.json").exists()
    assert not (tmp_path / "control" / "inbox" / f"{CID}.json").exists()


def test_ordering_and_tmp_ignored(tmp_path: Path, box: FileInbox) -> None:
    b = "c-20261019T031900Z-00ff00"
    put(tmp_path, f"{b}.json", body(b))
    put(tmp_path, f"{CID}.json", body())
    put(tmp_path, f".{CID}.json.tmp", "garbage")
    assert [i.handle for i in box.poll()] == [CID, b]


@pytest.mark.parametrize(
    ("name", "text", "error"),
    [
        (f"{CID}.json", "{not json", "invalid"),
        (f"{CID}.json", '{"v":1}', "invalid"),
        (f"{CID}.json", body(cmd="reboot"), "unknown_cmd"),
        (f"{CID}.json", body("c-20261019T031800Z-ffffff"), "invalid"),
        (f"{CID}.json", b"x" * (MAX_COMMAND_BYTES + 1), "too_large"),
        (f"{CID}.json", "[]", "invalid"),
    ],
)
def test_invalid_files(tmp_path: Path, box: FileInbox, name: str, text: str | bytes, error: str) -> None:
    put(tmp_path, name, text)
    (item,) = box.poll()
    assert item.command is None and item.error is not None
    assert item.error.error == error and not item.error.accepted
    assert item.error.applied_at is None and item.error.status is None
    assert item.error.detail is not None and len(item.error.detail) <= 2000
    echoed = error == "unknown_cmd" or "ffffff" in str(text)
    assert item.error.command_id == (CID if echoed else "unknown")


def test_symlink_and_directory_invalid(tmp_path: Path, box: FileInbox) -> None:
    target = tmp_path / "target.json"
    target.write_text(body(), encoding="utf-8")
    try:
        os.symlink(target, tmp_path / "control" / "inbox" / f"{CID}.json")
    except OSError:
        pytest.skip("symlinks unavailable")
    (tmp_path / "control" / "inbox" / "c-20261019T031900Z-00ff00.json").mkdir()
    items = box.poll()
    assert len(items) == 2 and all(i.error is not None and i.error.error == "invalid" for i in items)


def test_crash_after_result_not_returned(tmp_path: Path, box: FileInbox) -> None:
    done = tmp_path / "control" / "done"
    (done / f"{CID}.result.json").write_text(ok(CID).model_dump_json(), encoding="utf-8")
    put(tmp_path, f"{CID}.json", body())
    assert box.poll() == []
    assert (done / f"{CID}.json").exists()
    assert not (tmp_path / "control" / "inbox" / f"{CID}.json").exists()


def test_complete_vanished_file(tmp_path: Path, box: FileInbox) -> None:
    p = put(tmp_path, f"{CID}.json", body())
    (item,) = box.poll()
    p.unlink()
    box.complete(item, ok(CID))
    assert (tmp_path / "control" / "done" / f"{CID}.result.json").exists()


def test_complete_retries_permission_error(
    tmp_path: Path, box: FileInbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(tmp_path, f"{CID}.json", body())
    (item,) = box.poll()
    real = os.replace
    calls = {"n": 0}

    def flaky(src: str | Path, dst: str | Path) -> None:
        if str(src).endswith(f"{CID}.json") and "inbox" in str(src):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("busy")
        real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    box.complete(item, ok(CID))
    assert calls["n"] == 3 and (tmp_path / "control" / "done" / f"{CID}.json").exists()


def test_complete_never_raises(tmp_path: Path, box: FileInbox, monkeypatch: pytest.MonkeyPatch) -> None:
    put(tmp_path, f"{CID}.json", body())
    (item,) = box.poll()

    def boom(src: str | Path, dst: str | Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(os, "replace", boom)
    box.complete(item, ok(CID))  # logged, not raised
    assert box.poll() == []


def test_poll_survives_exists_error(tmp_path: Path, box: FileInbox, monkeypatch: pytest.MonkeyPatch) -> None:
    put(tmp_path, f"{CID}.json", body())
    real = Path.exists

    def bad(self: Path) -> bool:
        if self.name.endswith(".result.json"):
            raise PermissionError("denied")
        return real(self)

    monkeypatch.setattr(Path, "exists", bad)
    assert box.poll() == []
    monkeypatch.undo()
    assert len(box.poll()) == 1


def test_result_replace_retries_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    open_inbox(tmp_path)
    box = FileInbox(tmp_path, sleep=sleeps.append, started_at=START, now=lambda: ISSUED)
    put(tmp_path, f"{CID}.json", body())
    (item,) = box.poll()
    real = os.replace
    calls = {"n": 0}

    def flaky(src: str | Path, dst: str | Path) -> None:
        if str(dst).endswith(".result.json"):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError("busy")
        real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    box.complete(item, ok(CID))
    assert calls["n"] == 2 and sleeps == [0.05]
    assert (tmp_path / "control" / "done" / f"{CID}.result.json").exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_fifo_does_not_block(tmp_path: Path, box: FileInbox) -> None:
    os.mkfifo(tmp_path / "control" / "inbox" / f"{CID}.json")
    (item,) = box.poll()
    assert item.error is not None and item.error.error == "invalid"


def test_result_tmp_unlinked_when_replace_gives_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    open_inbox(tmp_path)
    box = FileInbox(tmp_path, sleep=lambda s: None, started_at=START, now=lambda: ISSUED)
    put(tmp_path, f"{CID}.json", body())
    (item,) = box.poll()
    real = os.replace

    def deny(src: str | Path, dst: str | Path) -> None:
        if str(dst).endswith(".result.json"):
            raise PermissionError("busy")
        real(src, dst)

    monkeypatch.setattr(os, "replace", deny)
    box.complete(item, ok(CID))
    assert not list((tmp_path / "control" / "done").glob(".*.tmp"))


def _expired_detail(tmp_path: Path, started_at: datetime, now: datetime) -> str | None:
    open_inbox(tmp_path)
    put(tmp_path, f"{CID}.json", body())
    (item,) = FileInbox(tmp_path, started_at=started_at, now=lambda: now).poll()
    assert item.command is not None and item.command.command_id == CID  # parsed command kept (C5 control row)
    if item.error is None:
        return None
    assert item.error.error == "expired" and not item.error.accepted and item.error.command_id == CID
    return item.error.detail


def test_fresh_command_applied(tmp_path: Path) -> None:
    assert _expired_detail(tmp_path, START, ISSUED + timedelta(seconds=30)) is None


def test_stale_file_at_startup_expired(tmp_path: Path) -> None:
    detail = _expired_detail(tmp_path, ISSUED + timedelta(seconds=1), ISSUED + timedelta(seconds=2))
    assert detail is not None and "before this daemon session" in detail


def test_61_s_old_expired(tmp_path: Path) -> None:
    detail = _expired_detail(tmp_path, START, ISSUED + timedelta(seconds=61))
    assert detail is not None and "ago" in detail


def test_future_issued_at_expired(tmp_path: Path) -> None:
    detail = _expired_detail(tmp_path, START, ISSUED - timedelta(seconds=6))
    assert detail is not None and "future" in detail


def test_open_inbox_started_at_passthrough(tmp_path: Path) -> None:
    put_dir = open_inbox(tmp_path, started_at=ISSUED + timedelta(seconds=1))
    put(tmp_path, f"{CID}.json", body())
    (item,) = put_dir.poll()
    assert item.error is not None and item.error.error == "expired"
