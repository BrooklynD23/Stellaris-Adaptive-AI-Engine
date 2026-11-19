"""Review B2c: a short real ``sae synth`` run counts fsyncs per file, and the bus bytes match durable mode."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from sae.adapters import cli
from sae.adapters.paths import resolve_paths
from sae.bus import writer
from sae.contracts.c5_bus import BUS_FILE, GOD_FILE
from sae.contracts.c6_adapter import AdapterMode
from sae.contracts.c8_config import PathsConfig
from sae.daemon import loop, statefile

YEARS = 1  # 12 decided months: enough month changes to tell gated from ungated, fast enough for unit
SEED = 7
_VOLATILE = ("t_wall", "t_mono_ns", "run_id", "seq")  # seq: shifted by timing-driven health lines


def _exe() -> Path:
    exe = resolve_paths(PathsConfig()).parser_exe
    if exe is None or not exe.is_file():
        pytest.skip("build parser/ (sae-parse) for the in-process synth run")
    return exe


def _synth(runs: Path, monkeypatch: pytest.MonkeyPatch, *, durable: bool) -> tuple[Path, list[int], float]:
    """One in-process synth run; returns its run dir, the inode of every fsynced fd, and the wall time."""
    if durable:  # pretend SYNTH is LIVE for both the writer and state.json
        monkeypatch.setattr(writer, "_DURABLE_MODES", frozenset({AdapterMode.SYNTH}))
        real_save = statefile.save_state
        monkeypatch.setattr(loop, "save_state", lambda p, s, *, fsync=True: real_save(p, s, fsync=True))
    inodes: list[int] = []
    real_fsync = os.fsync

    def counting(fd: int) -> None:
        inodes.append(os.fstat(fd).st_ino)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", counting)
    before = set(runs.iterdir()) if runs.is_dir() else set()
    t0 = time.perf_counter()
    code = cli.main("synth", ["--runs-dir", str(runs), "--archetype", "turtle", "--years", str(YEARS),
                              "--seed", str(SEED), "--set", f"paths.parser_exe={json.dumps(str(_exe()))}"])
    wall = time.perf_counter() - t0
    monkeypatch.undo()
    assert code == 0
    (run_dir,) = [p for p in set(runs.iterdir()) - before if p.is_dir()]
    return run_dir, inodes, wall


def _count(inodes: list[int], path: Path) -> int:
    ino = path.stat().st_ino
    return sum(1 for i in inodes if i == ino)


def _normalised(path: Path) -> list[dict[str, Any]]:
    """Events minus run-derived values; ``health`` lines carry live RSS/CPU samples, so dropped.

    The run id also appears inside paths (``receipt.path``) and seeds ``save_folder_key``.
    """
    out = []
    for raw in path.read_text(encoding="utf-8").replace(path.parent.name, "RUN").splitlines():
        ev = json.loads(raw)
        if ev["type"] == "health":
            continue
        for k in _VOLATILE:
            ev.pop(k, None)
        ev["payload"].pop("save_folder_key", None)
        out.append(ev)
    return out


def test_synth_fsyncs_only_on_close_and_bytes_match_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = tmp_path / "runs"
    fast_dir, fast, fast_s = _synth(runs, monkeypatch, durable=False)
    slow_dir, slow, slow_s = _synth(runs, monkeypatch, durable=True)
    # non-durable: one fsync per bus file (on close), none for state.json
    assert _count(fast, fast_dir / BUS_FILE) == 1 and _count(fast, fast_dir / GOD_FILE) == 1
    assert _count(fast, fast_dir / "state.json") == 0
    # durable (today's behaviour): one per month change plus close, and state.json every decided month
    assert _count(slow, slow_dir / BUS_FILE) > 1 and _count(slow, slow_dir / GOD_FILE) > 1
    assert _count(slow, slow_dir / "state.json") > 0
    for name in (BUS_FILE, GOD_FILE):
        assert _normalised(fast_dir / name) == _normalised(slow_dir / name)
    assert (fast_dir / "state.json").read_bytes() == (slow_dir / "state.json").read_bytes()
    with capsys.disabled():  # reported, not gated (depends on WSL load)
        print(f"\nfsync synth={len(fast)} durable={len(slow)}; wall synth={fast_s:.2f}s durable={slow_s:.2f}s")


def test_durable_and_lockstep_modes_partition_adapter_mode() -> None:
    """Review A1 NB1: the writer's durable set and the loop's lockstep set must not drift when a mode is added."""
    assert writer._DURABLE_MODES.isdisjoint(loop.LOCKSTEP_MODES)
    assert writer._DURABLE_MODES | loop.LOCKSTEP_MODES == set(AdapterMode)
