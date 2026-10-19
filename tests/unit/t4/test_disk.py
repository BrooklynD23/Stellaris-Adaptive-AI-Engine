"""Wave 3 N4: runs-dir size warning (start + once per game month), never deletes."""

from __future__ import annotations

from pathlib import Path

import pytest

from sae.contracts.c5_bus import HealthKind, HealthPayload
from sae.daemon import disk

from .test_loop import EchoAdapter, go


def test_dir_bytes_counts_nested_files(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "x.bin").write_bytes(b"x" * 10)
    (tmp_path / "a" / "b" / "y.bin").write_bytes(b"y" * 5)
    assert disk.dir_bytes(tmp_path) == 15
    assert disk.dir_bytes(tmp_path / "missing") == 0


def test_warns_at_start_and_monthly_and_deletes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disk, "RUNS_DIR_WARN_BYTES", 100)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    big = tmp_path / "old-run" / "saves" / "autosave_1.sav"
    big.parent.mkdir(parents=True)
    big.write_bytes(b"s" * 500)
    calls: list[Path] = []
    real = disk.dir_bytes
    monkeypatch.setattr(disk, "dir_bytes", lambda p: calls.append(p) or real(p))
    months = 6
    code, sink, _, _ = go(EchoAdapter(months, delay=0.0), run_dir)
    assert code == 0 and big.exists()
    warn = [d.payload for d in sink.drafts if d.type == "health" and isinstance(d.payload, HealthPayload)
            and d.payload.kind is HealthKind.BUDGET_BREACH and "runs_dir_bytes" in (d.payload.detail or "")]
    assert warn and warn[0].component == "daemon" and warn[0].metrics["runs_dir_bytes"] >= 500
    assert len(calls) <= months + 1  # start + at most once per game month
    assert all(p == tmp_path for p in calls)


def test_no_warning_below_threshold(tmp_path: Path) -> None:
    code, sink, _, _ = go(EchoAdapter(3, delay=0.0), tmp_path / "run")
    assert code == 0
    assert not any(d.type == "health" and "runs_dir_bytes" in (getattr(d.payload, "detail", None) or "")
                   for d in sink.drafts)
