"""``sae policy`` verbs through ``policy_cli.main`` (paths under tmp_path; human rows need the ``tty`` fixture)."""

from __future__ import annotations

import builtins
import hashlib
import sys
from collections.abc import Callable

import pytest

from sae.contracts.c10_profile import Preregistration, RegistryRow
from sae.core.policy import load_builtin
from sae.profile import policy_cli

from ._synth_campaigns import build_campaigns
from .conftest import AT, PolicyEnv

Answer = Callable[[str], None]
BUILTIN = load_builtin().sha256


def actions(env: PolicyEnv) -> list[str]:
    return [RegistryRow.model_validate_json(x).action for x in env.registry.read_text().splitlines()]


def test_show_empty(penv: PolicyEnv) -> None:
    code, out, _ = penv.run("show")
    assert code == 0 and "current: " + BUILTIN[:8] in out and "approved: none" in out and "sealed queries: none" in out


def test_non_tty_refuses_before_prompt(penv: PolicyEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    sha = penv.policy(0.03)
    monkeypatch.setattr(builtins, "input", lambda _p="": pytest.fail("must not prompt without a TTY"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    src = penv.sae.parent / "p.json"
    src.write_bytes((penv.pdir / f"{sha}.json").read_bytes())
    (penv.pdir / f"{sha}.json").unlink()
    code, _, err = penv.run("propose", str(src))
    assert code == 2 and "interactive terminal" in err
    assert not penv.registry.exists() and not (penv.pdir / f"{sha}.json").exists()


def test_wrong_hex_writes_nothing(penv: PolicyEnv, tty: None, answer: Answer) -> None:
    sha = penv.policy(0.03)
    penv.chain(sha, 1)
    answer("deadbeef")
    code, _, err = penv.run("reject", sha)
    assert code == 2 and "does not match" in err
    assert actions(penv) == ["propose"]


def test_eof_at_prompt(penv: PolicyEnv, tty: None, monkeypatch: pytest.MonkeyPatch) -> None:
    sha = penv.policy(0.03)
    penv.chain(sha, 1)

    def eof(_p: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", eof)
    code, _, err = penv.run("reject", sha)
    assert code == 2 and "no confirmation" in err


def test_propose_copies_file(penv: PolicyEnv, tty: None, answer: Answer) -> None:
    sha = penv.policy(0.03)
    src = penv.sae.parent / "candidate.json"
    src.write_bytes((penv.pdir / f"{sha}.json").read_bytes())
    (penv.pdir / f"{sha}.json").unlink()
    answer(sha[:8])
    code, out, _ = penv.run("propose", str(src))
    assert code == 0 and "propose" in out
    assert (penv.pdir / f"{sha}.json").read_bytes() == src.read_bytes()
    row = RegistryRow.model_validate_json(penv.registry.read_text().splitlines()[0])
    assert row.action == "propose" and row.by.startswith("human:") and row.seq == 0
    code, _, err = penv.run("propose", str(src))
    assert code == 2 and "already proposed" in err


def test_propose_rejects_bad_files(penv: PolicyEnv, tty: None, answer: Answer) -> None:
    answer("00000000")
    bad = penv.sae.parent / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert penv.run("propose", str(bad))[0] == 2
    assert penv.run("propose", str(penv.sae.parent / "missing.json"))[0] == 2
    sha = penv.policy(0.03)
    liar = penv.sae.parent / ("a" * 64 + ".json")  # name looks like a hash but is not this policy's
    liar.write_bytes((penv.pdir / f"{sha}.json").read_bytes())
    code, _, err = penv.run("propose", str(liar))
    assert code == 2 and "does not match its hash" in err
    big = penv.sae.parent / "big.json"
    big.write_bytes(b" " * (policy_cli.MAX_POLICY_BYTES + 1))
    assert penv.run("propose", str(big))[0] == 2


def test_prereg_writes_content_addressed_evidence(penv: PolicyEnv, tty: None, answer: Answer) -> None:
    sha = penv.policy(0.03)
    penv.chain(sha, 1)
    answer(sha[:8])
    code, _, _ = penv.run("prereg", sha, "--metric", "loco_log_loss", "--min-effect", "0.1", "--margin", "0.01",
                          "--budget", "4", "--min-n", "2")
    assert code == 0
    row = RegistryRow.model_validate_json(penv.registry.read_text().splitlines()[1])
    assert row.action == "prereg" and row.evidence_sha256 is not None
    data = (penv.pdir / "evidence" / f"{row.evidence_sha256}.json").read_bytes()
    assert hashlib.sha256(data).hexdigest() == row.evidence_sha256
    pre = Preregistration.model_validate_json(data)
    assert (pre.candidate_sha256, pre.incumbent_sha256, pre.budget, pre.min_n) == (sha, BUILTIN, 4, 2)
    code, _, err = penv.run("prereg", sha, "--metric", "loco_log_loss")
    assert code == 2 and "already has a prereg" in err


def test_prereg_refusals(penv: PolicyEnv, tty: None, answer: Answer) -> None:
    sha = penv.policy(0.03)
    answer(sha[:8])
    assert "no propose row" in penv.run("prereg", sha)[2]
    penv.chain(sha, 1)
    code, _, err = penv.run("prereg", sha, "--metric", "loco_log_loss", "--budget", "1", "--min-n", "5")
    assert code == 2 and "invalid preregistration" in err
    code, _, err = penv.run("prereg", sha, "--metric", "nonsense")
    assert code == 2 and "invalid choice" in err


def test_prereg_default_metric_matches_contract(penv: PolicyEnv, tty: None, answer: Answer) -> None:
    sha = penv.policy(0.03)
    penv.chain(sha, 1)
    answer(sha[:8])
    assert penv.run("prereg", sha)[0] == 0
    row = RegistryRow.model_validate_json(penv.registry.read_text().splitlines()[-1])
    assert row.evidence_sha256 is not None
    data = (penv.pdir / "evidence" / f"{row.evidence_sha256}.json").read_bytes()
    assert Preregistration.model_validate_json(data).primary_metric == "loco_log_loss"


def test_commit_refuses_when_registry_changes_during_prompt(
    penv: PolicyEnv, tty: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = penv.policy(0.03)
    penv.chain(sha, 1)

    def racing_input(_p: str = "") -> str:
        penv.row("reject", sha, "human:other", reason="raced")
        return sha[:8]

    monkeypatch.setattr(builtins, "input", racing_input)
    before = penv.registry.read_text()
    code, _, err = penv.run("prereg", sha)
    assert code == 2 and "registry changed while waiting" in err
    assert len(penv.registry.read_text().splitlines()) == len(before.splitlines()) + 1  # only the racing row


def test_approve_needs_whole_chain(penv: PolicyEnv, tty: None, answer: Answer) -> None:
    sha = penv.policy(0.03)
    penv.chain(sha, 3)
    answer(sha[:8])
    assert "needs propose, prereg" in penv.run("approve", sha)[2]
    penv.row("stage2_pass", sha, "gate", evidence_sha256=penv.report(sha, 2))
    code, _, _ = penv.run("approve", sha)
    assert code == 0 and actions(penv)[-1] == "approve"
    assert penv.run("approve", sha)[0] == 2  # approve twice is out of chain


def test_reject_and_reason(penv: PolicyEnv, tty: None, answer: Answer) -> None:
    sha = penv.policy(0.03)
    answer(sha[:8])
    assert penv.run("reject", sha)[0] == 2  # unknown sha
    penv.chain(sha, 1)
    assert penv.run("reject", sha, "--reason", "worse on tune")[0] == 0
    assert RegistryRow.model_validate_json(penv.registry.read_text().splitlines()[-1]).reason == "worse on tune"
    assert penv.run("reject", sha, "--reason", "x" * 600)[0] == 2


def test_promote_refusals(penv: PolicyEnv, tty: None, answer: Answer) -> None:
    sha = penv.policy(0.03)
    penv.chain(sha, 4)
    answer(sha[:8])
    assert "no complete approved chain" in penv.run("promote", sha)[2]
    penv.row("approve", sha, "human:test")
    (penv.pdir / f"{sha}.json").unlink()
    assert "missing, invalid" in penv.run("promote", sha)[2]


def test_promote_pin_mismatch(penv: PolicyEnv, tty: None, answer: Answer) -> None:
    built = build_campaigns(penv.sae, penv.runs, mode="live")
    sha = penv.policy(0.03, pin="v3.0.*", campaign_ids=(built[0].campaign_id, "c-" + "9" * 16))
    penv.chain(sha)
    answer(sha[:8])
    code, _, err = penv.run("promote", sha)
    assert code == 2 and "does not match campaign version(s) ['v4.4.4']" in err
    assert "promote" not in actions(penv)
    ok = penv.policy(0.04, pin="v4.4.*", campaign_ids=(built[0].campaign_id,))
    penv.chain(ok)
    answer(ok[:8])
    assert penv.run("promote", ok)[0] == 0


def test_promote_rollback_pin_cycle(penv: PolicyEnv, tty: None, answer: Answer) -> None:
    a, b = penv.policy(0.03), penv.policy(0.04)
    penv.chain(a)
    penv.chain(b)
    answer(a[:8])
    assert penv.run("promote", a)[0] == 0
    assert "already current" in penv.run("promote", a)[2]
    answer(b[:8])
    assert penv.run("promote", b)[0] == 0
    assert penv.run("rollback", a)[0] == 2  # confirmation was b's first 8 hex
    answer(a[:8])
    assert penv.run("rollback")[0] == 0  # one step: b -> a
    last = RegistryRow.model_validate_json(penv.registry.read_text().splitlines()[-1])
    assert (last.action, last.sha256, last.previous_sha256) == ("rollback", a, b)
    assert "already current" in penv.run("rollback", a)[2]
    answer(BUILTIN[:8])
    code, out, _ = penv.run("pin", "builtin")
    assert code == 0 and 'policy.pin = "builtin"' in out
    last = RegistryRow.model_validate_json(penv.registry.read_text().splitlines()[-1])
    assert (last.sha256, last.previous_sha256, last.reason) == (BUILTIN, a, "pin builtin")
    code, out, _ = penv.run("pin", "builtin")  # already built-in: no row, hint again
    assert code == 0 and 'policy.pin = "builtin"' in out and actions(penv).count("rollback") == 2
    code, out, _ = penv.run("show")
    assert code == 0 and "current: " + BUILTIN[:8] in out and a[:8] in out


def test_rollback_without_history_or_target(penv: PolicyEnv, tty: None, answer: Answer) -> None:
    assert "nothing to roll back" in penv.run("rollback")[2]
    a = penv.policy(0.03)
    penv.chain(a)
    answer(a[:8])
    code, _, err = penv.run("rollback", a)  # never promoted
    assert code == 2 and "never promoted" in err


def test_rollback_to_builtin_when_first_promote(penv: PolicyEnv, tty: None, answer: Answer) -> None:
    a = penv.policy(0.03)
    penv.chain(a)
    answer(a[:8])
    assert penv.run("promote", a)[0] == 0
    answer(BUILTIN[:8])
    assert penv.run("rollback")[0] == 0  # previous_sha256 None -> built-in
    assert RegistryRow.model_validate_json(penv.registry.read_text().splitlines()[-1]).sha256 == BUILTIN


def test_show_lists_chain_and_ledger(penv: PolicyEnv, tty: None) -> None:
    sha = penv.policy(0.03)
    penv.chain(sha, 4, sealed=("sealed_noninferior:c-" + "7" * 16,))
    penv.row("stage2_fail", sha, "gate", evidence_sha256="d" * 64)  # unreadable evidence
    code, out, _ = penv.run("show")
    assert code == 0 and "propose > prereg > stage1_pass > stage2_pass > stage2_fail" in out
    assert "c-" + "7" * 16 + "=1" in out and "unreadable stage2 evidence: 1" in out


def test_corrupt_registry_exit_1(penv: PolicyEnv) -> None:
    penv.row("propose", "a" * 64, "tuner")
    gap = RegistryRow(seq=5, at=AT, action="propose", sha256="b" * 64, by="tuner")
    with penv.registry.open("a", encoding="utf-8") as fh:
        fh.write(gap.model_dump_json(exclude_none=True) + "\n")
    code, _, err = penv.run("show")
    assert code == 1 and "registry error" in err


def test_usage_and_config_errors(penv: PolicyEnv) -> None:
    assert policy_cli.main("policy", []) == 2
    assert policy_cli.main("policy", ["approve", "short"]) == 2
    assert policy_cli.main("policy", ["pin", "other"]) == 2
    assert policy_cli.main("policy", ["show", "--set", "bogus.key=1"]) == 3


def test_c10_guard_refuses_human_row_without_tty(penv: PolicyEnv, tty: None, answer: Answer,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    from sae.contracts import c10_profile

    sha = penv.policy(0.03)
    penv.chain(sha, 1)
    monkeypatch.setattr(c10_profile, "_is_tty", lambda _s: False)  # the CLI check passes, the C10 guard refuses
    answer(sha[:8])
    code, _, err = penv.run("reject", sha)
    assert code == 1 and "interactive TTY" in err and actions(penv) == ["propose"]


def test_interactive_check_survives_odd_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> bool:
        raise ValueError("closed file")

    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": staticmethod(boom)})())
    assert policy_cli._interactive() is False
