"""F4 code boundary (player-profile plan section 3). F3 lives in tests/e2e/test_fog_f3.py.

``sae/core`` and ``sae/daemon`` must not name god-view or profile-store artefacts. The import-graph half
(``sae.profile``, ``sae.tune`` forbidden) is ``test_ci4_imports.FORBIDDEN_SAE``.
"""

from __future__ import annotations

import re
from pathlib import Path

from .conftest import REPO

BOUNDARY_DIRS = ("core", "daemon")
FORBIDDEN_NAMES = (
    "bus.god", "GodView", "GodObservation", "PlayerRecord", "campaigns.jsonl", "profile.json", "summaries/",
)
_PATTERN = re.compile("|".join(re.escape(n) for n in FORBIDDEN_NAMES))


def _sources() -> list[Path]:
    return [p for d in BOUNDARY_DIRS for p in sorted((REPO / "src" / "sae" / d).rglob("*.py"))]


def test_boundary_has_sources() -> None:
    """Non-vacuity: the grep covers real files."""
    assert len(_sources()) >= 10


def test_f4_no_god_or_profile_names_in_core_and_daemon() -> None:
    hits = [
        f"{p.relative_to(REPO)}:{n}: {line.strip()}"
        for p in _sources()
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if _PATTERN.search(line)
    ]
    assert hits == []


def test_f4_pattern_catches_each_name() -> None:
    """The pattern itself is not vacuous."""
    for name in FORBIDDEN_NAMES:
        assert _PATTERN.search(f"x = open('{name}')")

