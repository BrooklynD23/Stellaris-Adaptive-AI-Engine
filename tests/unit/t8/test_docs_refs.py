"""T8 docs reference only commands, subcommands and files that exist."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sae.cli import _COMMANDS
from sae.m0.checks import BY_ID

ROOT = Path(__file__).resolve().parents[3]
DOCS = [ROOT / "README.md", ROOT / "docs" / "user_guide.md", ROOT / "docs" / "developer_guide.md",
        ROOT / "docs" / "m0_checklist.md", ROOT / "tools" / "m0" / "README.md", ROOT / "packaging" / "PLAN.md"]
M0_SUBCOMMANDS = {"list", "steps", "init", "stage", "check", "tail", "record", "summary"}


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_commands_exist(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    for cmd in re.findall(r"`sae ([a-z0-9]+)", text):
        assert cmd in _COMMANDS, f"{doc.name}: unknown command sae {cmd}"
    for sub in re.findall(r"sae m0 ([a-z]+)", text):
        assert sub in M0_SUBCOMMANDS, f"{doc.name}: unknown sae m0 {sub}"
    for check in re.findall(r"sae m0 (?:steps|check|record) ((?:M0-\d+|M1-P|R-\d))", text):
        assert check in BY_ID, f"{doc.name}: unknown check {check}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_relative_links_resolve(doc: Path) -> None:
    for target in re.findall(r"\]\(([^)#:]+)(?:#[^)]*)?\)", doc.read_text(encoding="utf-8")):
        assert (doc.parent / target).exists(), f"{doc.name}: broken link {target}"
