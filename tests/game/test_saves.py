"""Deferred game tests X3-G1..G5: the parser on real Stellaris saves.

Every game fact here is UNVERIFIED until the Stellaris PC run: A-19 (save header: version/date/player),
A-20 (ironman flag may be absent), A-24 (parser cost on real saves), A-34 (``autosave_*.sav`` naming),
A-46 (autosaves share one folder). G1/G2/G3/G5 consume ``parsed_saves``; only G4 spawns the parser.
Assert messages carry counts, basenames and field names only, never save content or full paths.
"""

from __future__ import annotations

import fnmatch
import itertools
import json
import statistics
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from e2e._bus import month_index

from sae.contracts.c4_parse import PARSE_OUTPUT_ADAPTER, ParseError, ParseResult
from sae.contracts.c7_governor import GovernorConfig

_REPO = Path(__file__).resolve().parents[2]
_EXPECTED = _REPO / "parser" / "fixtures" / "balanced" / "expected.json"
_TIMEOUT_S = 60
_SAVES = "SAE_GAME_SAVES_DIR"

Parsed = list[tuple[Path, int, str]]


def _results(parsed: Parsed) -> list[tuple[str, ParseResult]]:
    """Validate each parse; fail with basename + exit code only."""
    out: list[tuple[str, ParseResult]] = []
    for path, code, stdout in parsed:
        assert code == 0, f"{path.name}: sae-parse exit {code}, expected 0"
        res = PARSE_OUTPUT_ADAPTER.validate_json(stdout)
        assert isinstance(res, ParseResult), f"{path.name}: output is not a ParseResult"
        out.append((path.name, res))
    return out


@pytest.mark.game(_SAVES, id="X3-G1")
def test_parse_real_saves(parsed_saves: Parsed, record_property: Callable[[str, object], None]) -> None:
    """Every real save parses to a valid ParseResult with header fields (UNVERIFIED A-19, A-20)."""
    results = _results(parsed_saves)
    none_ironman = 0
    for name, res in results:
        assert res.schema_version == 1, f"{name}: schema_version"
        assert res.meta.date, f"{name}: meta.date missing"
        assert res.meta.game_version, f"{name}: meta.game_version missing"
        assert any(c.is_player for c in res.countries), f"{name}: no player country"
        assert res.meta.ironman is not True, f"{name}: meta.ironman is True (use a non-Ironman save)"
        none_ironman += res.meta.ironman is None
    msg = f"meta.ironman None in {none_ironman} of {len(results)} saves"
    print(f"A-20 evidence: {msg}")
    record_property("A-20", msg)


@pytest.mark.game(_SAVES, id="X3-G2")
def test_parse_cost_budget(parsed_saves: Parsed, record_property: Callable[[str, object], None]) -> None:
    """wall_s <= 3.0 and rss within the governor worker cap on every save (UNVERIFIED A-24)."""
    cap = GovernorConfig().worker_mem_cap_bytes
    results = _results(parsed_saves)
    for name, res in results:
        assert res.cost.wall_s <= 3.0, f"{name}: cost.wall_s {res.cost.wall_s:.2f} > 3.0"
        assert res.cost.rss_peak_bytes <= cap, f"{name}: cost.rss_peak_bytes {res.cost.rss_peak_bytes} > {cap}"
    walls = [r.cost.wall_s for _, r in results]
    rss = [r.cost.rss_peak_bytes for _, r in results]
    msg = (f"wall_s max={max(walls):.3f} median={statistics.median(walls):.3f}; "
           f"rss_peak_bytes max={max(rss)} median={statistics.median(rss):.0f}; n={len(walls)}")
    print(f"A-24 evidence: {msg}")
    record_property("A-24", msg)


