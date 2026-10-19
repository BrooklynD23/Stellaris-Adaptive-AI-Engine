"""Run ``_child.py`` in a fresh interpreter and return its facts and the run's bus events."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_CHILD = Path(__file__).with_name("_child.py")


def run_child(years: int, runs_dir: Path, exe: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fresh subprocess (``sys.executable``); returns (facts, bus events)."""
    proc = subprocess.run(
        [sys.executable, str(_CHILD), str(years), str(runs_dir), str(exe)],
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("FACTS"))
    facts: dict[str, Any] = json.loads(line[5:])
    assert facts["code"] == 0
    bus = next(runs_dir.glob("*/bus.jsonl"))
    events = [json.loads(ln) for ln in bus.read_text(encoding="utf-8").splitlines() if ln]
    return facts, events
