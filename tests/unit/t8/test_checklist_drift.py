"""Drift guard: every UNVERIFIED id and every M0 check must appear in docs/m0_checklist.md."""

from __future__ import annotations

import re
from pathlib import Path

from sae.m0.checks import BY_ID, CHECKS

ROOT = Path(__file__).resolve().parents[3]
CHECKLIST = (ROOT / "docs" / "m0_checklist.md").read_text(encoding="utf-8")
A_ID = re.compile(r"\bA-\d{2}\b")
TEST_ID = re.compile(r"\b(?:M0-\d+|M1-P|R-\d)\b")


def _registry() -> dict[str, set[str]]:
    """README section 5: A-id -> test ids from its Test column."""
    text = (ROOT / "contracts" / "README.md").read_text(encoding="utf-8")
    section = text.split("## 5.", 1)[1]
    rows: dict[str, set[str]] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 4 and A_ID.fullmatch(cells[1]):
            rows[cells[1]] = set(TEST_ID.findall(cells[3]))
    return rows


def _checklist_rows(table_heading: str) -> set[str]:
    section = CHECKLIST.split(table_heading, 1)[1].split("\n## ", 1)[0]
    cells = {line.split("|")[1].strip() for line in section.splitlines() if line.startswith("| ")}
    return {c for c in cells if A_ID.fullmatch(c) or TEST_ID.fullmatch(c)}


def _tagged_ids() -> set[str]:
    """A-ids on any line mentioning UNVERIFIED in mod/, src/ and parser/ (test files excluded)."""
    found: set[str] = set()
    roots = [ROOT / "mod", ROOT / "src", ROOT / "parser" / "src", ROOT / "parser" / "README.md"]
    for root in roots:
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        for path in files:
            if "tests" in path.parts or path.suffix not in {".py", ".rs", ".txt", ".md", ".mod", ".yml"}:
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if "UNVERIFIED" in line:
                    found |= set(A_ID.findall(line))
    return found


def test_registry_is_parsed() -> None:
    assert len(_registry()) >= 42


def test_every_registry_id_in_checklist() -> None:
    missing = set(_registry()) - _checklist_rows("## B.")
    assert not missing, f"add rows to docs/m0_checklist.md table B: {sorted(missing)}"


def test_every_tagged_id_in_checklist() -> None:
    missing = _tagged_ids() - _checklist_rows("## B.")
    assert not missing, f"UNVERIFIED ids in code but not in the checklist: {sorted(missing)}"


def test_mod_registry_ids_in_checklist() -> None:
    text = (ROOT / "mod" / "sae_bridge" / "UNVERIFIED.md").read_text(encoding="utf-8")
    ids = {line.split("|")[1].strip() for line in text.splitlines() if line.startswith("| A-")}
    assert ids <= _checklist_rows("## B.")


def test_every_check_in_checklist_and_back() -> None:
    assert _checklist_rows("## A.") == set(BY_ID)


def test_registry_tests_match_kit_checks() -> None:
    """Each A-id's M0 tests in the registry exist in the kit and list that A-id."""
    for a_id, tests in _registry().items():
        for test in tests:
            assert test in BY_ID, f"{a_id}: test {test} has no sae m0 check"
            assert a_id in BY_ID[test].assumptions, f"{test} should list {a_id}"


def test_kit_assumptions_are_registered() -> None:
    registry = _registry()
    for check in CHECKS:
        for a_id in check.assumptions:
            assert a_id in registry, f"{check.id} lists unregistered {a_id}"
