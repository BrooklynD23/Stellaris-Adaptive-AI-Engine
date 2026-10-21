"""Deferred game tests X4-G1 / X4-G2: tune a detector on the player's real labelled live campaigns.

Needs a copy of a real ``<sae_dir>`` (campaigns.jsonl, profile/<key>/labels/, runs/) with closed, labelled live
campaigns. Skipped everywhere else. Procedure and evidence: ``docs/handoff/deferred/X4.md``. UNVERIFIED: real
LegalViews yield feature vectors on which these numbers mean something (M0/M2).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from sae.core.policy import load_builtin
from sae.profile import ledger
from sae.tune import cli

SAE_DIR = os.environ.get("SAE_GAME_SAE_DIR")
PLAYER = os.environ.get("SAE_GAME_PLAYER_KEY", "default")
SKIP = "set SAE_GAME_SAE_DIR (X4-G1; needs 6 closed labelled live campaigns, see X4-G0)"

pytestmark = [pytest.mark.game, pytest.mark.skipif(not SAE_DIR, reason=SKIP)]


@pytest.fixture
def real_sae(tmp_path: Path) -> Path:
    """Private copy of the real sae dir, so the run never writes into the player's folder."""
    assert SAE_DIR is not None
    dest = tmp_path / "sae"
    shutil.copytree(SAE_DIR, dest)
    return dest


def _tune_count(sae: Path) -> int:
    return len(ledger.labelled_campaigns(sae, PLAYER, sae / "runs", "tune", False))


def test_x4_g1_v1_on_real_campaigns(real_sae: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """X4-G1: V-1 on the real tune campaigns (ECE <= 0.10 on each fold, counter right on >= 4 of 5)."""
    n = _tune_count(real_sae)
    if n < cli.MIN_CAMPAIGNS:
        pytest.skip(f"X4-G0 prerequisite: {n} < 5 labelled tune campaigns")
    code = cli.main(
        "tune",
        [
            "detector",
            "--set",
            f"paths.sae_dir={real_sae}",
            "--set",
            f"paths.runs_dir={real_sae / 'runs'}",
            "--player",
            PLAYER,
            "--force",
        ],
    )
    out = capsys.readouterr()
    with capsys.disabled():
        print(f"\n--- X4-G1/G2 tuner output ---\n{out.out}{out.err}--- end ---")
    assert code == 0, f"V-1 not met (no feasible grid point) or refused: {out.err}"
    # exit 0 already means ECE <= 0.10 on every fold and the counter right on >= 4 of 5 (see fit.loco/choose)


def test_x4_g2_v3_block(real_sae: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """X4-G2: print the V-3 block (switches, reward spread) for the maintainer to record; never fails on values."""
    if (n := _tune_count(real_sae)) < 5:
        pytest.skip(f"X4-G0 prerequisite: {n} < 5 labelled tune campaigns")
    campaigns = ledger.labelled_campaigns(real_sae, PLAYER, real_sae / "runs", "tune", False)

    line = cli._v3(campaigns, real_sae / "runs", load_builtin().doc)
    with capsys.disabled():
        print(f"\nX4-G2 {line}")
    assert line.startswith("V-3")
