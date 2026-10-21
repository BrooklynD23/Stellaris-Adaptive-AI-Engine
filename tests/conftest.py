"""Root test config: the ``game`` marker for tests that need real Stellaris inputs (X3 D2)."""

from __future__ import annotations

import os

import pytest

_DOC = "docs/handoff/deferred/X3.md"


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``game`` marker."""
    config.addinivalue_line(
        "markers",
        "game(*env_vars, id=None): needs real Stellaris inputs; skipped unless the SAE_GAME_* vars are set",
    )


def _unset(var: str) -> bool:
    return not os.environ.get(var)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip game tests whose env vars are unset; copy ``id`` to user_properties."""
    for item in items:
        mark = item.get_closest_marker("game")
        if mark is None:
            continue
        deferred_id = mark.kwargs.get("id")
        if deferred_id is not None:
            item.user_properties.append(("deferred_id", deferred_id))
        missing = [v for v in mark.args if _unset(v)]
        if missing:
            anchor = f" ({_DOC}#{str(deferred_id).lower()})" if deferred_id else ""
            item.add_marker(pytest.mark.skip(reason=f"game input missing: {', '.join(missing)}{anchor}"))


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Fail loudly when a supplied game path does not exist (never print the value)."""
    mark = item.get_closest_marker("game")
    if mark is None:
        return
    for var in mark.args:
        value = os.environ.get(var)
        if value and not os.path.exists(value):
            pytest.fail(f"{var} set but path not found", pytrace=False)
