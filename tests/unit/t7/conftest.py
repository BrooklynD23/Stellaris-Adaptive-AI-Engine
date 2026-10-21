"""Local minimum: register the ``game`` marker (X3 owns the global one; duplicates merge later)."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``game`` marker for tests needing the real game."""
    config.addinivalue_line("markers", "game: needs the real Stellaris game, saves or logs (deferred)")
