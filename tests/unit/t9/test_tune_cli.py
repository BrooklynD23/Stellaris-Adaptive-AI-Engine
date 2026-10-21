"""``sae tune detector`` through ``sae.tune.cli.main`` (paths under tmp_path; no game, no sealed access)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sae.contracts.c10_profile import PolicyVersion, RegistryRow, canonical_json, policy_sha256
from sae.core.policy import load_builtin
from sae.profile import ledger, store
from sae.tune import cli, fit

from ._synth_campaigns import build_campaigns
from .conftest import PolicyEnv

GRID = [fit.GridPoint(k, 0.02, 3.0, 0.45) for k in (3.0, 30.0)]
BUILTIN = load_builtin().sha256


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """No real process probe, no real nice(19) on the test process; ECE bound loosened (synth switcher is noisy)."""
    calls: list[str] = []
    monkeypatch.setattr(cli, "_game_running", lambda: False)
    monkeypatch.setattr(cli, "_lower_priority", lambda: calls.append("idle"))
    monkeypatch.setattr(fit, "ECE_MAX", 0.2)
    return calls


def tune(env: PolicyEnv, *args: str, grid: list[fit.GridPoint] | None = GRID) -> tuple[int, str, str]:
    env.capsys.readouterr()
    code = cli.main("tune", ["detector", *env.flags(), *args], grid=grid)
    out = env.capsys.readouterr()
    return code, out.out, out.err


def policies(env: PolicyEnv) -> list[Path]:
    return sorted(p for p in env.pdir.glob("*.json")) if env.pdir.exists() else []


def rows(env: PolicyEnv) -> list[RegistryRow]:
    return [RegistryRow.model_validate_json(x) for x in env.registry.read_text().splitlines()]


def test_success_live(penv: PolicyEnv, _quiet: list[str]) -> None:
    build_campaigns(penv.sae, penv.runs, "live", 6, True, seed=1)
    code, out, err = tune(penv)
    assert code == 0, err
    assert _quiet == ["idle"]
    (file,) = policies(penv)
    doc = PolicyVersion.model_validate_json(file.read_bytes())
    assert file.stem == policy_sha256(doc) and file.read_bytes() == canonical_json(doc)
    assert doc.parent_sha256 == BUILTIN and doc.fixed == load_builtin().doc.fixed
    t = doc.tuning
    assert (t.method, t.likelihood_source, t.kappa, t.tuner_version) == (
        "loco_shrinkage",
        "fitted_real",
        t.kappa,
        "0.1.0",
    )
    assert len(t.campaign_ids) == 5 and t.kappa in (3.0, 30.0)
    assert doc.notes is not None and "CUSUM: diagnostic only" in doc.notes and "V-3" in doc.notes
    assert "synth:" not in doc.notes and "synth:" not in out
    assert f"policy sha256: {file.stem}" in out and "ll=" in out
    (row,) = rows(penv)
    assert (row.action, row.by, row.sha256, row.seq) == ("propose", "tuner", file.stem, 0)
    events, _, _ = store.read_index(penv.sae / store.INDEX_FILE)
    assert doc.created_at == events[-1].at


def test_notes_use_round2_values_only(penv: PolicyEnv) -> None:
    build_campaigns(penv.sae, penv.runs, "live", 6, True, seed=1)
    assert tune(penv)[0] == 0
    doc = PolicyVersion.model_validate_json(policies(penv)[0].read_bytes())
    assert doc.notes is not None
    for part in doc.notes.split("; "):
        if " ll=" in part:
            vals = [w.split("=")[1] for w in part.split() if w.startswith(("ll=", "ece="))]
            assert all(len(v.replace("-", "").replace(".", "").lstrip("0").split("e")[0]) <= 2 for v in vals), part


def test_deterministic_same_sha_twice(penv: PolicyEnv, tmp_path: Path) -> None:
    build_campaigns(penv.sae, penv.runs, "live", 6, True, seed=1)
    assert tune(penv)[0] == 0
    first = policies(penv)[0].stem
    code, out, _ = tune(penv)  # same inputs: same sha, no second propose row
    assert code == 0 and f"policy sha256: {first}" in out and "already proposed" in out
    assert [p.stem for p in policies(penv)] == [first] and len(rows(penv)) == 1


def test_deterministic_across_fresh_dirs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shas = []
    for name in ("a", "b"):
        env = PolicyEnv(tmp_path / name, capsys)
        build_campaigns(env.sae, env.runs, "live", 6, True, seed=1)
        assert tune(env)[0] == 0
        shas.append(policies(env)[0].stem)
    assert shas[0] == shas[1]


def test_sealed_campaign_never_loaded(penv: PolicyEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    built = build_campaigns(penv.sae, penv.runs, "live", 6, True, seed=1)
    splits: list[object] = []
    loaded: list[str] = []
    real = ledger.labelled_campaigns

    def spy(*a: object, **k: object) -> list[ledger.LabelledCampaign]:
        splits.append(a[3] if len(a) > 3 else k.get("split"))
        got = real(*a, **k)  # type: ignore[arg-type]
        loaded.extend(c.state.campaign_id for c in got)
        return got

    monkeypatch.setattr(ledger, "labelled_campaigns", spy)
    assert tune(penv)[0] == 0
    assert splits == ["tune"]
    sealed = built[5].campaign_id
    assert sealed not in loaded
    doc = PolicyVersion.model_validate_json(policies(penv)[0].read_bytes())
    assert sealed not in doc.tuning.campaign_ids and len(loaded) == 5


def test_fewer_than_five_refused(penv: PolicyEnv) -> None:
    build_campaigns(penv.sae, penv.runs, "live", 4, True, seed=1)
    code, _, err = tune(penv)
    assert code == 2 and "need 5 labelled tune campaigns, have 4" in err
    assert policies(penv) == []


def test_synth_hidden_without_flag(penv: PolicyEnv) -> None:
    build_campaigns(penv.sae, penv.runs, "synth", 6, True, seed=1)
    code, _, err = tune(penv)
    assert code == 2 and "have 0" in err


def test_include_synth_labels_diagnostic_only(penv: PolicyEnv) -> None:
    build_campaigns(penv.sae, penv.runs, "synth", 6, True, seed=1)
    code, out, err = tune(penv, "--include-synth")
    assert code == 0, err
    doc = PolicyVersion.model_validate_json(policies(penv)[0].read_bytes())
    assert doc.tuning.likelihood_source == "synth_aligned"
    assert doc.notes is not None and cli.SYNTH_NOTE in doc.notes and cli.SYNTH_NOTE in out


def test_no_feasible_point_exit_1_nothing_written(penv: PolicyEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    build_campaigns(penv.sae, penv.runs, "live", 6, True, seed=1)
    monkeypatch.setattr(fit, "ECE_MAX", -1.0)
    code, out, err = tune(penv)
    assert code == 1 and "no feasible point" in err and "no feasible grid point" in out and "ece=" in out
    assert policies(penv) == [] and not penv.registry.exists()


def test_default_180_point_grid_with_v1_bound(penv: PolicyEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    build_campaigns(penv.sae, penv.runs, "live", 6, True, seed=1)
    monkeypatch.setattr(fit, "ECE_MAX", 0.10)  # the V-1 bound; seed 1 happens to be feasible
    code, _, err = tune(penv, grid=None)
    assert code == 0, err
    doc = PolicyVersion.model_validate_json(policies(penv)[0].read_bytes())
    assert doc.tuning.kappa in fit.KAPPAS and doc.detector.rho in fit.RHOS


def test_game_detected_refused_unless_force(
    penv: PolicyEnv, monkeypatch: pytest.MonkeyPatch, _quiet: list[str]
) -> None:
    build_campaigns(penv.sae, penv.runs, "live", 6, True, seed=1)
    monkeypatch.setattr(cli, "_game_running", lambda: True)
    code, _, err = tune(penv)
    assert code == 2 and "Stellaris" in err and policies(penv) == [] and _quiet == []
    code, _, err = tune(penv, "--force")
    assert code == 0, err


def test_parent_is_registry_current(penv: PolicyEnv, tty: None) -> None:
    build_campaigns(penv.sae, penv.runs, "live", 6, True, seed=1)
    sha = penv.policy(0.03)
    penv.chain(sha)
    penv.row("promote", sha, "human:test", previous_sha256=None)
    code, _, err = tune(penv)
    assert code == 0, err
    new = next(p for p in policies(penv) if p.stem != sha)
    doc = PolicyVersion.model_validate_json(new.read_bytes())
    assert doc.parent_sha256 == sha and doc.version == "0.3.0"
    assert rows(penv)[-1].action == "propose" and rows(penv)[-1].by == "tuner"


def test_current_policy_file_missing_refused(penv: PolicyEnv, tty: None) -> None:
    build_campaigns(penv.sae, penv.runs, "live", 6, True, seed=1)
    sha = penv.policy(0.03)
    penv.chain(sha)
    penv.row("promote", sha, "human:test", previous_sha256=None)
    (penv.pdir / f"{sha}.json").unlink()
    code, _, err = tune(penv)
    assert code == 2 and "missing" in err


def test_player_flag_selects_label_dir(penv: PolicyEnv) -> None:
    build_campaigns(penv.sae, penv.runs, "live", 6, True, seed=1, player_key="alice")
    assert tune(penv)[0] == 2  # default player has no labels
    assert tune(penv, "--player", "alice")[0] == 0


def test_usage_and_config_errors(penv: PolicyEnv, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main("tune", []) == 2
    assert cli.main("tune", ["detector", "--bogus"]) == 2
    capsys.readouterr()
    assert cli.main("tune", ["detector", "--set", "nonsense"]) == 3
    assert "invalid config" in capsys.readouterr().err


def test_corrupt_index_exit_1(penv: PolicyEnv) -> None:
    build_campaigns(penv.sae, penv.runs, "live", 6, True, seed=1)
    index = penv.sae / store.INDEX_FILE
    lines = index.read_text().splitlines()
    index.write_text("\n".join(lines[1:]) + "\n")  # seq now starts at 1: gap
    code, _, err = tune(penv)
    assert code == 1 and "corrupted" in err


def test_v3_counts_recorded_events(penv: PolicyEnv) -> None:
    built = build_campaigns(penv.sae, penv.runs, "live", 6, True, seed=1)
    lcs = ledger.labelled_campaigns(penv.sae, "default", penv.runs, "tune", False)
    line = cli._v3(lcs, penv.runs, load_builtin().doc)
    assert "recorded switches=0" in line and "rewards n=0" in line and "no signal" in line
    assert built  # fixture writes no strategy_selected/outcome events, so the replay count is the signal
    assert "replay switches=" in line and json.dumps(line)


def test_bump_version() -> None:
    assert cli._bump("0.1.0") == "0.2.0" and cli._bump("1.9.4") == "1.10.0"
