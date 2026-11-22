"""bench parse/spy pass --background to sae-parse."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from sae.bench.cli import main

_OUT = json.dumps({"cost": {"wall_s": 1.0, "cpu_s": 1.0, "rss_peak_bytes": 1}, "meta": {"date": "2200.01.01"}})


def _run(cmds: list[list[str]]) -> object:
    def run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        cmds.append(list(cmd))
        return subprocess.CompletedProcess(list(cmd), 0, _OUT, "")

    return run


def test_parse_passes_background(tmp_path: Path) -> None:
    sav = tmp_path / "a.sav"
    sav.write_bytes(b"x")
    cmds: list[list[str]] = []
    rc = main("bench", ["parse", "--parser-exe", "exe", str(sav)], runner=_run(cmds))  # type: ignore[arg-type]
    assert rc == 0
    assert cmds and all("--background" in c for c in cmds)


def test_spy_passes_background(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "a.sav").write_bytes(b"x")
    cmds: list[list[str]] = []
    main("bench", ["spy", "--parser-exe", "exe", str(tmp_path)], runner=_run(cmds))  # type: ignore[arg-type]
    assert cmds and all("--background" in c for c in cmds)
