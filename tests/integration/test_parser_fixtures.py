"""Run T3's ``validate_fixtures.check`` over every fixture directory (T3 request)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from .conftest import REPO

_TOOL = REPO / "parser" / "tools" / "validate_fixtures.py"
_FIXTURES = sorted(p for p in (REPO / "parser" / "fixtures").iterdir() if p.is_dir())


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_fixtures", _TOOL)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fixtures_exist() -> None:
    """Non-vacuity: there are fixtures to check."""
    assert len(_FIXTURES) >= 5


@pytest.mark.parametrize("fixture", _FIXTURES, ids=lambda p: p.name)
def test_fixture_valid(parser_exe: Path, fixture: Path) -> None:
    """Each fixture parses and validates against C4."""
    assert _load().check(parser_exe, fixture) == []
