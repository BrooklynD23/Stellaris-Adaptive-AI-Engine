"""T9 test support: the ``game`` marker (X3 owns the global one; duplicates merge later) and policy helpers."""

from __future__ import annotations

import builtins
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
from sae.core.policy import builtin_policy_doc
from sae.profile import policy_cli, store

AT = datetime(2026, 10, 20, tzinfo=UTC)


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``game`` marker for tests needing the real game."""
    config.addinivalue_line("markers", "game: needs the real Stellaris game, saves or logs (deferred)")


class PolicyEnv:
    """A tmp ``<sae_dir>`` with policy and registry helpers; ``run`` drives ``sae policy``."""

    def __init__(self, root: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self.sae = root / "sae"
        self.runs = root / "runs"
        self.runs.mkdir(parents=True)
        self.pdir = self.sae / "policies"
        self.capsys = capsys

    @property
    def registry(self) -> Path:
        return self.pdir / "registry.jsonl"

    def flags(self) -> list[str]:
        return ["--set", f"paths.sae_dir={self.sae}", "--runs-dir", str(self.runs)]

    def config(self) -> SaeConfig:
        return resolve_config({}, {}, {"paths": {"sae_dir": str(self.sae), "runs_dir": str(self.runs)}})

    def run(self, verb: str, *args: str) -> tuple[int, str, str]:
        self.capsys.readouterr()
        code = policy_cli.main("policy", [verb, *self.flags(), *args])
        out = self.capsys.readouterr()
        return code, out.out, out.err

    def policy(self, rho: float, *, pin: str = "v4.4.*", campaign_ids: tuple[str, ...] = (), write: bool = True) -> str:
        """A valid policy (built-in tables, own ``rho``); written to ``policies/<sha>.json`` unless ``write=False``."""
        base = builtin_policy_doc()
        tuning = base.tuning.model_copy(update={"method": "manual", "campaign_ids": campaign_ids})
        doc: PolicyVersion = base.model_copy(update={
            "version": "0.2.0", "game_version_pin": pin, "tuning": tuning,
            "detector": base.detector.model_copy(update={"rho": rho}),
        })  # fmt: skip
        sha = policy_sha256(doc)
        if write:
            store.write_atomic(self.pdir / f"{sha}.json", canonical_json(doc))
        return sha

    def evidence(self, model: GateReport | Preregistration) -> str:
        data = canonical_json(model)
        digest = hashlib.sha256(data).hexdigest()
        store.write_atomic(self.pdir / "evidence" / f"{digest}.json", data)
        return digest

    def row(self, action: RegistryAction, sha: str, by: str, **kw: str | None) -> RegistryRow:
        return append_registry_row(
            self.registry, RegistryRow(seq=0, at=AT, action=action, sha256=sha, by=by, **kw)
        )

    def report(self, sha: str, stage: int, names: tuple[str, ...] = ("heldin_log_loss_delta",)) -> str:
        return self.evidence(GateReport(
            stage=stage, candidate_sha256=sha, incumbent_sha256="0" * 64, gate_version="0.1.0", passed=True,
            checks=tuple(GateCheck(name=n, passed=True) for n in names), created_at=AT,
        ))  # fmt: skip

    def chain(self, sha: str, upto: int = 5, sealed: tuple[str, ...] = ("sealed_noninferior:c-" + "0" * 16,)) -> None:
        """Write the first ``upto`` chain rows (propose, prereg, stage1_pass, stage2_pass, approve); needs ``tty``."""
        prereg = self.evidence(Preregistration(
            candidate_sha256=sha, incumbent_sha256="0" * 64, kind="detector_replay", primary_metric="loco_log_loss",
            min_effect=0.05, non_inferiority_margin=0.02, budget=6, min_n=1, created_at=AT,
        ))  # fmt: skip
        steps: list[Callable[[], object]] = [
            lambda: self.row("propose", sha, "tuner"),
            lambda: self.row("prereg", sha, "human:test", evidence_sha256=prereg),
            lambda: self.row("stage1_pass", sha, "gate", evidence_sha256=self.report(sha, 1)),
            lambda: self.row("stage2_pass", sha, "gate", evidence_sha256=self.report(sha, 2, sealed)),
            lambda: self.row("approve", sha, "human:test"),
        ]
        for step in steps[:upto]:
            step()


@pytest.fixture
def penv(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> PolicyEnv:
    """Policy env with ``SAE_*`` variables removed."""
    for name in list(__import__("os").environ):
        if name.startswith("SAE_"):
            monkeypatch.delenv(name)
    return PolicyEnv(tmp_path, capsys)


@pytest.fixture
def tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the C10 TTY guard and the CLI's own interactivity check pass (capture swaps the stream objects)."""
    monkeypatch.setattr(c10_profile, "_is_tty", lambda _stream: True)
    monkeypatch.setattr(policy_cli, "_interactive", lambda: True)


@pytest.fixture
def answer(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """``answer("ab12cd34")`` makes the next ``input()`` calls return that text."""

    def set_reply(text: str) -> None:
        monkeypatch.setattr(builtins, "input", lambda _prompt="": text)

    return set_reply
