"""Shared helper: one ``sae synth`` run in its own directory."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from e2e._cli import CliResult, run_sae

#: C3 section 6 counter table, by strategy name only (outscale_siege and early_punish write identical values).
COUNTER = {
    "turtle": "outscale_siege",
    "tech_rush": "early_punish",
    "early_aggression": "hold_counterpunch",
    "balanced": "baseline",
}


def synth(
    base: Path, exe: Path, archetype: str, years: int, seed: int, *, forced: str | None = None, with_exe: bool = True
) -> CliResult:
    """Run ``sae synth`` under ``base``; ``forced`` pins ``governor.forced_level``."""
    sets = [] if forced is None else [f"governor.forced_level={json.dumps(forced)}"]
    argv = ["synth", "--archetype", archetype, "--years", str(years), "--seed", str(seed)]
    return run_sae(argv, base, parser_exe=exe if with_exe else None, sets=sets)


def record(base: Path, exe: Path, archetype: str, years: int, seed: int) -> Path:
    """Record one campaign with ``sae synth --record`` under ``base``; returns the recording directory."""
    rec = base / "rec"
    r = run_sae(
        ["synth", "--archetype", archetype, "--years", str(years), "--seed", str(seed), "--record", str(rec)],
        base, parser_exe=exe,
    )
    assert r.code == 0, r.stderr[-500:]
    return rec


def replay(
    rec: Path, base: Path, exe: Path, *, forced: str | None = None, ack_model: str = "from_recording",
    sets: Sequence[str] = (),
) -> CliResult:
    """``sae replay <rec> --speed max`` in its own ``--runs-dir`` (``base/runs``); ``forced`` pins the level."""
    s = list(sets) + ([] if forced is None else [f"governor.forced_level={json.dumps(forced)}"])
    return run_sae(["replay", str(rec), "--speed", "max", "--ack-model", ack_model], base, parser_exe=exe, sets=s)
