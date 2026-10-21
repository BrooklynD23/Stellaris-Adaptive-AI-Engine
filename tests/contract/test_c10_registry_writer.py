"""C10 section 5 / proposal 025 section 2: ``append_registry_row`` (TTY guard, lock, gapless seq, LF repair)."""

from __future__ import annotations

import io
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from sae.contracts.c10_profile import RegistryError, RegistryRow, append_registry_row, fold_registry

NOW = datetime(2026, 1, 1, tzinfo=UTC)
H = "1" * 64
H3 = "3" * 64
BUILTIN = "0" * 64


class _Tty:
    def __init__(self, tty: bool) -> None:
        self.closed = False
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _row(action: str, by: str, **kw: Any) -> RegistryRow:
    return RegistryRow(seq=99, at=NOW, action=action, sha256=H, by=by, **kw)  # type: ignore[arg-type]


def _tty(monkeypatch: pytest.MonkeyPatch, stdin: object, stdout: object) -> None:
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)


def _closed() -> io.StringIO:
    s = io.StringIO()
    s.close()
    return s


@pytest.mark.parametrize(
    ("stdin", "stdout"),
    [(_Tty(False), _Tty(False)), (_Tty(True), _Tty(False)), (_Tty(False), _Tty(True)), (None, _Tty(True)),
     (_closed(), _Tty(True))],
)  # fmt: skip
def test_human_row_without_tty_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdin: object, stdout: object
) -> None:
    _tty(monkeypatch, stdin, stdout)
    reg = tmp_path / "policies" / "registry.jsonl"
    with pytest.raises(RegistryError, match="TTY"):
        append_registry_row(reg, _row("propose", "human:alice"))
    assert not reg.exists()
    assert not reg.with_name("registry.jsonl.lock").exists()


def test_human_row_with_tty_appended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _tty(monkeypatch, _Tty(True), _Tty(True))
    reg = tmp_path / "registry.jsonl"
    out = append_registry_row(reg, _row("propose", "human:alice"))
    assert out.seq == 0
    assert reg.read_bytes().count(b"\n") == 1


def test_tuner_gate_rows_need_no_tty_and_seq_is_gapless(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _tty(monkeypatch, None, _Tty(False))
    reg = tmp_path / "registry.jsonl"
    rows = [
        append_registry_row(reg, _row("propose", "tuner")),
        append_registry_row(reg, _row("stage1_fail", "gate", evidence_sha256=H3)),
        append_registry_row(reg, _row("propose", "tuner")),
    ]
    assert [r.seq for r in rows] == [0, 1, 2]
    lines = reg.read_bytes().splitlines()
    assert b'"seq":0' in lines[0] and b"null" not in lines[0]  # compact, exclude_none
    parsed = [RegistryRow.model_validate_json(x) for x in lines]
    assert parsed == rows
    fold_registry(parsed, BUILTIN)  # gapless: no RegistryError
    assert not reg.with_name("registry.jsonl.lock").exists()


def test_existing_lock_refused_and_untouched(tmp_path: Path) -> None:
    reg = tmp_path / "registry.jsonl"
    lock = tmp_path / "registry.jsonl.lock"
    lock.write_bytes(b"pid 1")
    with pytest.raises(RegistryError, match=r"^registry locked: registry\.jsonl\.lock$"):
        append_registry_row(reg, _row("propose", "tuner"))
    assert lock.read_bytes() == b"pid 1"
    assert not reg.exists()


def test_lock_removed_after_write_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", boom)
    reg = tmp_path / "registry.jsonl"
    with pytest.raises(OSError, match="disk full"):
        append_registry_row(reg, _row("propose", "tuner"))
    assert not reg.with_name("registry.jsonl.lock").exists()


def test_partial_last_line_repaired_gapless(tmp_path: Path) -> None:
    reg = tmp_path / "registry.jsonl"
    append_registry_row(reg, _row("propose", "tuner"))
    with reg.open("ab") as fh:
        fh.write(b'{"v":1,"seq":1,"at":"2026-01')  # crash mid-write
    out = append_registry_row(reg, _row("propose", "tuner"))
    assert out.seq == 1
    lines = reg.read_bytes().split(b"\n")
    assert lines[-1] == b""  # file ends with LF
    valid = []
    for x in lines[:-1]:
        try:
            valid.append(RegistryRow.model_validate_json(x))
        except ValueError:
            continue
    assert [r.seq for r in valid] == [0, 1]
    fold_registry(valid, BUILTIN)


def test_complete_row_missing_lf_counts(tmp_path: Path) -> None:
    """A crash after the JSON but before the LF: the repaired line is valid, so the next seq follows it."""
    reg = tmp_path / "registry.jsonl"
    first = _row("propose", "tuner").model_copy(update={"seq": 0})
    reg.write_bytes(first.model_dump_json(exclude_none=True).encode())
    out = append_registry_row(reg, _row("propose", "tuner"))
    assert out.seq == 1
    rows = [RegistryRow.model_validate_json(x) for x in reg.read_bytes().splitlines()]
    fold_registry(rows, BUILTIN)
