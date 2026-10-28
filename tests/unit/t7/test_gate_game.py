"""Deferred game test (X4-G3): Stage 1 then Stage 2 detector_replay on real labelled campaigns.

Needs a copy-able ``<sae_dir>`` holding real labelled campaigns (X4-G0) and a proposed, preregistered candidate.
Runs only when ``SAE_GAME_SAE_DIR`` and ``SAE_GAME_CANDIDATE_SHA`` are set; see docs/handoff/deferred/X4.md.
UNVERIFIED (M0): real autosaves produce LegalViews on which these numbers mean something.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from sae.bench.gate import cli
from sae.contracts.c10_profile import GateReport, RegistryRow

pytestmark = pytest.mark.game


def _need(name: str, why: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(why)
    return value


@pytest.mark.game(id="X4-G3")
def test_stage1_then_stage2_on_real_campaigns(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = _need("SAE_GAME_SAE_DIR", "set SAE_GAME_SAE_DIR (X4-G3; needs X4-G0 labelled campaigns)")
    sha = _need("SAE_GAME_CANDIDATE_SHA", "set SAE_GAME_CANDIDATE_SHA (X4-G3; a proposed, preregistered candidate)")
    sae = tmp_path / "sae"
    shutil.copytree(src, sae)  # never mutate the real registry: the sealed query is single-use
    runs = os.environ.get("SAE_GAME_RUNS_DIR") or str(sae / "runs")
    flags = ["--set", f"paths.sae_dir={sae}", "--runs-dir", runs]
    s1 = cli.main("gate", ["stage1", sha, *flags])
    cap = capsys.readouterr()
    if s1 == 2:
        pytest.skip(f"stage1 refused (exit 2): {cap.err.strip()}")
    out = cap.out
    assert s1 in (0, 1), out + cap.err
    if s1 == 1:
        pytest.skip(f"candidate failed Stage 1 on real data (a valid outcome, record it):\n{out}")
    s2 = cli.main("gate", ["stage2", sha, "--objective", "detector_replay", *flags])
    out = capsys.readouterr().out
    assert s2 in (0, 1), out
    rows = [RegistryRow.model_validate_json(x) for x in (sae / "policies" / "registry.jsonl").read_text().splitlines()]
    assert [r.action for r in rows if r.sha256 == sha][-2:-1] == ["stage1_pass"]
    last = rows[-1]
    assert last.action in {"stage2_pass", "stage2_fail"} and last.evidence_sha256
    rep = GateReport.model_validate_json((sae / "policies" / "evidence" / f"{last.evidence_sha256}.json").read_bytes())
    assert rep.passed == (s2 == 0)
