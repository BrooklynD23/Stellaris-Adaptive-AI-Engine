"""Shared run-liveness check (matches T4 ``runs.py``). Imports only stdlib, psutil and contracts."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import psutil

from sae.contracts.c8_config import CURRENT_RUN_FILE

__all__ = ["EMPTY_GRACE_S", "is_run_live"]

EMPTY_GRACE_S = 5.0


def is_run_live(runs_dir: Path, run_id: str, *, now: Callable[[], float] = time.time,
                pid_exists: Callable[[int], bool] = psutil.pid_exists,
                grace_s: float = EMPTY_GRACE_S) -> bool:
    """True when ``CURRENT`` names ``run_id`` with a living pid, or is empty and younger than ``grace_s``."""
    path = runs_dir / CURRENT_RUN_FILE
    try:
        text = path.read_text(encoding="utf-8")
        if not text:  # a starter mid-create
            return now() - path.stat().st_mtime < grace_s
        parts = text.split()
        return len(parts) >= 2 and parts[0] == run_id and pid_exists(int(parts[1]))
    except (OSError, ValueError):
        return False
