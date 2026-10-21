"""``sae gate stage2``: sealed tripwire, one query per candidate, synth never spends, game-paired never results."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sae.bench.gate import stage2
from sae.core.policy import builtin_policy_doc
from sae.profile import ledger

from .test_gate_stage1 import BUILTIN, GEnv, check
from .test_gate_stage1 import genv as _genv

genv = _genv  # re-export the fixture without redefining it

TAG = "tripwire, not a significance test (n_sealed = 1)"


def passed_stage1(genv: GEnv, mode: str = "live", rho: float = 0.021, margin: float = 0.5) -> str:
    """Candidate with a recorded stage1_pass (rows written directly so synth fixtures work too)."""
    genv.campaigns(mode)
    sha = genv.policy(rho)
    genv.propose(sha)
    genv.prereg(sha, margin=margin)
    genv.stage1_row(sha)
    return sha


def test_objective_is_required(genv: GEnv) -> None:
    sha = passed_stage1(genv)
    code, _, _ = genv.run("stage2", sha)
    assert code == 2


@pytest.mark.parametrize("objective", ["beat_player", "challenge_band"])
def test_game_paired_exits_4_with_draft(genv: GEnv, objective: str, tmp_path: Path) -> None:
    sha = passed_stage1(genv)
    before = genv.registry.read_bytes()
    out = tmp_path / "protocol.json"
    code, _, err = genv.run("stage2", sha, "--objective", objective, "--out", str(out))
    assert code == 4 and "no result: game-paired Stage 2 needs D6 and real paired runs (M4)" in err
    proto = json.loads(out.read_text())
    assert proto["objective"] == objective and proto["min_n"] >= 30 and len(proto["paired_seeds"]) >= 30
    assert proto["window_years"] == 30 and proto["crash_is_loss"] is True and proto["halt_after_crashes"] == 2
    assert proto["sealed_save_bank_min"] >= 3 * proto["min_n"] and proto["retire_save_after_uses"] == 10
    assert genv.registry.read_bytes() == before  # no row, no change


def test_game_paired_needs_out_and_writable(genv: GEnv, tmp_path: Path) -> None:
    sha = passed_stage1(genv)
    assert genv.run("stage2", sha, "--objective", "beat_player")[0] == 2
    blocked = tmp_path / "file"
    blocked.write_text("x")
    assert genv.run("stage2", sha, "--objective", "beat_player", "--out", str(blocked / "p.json"))[0] == 1


def test_invalid_config_is_3(genv: GEnv) -> None:
    code, _, err = genv.run("stage2", "a" * 64, "--objective", "detector_replay", "--set", "bogus.key=1")
    assert code == 3 and "invalid config" in err


def test_detector_replay_live_writes_row_and_spends_one_query(genv: GEnv) -> None:
    sha = passed_stage1(genv)
    sealed = genv.built[5].campaign_id
    code, out, _ = genv.run("stage2", sha, "--objective", "detector_replay")
    assert code == 0, out
    assert genv.actions() == ["propose", "prereg", "stage1_pass", "stage2_pass"]
    rep = genv.last_report()
    assert rep.stage == 2 and rep.passed and rep.incumbent_sha256 == BUILTIN
    sealed_check = check(rep, f"sealed_noninferior:{sealed}")
    assert sealed_check.passed and sealed_check.detail == TAG
    assert ledger.sealed_query_counts(genv.pdir).per_campaign == {sealed: 1}
    for name in ("detection_delay", "false_lock", "v3_signal"):
        c = check(rep, name)
        assert c.passed and c.detail == f"informational; {TAG}"
    assert "evidence" in out


def test_second_query_refused(genv: GEnv) -> None:
    sha = passed_stage1(genv)
    assert genv.run("stage2", sha, "--objective", "detector_replay")[0] == 0
    before = genv.registry.read_bytes()
    code, _, err = genv.run("stage2", sha, "--objective", "detector_replay")
    assert code == 2 and "already used its sealed query" in err
    assert genv.registry.read_bytes() == before


def test_requires_stage1_pass(genv: GEnv) -> None:
    genv.campaigns()
    sha = genv.policy()
    genv.propose(sha)
    genv.prereg(sha)
    code, _, err = genv.run("stage2", sha, "--objective", "detector_replay")
    assert code == 2 and "stage1_pass" in err
    genv.stage1_row(sha, passed=False)
    assert genv.run("stage2", sha, "--objective", "detector_replay")[0] == 2


def test_no_sealed_campaign_exit_2(genv: GEnv) -> None:
    other = GEnv(genv.sae.parent / "five", genv.capsys)
    other.campaigns("live", n_labelled=5)
    sha5 = other.policy()
    other.propose(sha5)
    other.prereg(sha5)
    other.stage1_row(sha5)
    code, _, err = other.run("stage2", sha5, "--objective", "detector_replay")
    assert code == 2 and "no sealed campaign (D3: label a 6th)" in err
    assert other.actions()[-1] == "stage1_pass"


def test_unreadable_candidate_refused(genv: GEnv) -> None:
    genv.campaigns()
    ghost = "d" * 64
    genv.propose(ghost)
    genv.prereg(ghost)
    genv.stage1_row(ghost)
    assert genv.run("stage2", ghost, "--objective", "detector_replay")[0] == 2


def test_failing_sealed_check_decides_and_informational_never_flip(genv: GEnv) -> None:
    genv.campaigns()
    sha = genv.policy(0.2, notes="worse")
    genv.propose(sha)
    genv.prereg(sha, margin=0.0)
    genv.stage1_row(sha)
    code, _, _ = genv.run("stage2", sha, "--objective", "detector_replay")
    assert genv.actions()[-1] == "stage2_fail" and code == 1
    rep = genv.last_report()
    assert not rep.passed
    sealed = [c for c in rep.checks if c.name.startswith("sealed_noninferior:")]
    assert sealed and not any(c.passed for c in sealed)
    assert all(c.passed for c in rep.checks if not c.name.startswith("sealed_noninferior:"))
    assert rep.passed == all(c.passed for c in rep.checks)


def test_synth_is_diagnostic_and_burns_nothing(genv: GEnv) -> None:
    sha = passed_stage1(genv, "synth")
    before = genv.registry.read_bytes()
    code, out, _ = genv.run("stage2", sha, "--objective", "detector_replay", "--include-synth")
    assert code == 0 and "synth: diagnostic only, not verification" in out
    assert genv.registry.read_bytes() == before
    assert ledger.sealed_query_counts(genv.pdir).per_campaign == {}
    # without the flag a synth-only fixture has no sealed campaign
    assert genv.run("stage2", sha, "--objective", "detector_replay")[0] == 2
    # synth may be repeated and may follow a real query
    assert genv.run("stage2", sha, "--objective", "detector_replay", "--include-synth")[0] == 0


def test_delay_and_false_lock_on_switching_campaigns(genv: GEnv) -> None:
    genv.campaigns()
    tune = ledger.labelled_campaigns(genv.sae, "default", genv.runs, "tune", True)
    cand = builtin_policy_doc()
    checks = stage2.run_detector_replay(cand, cand, 0.0, tune)
    by = {c.name: c for c in checks}
    delay = by["detection_delay"].value
    assert delay is not None and delay >= 0
    assert by["false_lock"].value is not None and 0 <= by["false_lock"].value <= 1
    assert by["v3_signal"].value == 0  # candidate == incumbent
    sealed_checks = [c for c in checks if c.name.startswith("sealed_noninferior:")]
    assert len(sealed_checks) == 5 and all(c.value == 0 and c.passed for c in sealed_checks)


def test_synth_flag_never_reads_live_sealed(genv: GEnv) -> None:
    sha = passed_stage1(genv, "live")
    before = genv.registry.read_bytes()
    code, out, err = genv.run("stage2", sha, "--objective", "detector_replay", "--include-synth")
    assert code == 2 and "no sealed campaign" in err and genv.built[5].campaign_id not in out
    assert genv.registry.read_bytes() == before
    assert ledger.sealed_query_counts(genv.pdir).per_campaign == {}


def test_same_detector_second_sha_refused(genv: GEnv) -> None:
    a = passed_stage1(genv)
    b = genv.policy(0.021, notes="twin")
    genv.propose(b)
    genv.prereg(b)
    genv.stage1_row(b)
    assert genv.run("stage2", a, "--objective", "detector_replay")[0] == 0
    before = genv.registry.read_bytes()
    code, _, err = genv.run("stage2", b, "--objective", "detector_replay")
    assert code == 2 and "detector already spent a sealed query" in err
    assert genv.registry.read_bytes() == before