def _paths(node: Any, prefix: str = "") -> set[str]:
    """Key paths of nested dicts; lists flatten to ``[]``, self_state ids to ``*``."""
    out: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            key = "*" if prefix.endswith("self_state") else str(k)
            p = f"{prefix}.{key}" if prefix else key
            out.add(p)
            out |= _paths(v, p)
    elif isinstance(node, list):
        for item in node:
            out |= _paths(item, f"{prefix}[]")
    return out


@pytest.mark.game(_SAVES, id="X3-G3")
def test_roundtrip_vs_fixtures(parsed_saves: Parsed, record_property: Callable[[str, object], None]) -> None:
    """Real output keys (minus cost, save) cover the fixture's keys; strict models accept real data (F2)."""
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    want = _paths({k: v for k, v in expected.items() if k not in {"cost", "save"}})
    _results(parsed_saves)  # strict validation (extra=forbid) on every save = F2 on real data
    path, _, stdout = parsed_saves[0]
    real = {k: v for k, v in json.loads(stdout).items() if k not in {"cost", "save"}}
    missing = sorted(want - _paths(real))
    # Lists in a real save may be empty (no managed AI): only list-contained paths may be absent then.
    hard = [m for m in missing if "[]" not in m]
    assert not hard, f"{path.name}: fixture keys missing from real output: {', '.join(hard)}"
    msg = f"{len(missing)} list-element keys absent in {path.name}"
    print(f"X3-G3 evidence: {msg}")
    record_property("X3-G3", msg)


@pytest.mark.game(_SAVES, id="X3-G4")
def test_exit_codes(parsed_saves: Parsed, game_parser_exe: Path, tmp_path: Path) -> None:
    """Missing file exits 3; a half-size temp copy exits 5 or 6; stdout is a ParseError."""
    def run(save: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(game_parser_exe), "parse", "--save", str(save)],
            capture_output=True, text=True, timeout=_TIMEOUT_S, check=False,
        )

    r = run(tmp_path / "missing.sav")
    assert r.returncode == 3, f"missing file: exit {r.returncode}, expected 3"
    assert isinstance(PARSE_OUTPUT_ADAPTER.validate_json(r.stdout), ParseError), "missing file: not a ParseError"

    src = parsed_saves[0][0]
    data = src.read_bytes()
    cut = tmp_path / "truncated.sav"
    cut.write_bytes(data[: len(data) // 2])
    r = run(cut)
    assert r.returncode in (5, 6), f"truncated copy of {src.name}: exit {r.returncode}, expected 5 or 6"
    assert isinstance(PARSE_OUTPUT_ADAPTER.validate_json(r.stdout), ParseError), "truncated: not a ParseError"


@pytest.mark.game(_SAVES, id="X3-G5")
def test_autosave_cadence(
    parsed_saves: Parsed, game_parser_exe: Path, record_property: Callable[[str, object], None]
) -> None:
    """autosave_*.sav dates are consecutive months, one folder (UNVERIFIED A-24, A-34, A-46)."""
    auto = [(p, c, s) for p, c, s in parsed_saves if fnmatch.fnmatch(p.name, "autosave_*.sav")]
    if not auto:
        pytest.skip("no autosave_*.sav in SAE_GAME_SAVES_DIR")
    assert len({p.parent for p, _, _ in auto}) == 1, "autosaves span more than one folder (A-46)"
    months = sorted({month_index(r.meta.date) for _, r in _results(auto)})
    gaps = [b - a for a, b in itertools.pairwise(months)]
    spy = subprocess.run(
        [sys.executable, "-m", "sae", "bench", "spy", str(auto[0][0].parent), "--parser-exe", str(game_parser_exe)],
        capture_output=True, text=True, timeout=_TIMEOUT_S * 4, check=False,
    )
    msg = f"{spy.stdout.strip() or 'no spy output'} (exit {spy.returncode}, not asserted)"
    print(f"A-24 evidence: {msg}")
    record_property("A-24-spy", msg)
    assert all(g <= 1 for g in gaps), f"month gaps > 1: {sorted(g for g in gaps if g > 1)} over {len(months)} months"
