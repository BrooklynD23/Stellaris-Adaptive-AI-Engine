"""Subprocess runner for ``python -m sae <cmd>`` used by every e2e test."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")
TIMEOUT_S = 180
MAX_PARALLEL = 4


@dataclass(frozen=True)
class CliResult:
    """Outcome of one CLI call. ``run_dir`` is the single run directory created under ``runs_dir`` (or None)."""

    code: int
    stdout: str
    stderr: str
    runs_dir: Path
    cwd: Path

    @property
    def run_dir(self) -> Path | None:
        """The run directory, when exactly one exists."""
        dirs = [p for p in self.runs_dir.iterdir() if p.is_dir()] if self.runs_dir.is_dir() else []
        return dirs[0] if len(dirs) == 1 else None

    def strays(self) -> list[str]:
        """Files the call wrote outside ``runs_dir``: in its cwd, or extra entries beside the run dir."""
        out = [str(p) for p in self.cwd.rglob("*")] if self.cwd.is_dir() else []
        runs = list(self.runs_dir.iterdir()) if self.runs_dir.is_dir() else []
        return out + [str(p) for p in runs if p != self.run_dir]


def run_sae(
    argv: Sequence[str],
    base: Path,
    *,
    parser_exe: Path | None = None,
    sets: Sequence[str] = (),
    timeout: int = TIMEOUT_S,
    use_runs_dir: bool = True,
) -> CliResult:
    """Run ``python -m sae <argv> --runs-dir base/runs [--set ...]`` in a subprocess with cwd in ``base/cwd``.

    ``parser_exe`` is passed explicitly as ``--set paths.parser_exe="<exe>"`` (T2 default defect, proposal 014).
    """
    runs = base / "runs"
    cwd = base / "cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "sae", *argv]
    if use_runs_dir:
        cmd += ["--runs-dir", str(runs)]
    if parser_exe is not None:
        cmd += ["--set", f"paths.parser_exe={json.dumps(str(parser_exe))}"]
    for s in sets:
        cmd += ["--set", s]
    env = {k: v for k, v in os.environ.items() if not k.startswith("SAE_")}
    env["PYTHONHASHSEED"] = "0"
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    return CliResult(p.returncode, p.stdout, p.stderr, runs, cwd)


def run_parallel(jobs: Sequence[Callable[[], T]], workers: int = MAX_PARALLEL) -> list[T]:
    """Run jobs on up to ``workers`` (<= 4) threads; results keep job order, the first exception propagates."""
    with ThreadPoolExecutor(max_workers=min(workers, MAX_PARALLEL)) as pool:
        futures = [pool.submit(j) for j in jobs]
        return [f.result() for f in futures]
