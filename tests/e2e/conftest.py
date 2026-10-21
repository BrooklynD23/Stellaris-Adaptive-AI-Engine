"""Shared e2e fixtures: parser binary and full-matrix switch (CLI helpers live in ``_cli``)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sae.adapters.paths import resolve_paths
from sae.contracts.c8_config import PathsConfig

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def parser_exe() -> Path:
    """The ``sae-parse`` binary via T2's resolver; the test is skipped when none is built."""
    exe = resolve_paths(PathsConfig()).parser_exe
    if exe is None or not exe.is_file():
        pytest.skip("build parser/")
    return exe


@pytest.fixture(scope="session")
def full() -> bool:
    """True when ``T7_E2E_FULL=1``: the full seed matrix."""
    return os.environ.get("T7_E2E_FULL") == "1"
