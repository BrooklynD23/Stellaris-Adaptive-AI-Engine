"""Each contract doc's Version line, Status line and the README index row agree (proposal 031-033 header fix)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
DOCS = sorted(CONTRACTS.glob("C[0-9]*_*.md"), key=lambda p: int(p.name[1 : p.name.index("_")]))


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name.split("_")[0])
def test_version_status_and_index_agree(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    version = re.search(r"^\| Version \| ([0-9.]+)", text, re.M)
    status = re.search(r"^\| Status \| FROZEN v([0-9.]+)", text, re.M)
    assert version and status, f"{doc.name}: missing Version or Status line"
    assert version.group(1) == status.group(1), f"{doc.name}: Version vs Status"
    cid = doc.name.split("_")[0]
    index = (CONTRACTS / "README.md").read_text(encoding="utf-8")
    row = re.search(rf"^\| {cid} \| .*\| ([0-9.]+) \| FROZEN v([0-9.]+) \|", index, re.M)
    assert row, f"README index has no row for {cid}"
    assert row.group(1) == row.group(2) == version.group(1), f"README index row for {cid} is stale"
