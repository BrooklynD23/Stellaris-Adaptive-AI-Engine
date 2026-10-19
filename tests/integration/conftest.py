"""Shared integration fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from sae.adapters.paths import resolve_paths
from sae.contracts.c8_config import PathsConfig

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def parser_exe() -> Path:
    """The ``sae-parse`` binary via T2's resolver (proposal 014); skip when none is built."""
    exe = resolve_paths(PathsConfig()).parser_exe
    if exe is None or not exe.is_file():
        pytest.skip("build parser/ (cargo build --release -j 1)")
    return exe
