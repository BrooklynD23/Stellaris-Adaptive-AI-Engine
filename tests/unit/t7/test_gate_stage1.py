"""``sae gate stage1`` through ``gate.cli.main`` on the seeded live-mode fixture (paths under tmp_path)."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from t9._synth_campaigns import BuiltCampaign, build_campaigns

from sae.bench.gate import GATE_VERSION, cli, metrics
from sae.contracts import c10_profile
from sae.contracts.c8_config import SaeConfig, resolve_config
from sae.contracts.c10_profile import (
    GateCheck,
    GateReport,
    PolicyVersion,
    Preregistration,
    RegistryAction,
    RegistryRow,
    append_registry_row,
    canonical_json,
    policy_sha256,
)
from sae.contracts.common import Strategy
from sae.core.policy import builtin_policy_doc, load_builtin
from sae.profile import store

AT = datetime(2026, 10, 20, tzinfo=UTC)
BUILTIN = load_builtin().sha256
HELDIN = (
    "held-in, biased toward the candidate; LOCO per fold is in the tuner notes; the unbiased check is Stage 2 sealed"
)


class GEnv:
    """A tmp ``<sae_dir>`` with policy/registry helpers; ``run`` drives ``sae gate``."""

    def __init__(self, root: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self.sae = root / "sae"
        self.runs = root / "runs"
        self.runs.mkdir(parents=True)
        self.pdir = self.sae / "policies"
        self.capsys = capsys
        self.built: list[BuiltCampaign] = []

    @property
    def registry(self) -> Path:
        return self.pdir / "registry.jsonl"

    def campaigns(self, mode: Any = "live", n_labelled: int = 6) -> None:
        self.built = build_campaigns(self.sae, self.runs, mode, n_labelled)

    def flags(self) -> list[str]:
        return ["--set", f"paths.sae_dir={self.sae}", "--runs-dir", str(self.runs)]

    def config(self) -> SaeConfig:
        return resolve_config({}, {}, {"paths": {"sae_dir": str(self.sae), "runs_dir": str(self.runs)}})

    def run(self, verb: str, sha: str, *args: str) -> tuple[int, str, str]:
        self.capsys.readouterr()
        code = cli.main("gate", [verb, sha, *self.flags(), *args])
        out = self.capsys.readouterr()
        return code, out.out, out.err

    def policy(self, rho: float = 0.021, *, write: bool = True, **kw: Any) -> str:
        """A valid child of the built-in policy; keyword overrides: pin, campaign_ids, synth_seeds, notes, weights."""
        base = builtin_policy_doc()
        tuning = base.tuning.model_copy(update={
            "method": "manual", "campaign_ids": kw.get("campaign_ids", ()), "synth_seeds": kw.get("synth_seeds", ()),
        })  # fmt: skip
        fixed = base.fixed
        if "weights" in kw:
            fixed = fixed.model_copy(update={"outcome_weights": kw["weights"]})
        doc: PolicyVersion = base.model_copy(update={
            "version": "0.2.0", "game_version_pin": kw.get("pin", "v4.4.*"), "tuning": tuning, "fixed": fixed,
            "parent_sha256": kw.get("parent", BUILTIN), "notes": kw.get("notes"),
            "detector": base.detector.model_copy(update={"rho": rho}),
        })  # fmt: skip
        sha = policy_sha256(doc)
        if write:
            store.write_atomic(self.pdir / f"{sha}.json", canonical_json(doc))
        return sha

    def row(self, action: RegistryAction, sha: str, by: str, evidence: str | None = None) -> None:
        append_registry_row(self.registry, RegistryRow(seq=0, at=AT, action=action, sha256=sha, by=by,
                                                       evidence_sha256=evidence))  # fmt: skip

    def evidence(self, model: GateReport | Preregistration) -> str:
        data = canonical_json(model)
        digest = hashlib.sha256(data).hexdigest()
        store.write_atomic(self.pdir / "evidence" / f"{digest}.json", data)
        return digest

    def propose(self, sha: str) -> None:
        self.row("propose", sha, "tuner")

    def prereg(self, sha: str, min_effect: float = -1.0, margin: float = 0.5) -> None:
        ev = self.evidence(Preregistration(
            candidate_sha256=sha, incumbent_sha256=BUILTIN, kind="detector_replay", primary_metric="loco_log_loss",
            min_effect=min_effect, non_inferiority_margin=margin, budget=6, min_n=1, created_at=AT,
        ))  # fmt: skip
        self.row("prereg", sha, "human:test", ev)

    def stage1_row(self, sha: str, passed: bool = True) -> None:
        rep = GateReport(stage=1, candidate_sha256=sha, incumbent_sha256=BUILTIN, gate_version=GATE_VERSION,
                         checks=(GateCheck(name="schema", passed=passed),), passed=passed, created_at=AT)  # fmt: skip
        self.row("stage1_pass" if passed else "stage1_fail", sha, "gate", self.evidence(rep))

    def actions(self) -> list[str]:
        if not self.registry.exists():
            return []
        return [RegistryRow.model_validate_json(x).action for x in self.registry.read_text().splitlines()]

    def last_report(self) -> GateReport:
        last = RegistryRow.model_validate_json(self.registry.read_text().splitlines()[-1])
        raw = (self.pdir / "evidence" / f"{last.evidence_sha256}.json").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == last.evidence_sha256
        return GateReport.model_validate_json(raw)


@pytest.fixture
def genv(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> GEnv:
    """Gate env with ``SAE_*`` variables removed and the human-row TTY guard satisfied (prereg rows are human)."""
    for name in list(__import__("os").environ):
        if name.startswith("SAE_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(c10_profile, "_is_tty", lambda _stream: True)
    return GEnv(tmp_path, capsys)


def ready(genv: GEnv, rho: float = 0.021, **kw: Any) -> str:
    """Live fixture plus a proposed, preregistered candidate."""
    genv.campaigns()
    sha = genv.policy(rho, **{k: v for k, v in kw.items() if k not in {"min_effect"}})
    genv.propose(sha)
    if kw.get("prereg", True):
        genv.prereg(sha, kw.get("min_effect", -1.0))
    return sha


def check(rep: GateReport, name: str) -> GateCheck:
    return next(c for c in rep.checks if c.name == name)


def test_dummy_candidate_passes(genv: GEnv) -> None:
    sha = ready(genv)
    code, out, _ = genv.run("stage1", sha)
    assert code == 0, out
    assert genv.actions() == ["propose", "prereg", "stage1_pass"]
    rep = genv.last_report()
    assert rep.passed and rep.stage == 1 and rep.gate_version == GATE_VERSION
    names = [c.name for c in rep.checks]
    for n in ("schema", "parent", "mutable_surface", "pin", "sealed_split", "seed_range", "sealed_budget", "prereg",
              "gate_replay", "heldin_log_loss_delta", "feel_switches", "feel_commitment"):
        assert n in names and check(rep, n).passed
    assert "repo_suite" not in names
    assert check(rep, "heldin_log_loss_delta").detail == HELDIN
    assert "repo suite not run" in (check(rep, "gate_replay").detail or "")
    assert check(rep, "v1_constraints").passed


def test_sealed_id_candidate_fails(genv: GEnv) -> None:
    genv.campaigns()
    sha = genv.policy(campaign_ids=(genv.built[5].campaign_id,))
    genv.propose(sha)
    genv.prereg(sha)
    code, _, _ = genv.run("stage1", sha)
    assert code == 1 and genv.actions()[-1] == "stage1_fail"
    rep = genv.last_report()
    assert not rep.passed and not check(rep, "sealed_split").passed


def test_fixed_change_fails_mutable_surface(genv: GEnv) -> None:
    sha = ready(genv, weights={"d_fleet_share": 2.0})
    assert genv.run("stage1", sha)[0] == 1
    assert not check(genv.last_report(), "mutable_surface").passed


def test_missing_prereg_fails(genv: GEnv) -> None:
    sha = ready(genv, prereg=False)
    assert genv.run("stage1", sha)[0] == 1
    rep = genv.last_report()
    assert not check(rep, "prereg").passed and not check(rep, "heldin_log_loss_delta").passed


def test_seed_range_fails(genv: GEnv) -> None:
    sha = ready(genv, synth_seeds=(5, 1_000_000))
    assert genv.run("stage1", sha)[0] == 1
    assert not check(genv.last_report(), "seed_range").passed


def test_same_detector_after_stage2_fails_sealed_budget(genv: GEnv) -> None:
    genv.campaigns()
    first = genv.policy(0.021)
    genv.propose(first)
    genv.prereg(first)
    genv.stage1_row(first)
    genv.row("stage2_pass", first, "gate", genv.evidence(GateReport(
        stage=2, candidate_sha256=first, incumbent_sha256=BUILTIN, gate_version=GATE_VERSION, passed=True,
        checks=(GateCheck(name="sealed_noninferior:" + genv.built[5].campaign_id, passed=True),), created_at=AT)))
    twin = genv.policy(0.021, notes="same detector, new notes")
    assert twin != first
    genv.propose(twin)
    genv.prereg(twin)
    assert genv.run("stage1", twin)[0] == 1
    assert not check(genv.last_report(), "sealed_budget").passed


def test_refusals(genv: GEnv) -> None:
    genv.campaigns()
    sha = genv.policy()
    code, _, err = genv.run("stage1", sha)
    assert code == 2 and "no propose row" in err
    genv.propose(sha)
    genv.prereg(sha)
    assert genv.run("stage1", sha)[0] == 0
    code, _, err = genv.run("stage1", sha)
    assert code == 2 and "already has a Stage 1 row" in err
    code, _, err = genv.run("stage1", genv.policy(0.03), "--repo", str(genv.sae / "nope"))
    assert code == 2


def test_schema_parent_and_pin_failures(genv: GEnv) -> None:
    genv.campaigns()
    ghost = "c" * 64  # proposed, but no policy file
    genv.propose(ghost)
    assert genv.run("stage1", ghost)[0] == 1
    assert [c.name for c in genv.last_report().checks] == ["schema"]
    orphan = genv.policy(0.03, parent="a" * 64)
    genv.propose(orphan)
    assert genv.run("stage1", orphan)[0] == 1
    rep = genv.last_report()
    assert not check(rep, "parent").passed and not check(rep, "mutable_surface").passed
    old = genv.policy(0.04, pin="v3.0.*")
    genv.propose(old)
    assert genv.run("stage1", old)[0] == 1
    assert not check(genv.last_report(), "pin").passed


def test_no_tune_campaigns_fails(genv: GEnv) -> None:
    sha = genv.policy()
    genv.propose(sha)
    genv.prereg(sha)
    assert genv.run("stage1", sha)[0] == 1
    assert not check(genv.last_report(), "gate_replay").passed


def test_heldin_threshold(genv: GEnv) -> None:
    sha = ready(genv, min_effect=5.0)  # impossible effect size
    assert genv.run("stage1", sha)[0] == 1
    rep = genv.last_report()
    assert not check(rep, "heldin_log_loss_delta").passed and check(rep, "gate_replay").passed


def test_replay_crash_and_nondeterminism(genv: GEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    sha = ready(genv)
    real = metrics.detector_trace

    def boom(*_a: Any) -> Any:
        raise ValueError("boom")

    monkeypatch.setattr("sae.bench.gate.stage1.detector_trace", boom)
    assert genv.run("stage1", sha)[0] == 1
    assert "replay error" in (check(genv.last_report(), "gate_replay").detail or "")

    sha2 = genv.policy(0.05)
    genv.propose(sha2)
    genv.prereg(sha2)
    calls = {"n": 0}

    def flaky(obs: Any, policy: Any) -> Any:
        rows = real(obs, policy)
        if policy_sha256(policy) == BUILTIN:
            return rows
        calls["n"] += 1
        return rows[1:] if calls["n"] % 2 == 0 else rows

    monkeypatch.setattr("sae.bench.gate.stage1.detector_trace", flaky)
    assert genv.run("stage1", sha2)[0] == 1
    assert "differ" in (check(genv.last_report(), "gate_replay").detail or "")


def test_feel_checks_fail_on_twitchy_strategy(genv: GEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    sha = ready(genv)
    real = metrics.strategy_trace

    def twitchy(obs: Any, policy: Any) -> Any:
        rows = real(obs, policy)
        if policy_sha256(policy) == BUILTIN:
            return rows
        a, b = rows[0][1], next(s for s in Strategy if s != rows[0][1])
        return [(m, a if (i // 3) % 2 == 0 else b) for i, (m, _s) in enumerate(rows)]

    monkeypatch.setattr("sae.bench.gate.stage1.strategy_trace", twitchy)
    assert genv.run("stage1", sha)[0] == 1
    rep = genv.last_report()
    assert not check(rep, "feel_switches").passed and not check(rep, "feel_commitment").passed


def test_repo_suite_records_exit_codes(genv: GEnv, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sha = ready(genv)
    seen: list[tuple[str, ...]] = []

    def fake_run(cmd: Any, **_kw: Any) -> Any:
        seen.append(tuple(cmd))
        return subprocess.CompletedProcess(cmd, 1 if "mypy" in cmd else 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    repo = tmp_path / "checkout"
    repo.mkdir()
    assert genv.run("stage1", sha, "--repo", str(repo))[0] == 1
    rep = genv.last_report()
    suite = check(rep, "repo_suite")
    assert not suite.passed and suite.detail == "pytest=0 mypy=1 ruff=0" and suite.value == 1.0
    assert ("uv", "run", "pytest", "-x", "-q") in seen and ("uv", "run", "mypy", "--strict") in seen
    assert "repo suite not run" not in (check(rep, "gate_replay").detail or "")


def test_repo_suite_tool_missing(genv: GEnv, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sha = ready(genv)

    def missing(cmd: Any, **_kw: Any) -> Any:
        raise FileNotFoundError("uv")

    monkeypatch.setattr(subprocess, "run", missing)
    assert genv.run("stage1", sha, "--repo", str(tmp_path))[0] == 1
    assert check(genv.last_report(), "repo_suite").detail == "pytest=-1 mypy=-1 ruff=-1"


def test_include_synth_writes_no_row(genv: GEnv) -> None:
    genv.campaigns("synth")
    sha = genv.policy()
    genv.propose(sha)
    genv.prereg(sha)
    assert genv.run("stage1", sha)[0] == 1  # no live tune campaigns
    assert genv.actions()[-1] == "stage1_fail"
    sha2 = genv.policy(0.05)
    genv.propose(sha2)
    genv.prereg(sha2)
    before = genv.actions()
    code, out, _ = genv.run("stage1", sha2, "--include-synth")
    assert code == 0 and "synth: diagnostic only, not verification" in out
    assert genv.actions() == before


def test_unreadable_registry_is_exit_1(genv: GEnv) -> None:
    genv.campaigns()
    genv.pdir.mkdir(parents=True)
    genv.registry.mkdir()  # a directory where the file should be
    assert genv.run("stage1", "a" * 64)[0] == 1
