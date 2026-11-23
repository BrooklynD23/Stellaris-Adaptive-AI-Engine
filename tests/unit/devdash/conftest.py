"""Shared setup for the dev-dashboard unit tests.

Puts ``tools/game`` and ``tools/devdash`` on ``sys.path``. ``tools/devdash`` goes in last, so it sits first and
``devdash`` resolves to the production package. An autouse fixture points ``SAE_DEVDASH_DIR`` at a per-test temporary
directory, so no test writes to the real ``.sae-dev/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Spec D8: the dev tooling (dashboard server, hooks, task board, game pipeline) runs on WSL/Linux only. Its tests use
# POSIX facilities (fcntl, fork-style subprocesses, signals), so on Windows CI they are not even imported. Only the
# platform-neutral boundary and gate-registry tests remain.
_PORTABLE = {"conftest.py", "test_boundary.py", "test_gates.py"}
collect_ignore: list[str] = (
    [p.name for p in Path(__file__).parent.glob("*.py") if p.name not in _PORTABLE] if sys.platform == "win32" else []
)

_ROOT = Path(__file__).resolve().parents[3]
for _tool in ("tools/game", "tools/devdash"):
    _entry = str(_ROOT / _tool)
    if _entry not in sys.path:
        sys.path.insert(0, _entry)


@pytest.fixture(autouse=True)
def dev(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``SAE_DEVDASH_DIR`` at ``tmp_path/.sae-dev`` for this test and return that directory."""
    target = tmp_path / ".sae-dev"
    monkeypatch.setenv("SAE_DEVDASH_DIR", str(target))
    return target
