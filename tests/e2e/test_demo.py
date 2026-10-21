"""Real one-year ``sae bench demo`` against the built parser."""

from __future__ import annotations

import importlib.util
import subprocess
from collections.abc import Sequence
from pathlib import Path

from sae.bench.cli import main
from sae.bench.demo import default_runner


def test_demo_real_one_year(tmp_path: Path, parser_exe: Path) -> None:
    """Four archetype runs; only the first telemetry call is real, and its .rrd is non-empty when rerun exists."""
    calls: list[str] = []

    def runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if cmd[3] == "telemetry":
            calls.append("t")
            if len(calls) > 1:
                return subprocess.CompletedProcess(list(cmd), 0, "", "")
        return default_runner(cmd)

    assert main("bench", ["demo", "--years", "1", "--out", str(tmp_path), "--parser-exe", str(parser_exe)],
                runner=runner) == 0
    assert len(calls) == 4
    if importlib.util.find_spec("rerun") is not None:
        rrds = sorted(tmp_path.glob("*.rrd"))
        assert rrds and rrds[0].is_file() and rrds[0].stat().st_size > 0
