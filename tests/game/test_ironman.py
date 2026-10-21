"""Deferred game test X3-G6: Ironman detection on a real Ironman save (UNVERIFIED A-20; M0-14)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from sae.contracts.c4_parse import PARSE_OUTPUT_ADAPTER, ParseResult

_TIMEOUT_S = 60


@pytest.mark.game("SAE_GAME_IRONMAN_SAVE", id="X3-G6")
@pytest.mark.parametrize("sections", [None, "meta"], ids=["full", "meta"])
def test_ironman_flagged(game_parser_exe: Path, sections: str | None) -> None:
    """Exit 0 and meta.ironman is True for a full parse and ``--sections meta`` (UNVERIFIED A-20)."""
    save = Path(os.environ["SAE_GAME_IRONMAN_SAVE"])
    cmd = [str(game_parser_exe), "parse", "--save", str(save)]
    if sections:
        cmd += ["--sections", sections]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT_S, check=False)
    label = sections or "full"
    assert r.returncode == 0, f"{label}: sae-parse exit {r.returncode}, expected 0"
    res = PARSE_OUTPUT_ADAPTER.validate_json(r.stdout)
    assert isinstance(res, ParseResult), f"{label}: output is not a ParseResult"
    assert res.meta.ironman is True, f"{label}: meta.ironman is {res.meta.ironman}, expected True"
