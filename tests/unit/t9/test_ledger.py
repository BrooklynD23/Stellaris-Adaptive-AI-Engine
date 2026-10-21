"""Sealed-query ledger and labelled-campaign loader (proposal 029 section 3, X4 plan 1.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sae.contracts.c6_adapter import Observation
from sae.profile import ledger, store
from sae.profile.index import campaign_state

from ._synth_campaigns import BuiltCampaign, build_campaigns
from .conftest import PolicyEnv


def sealed_name(cid: str) -> str:
    return ledger.SEALED_CHECK_PREFIX + cid


def test_empty_ledger(tmp_path: Path) -> None:
    q = ledger.sealed_query_counts(tmp_path / "nope")
    assert q.per_campaign == {} and q.unreadable == 0
    assert q.counts_for(["a"]) == {"a": 0}


def test_counts_stage2_rows_only(penv: PolicyEnv) -> None:
    sha = penv.policy(0.03)
    c1, c2 = "c-" + "1" * 16, "c-" + "2" * 16
    penv.row("propose", sha, "tuner")
    penv.row("stage1_pass", sha, "gate", evidence_sha256=penv.report(sha, 1, (sealed_name(c1),)))  # not stage 2
    ev = penv.report(sha, 2, (sealed_name(c1), sealed_name(c2), "detection_delay"))
    penv.row("stage2_pass", sha, "gate", evidence_sha256=ev)
    penv.row("stage2_fail", sha, "gate", evidence_sha256=penv.report(sha, 2, (sealed_name(c1),)))
    q = ledger.sealed_query_counts(penv.pdir)
    assert q.per_campaign == {c1: 2, c2: 1}
    assert q.unreadable == 0


def test_unreadable_evidence_counts_against_every_sealed(penv: PolicyEnv) -> None:
    sha = penv.policy(0.03)
    penv.row("stage2_pass", sha, "gate", evidence_sha256="e" * 64)  # file missing
    bad = penv.pdir / "evidence" / ("f" * 64 + ".json")
    store.write_atomic(bad, b"{not json")
    penv.row("stage2_fail", sha, "gate", evidence_sha256="f" * 64)
    q = ledger.sealed_query_counts(penv.pdir)
    assert q.unreadable == 2
    assert q.counts_for(["x", "y"]) == {"x": 2, "y": 2}


def test_registry_garbage_and_unreadable_file(penv: PolicyEnv) -> None:
    sha = penv.policy(0.03)
    penv.row("propose", sha, "tuner")
    with penv.registry.open("ab") as fh:
        fh.write(b"garbage line\n")
    assert ledger.sealed_query_counts(penv.pdir).unreadable == 0
    penv.registry.write_bytes(b"\xff\xfe\x00bad")
    assert ledger.sealed_query_counts(penv.pdir).unreadable == 1


def _env(tmp_path: Path, mode: str = "live", n: int = 6) -> tuple[Path, Path, list[BuiltCampaign]]:
    sae, runs = tmp_path / "sae", tmp_path / "runs"
    runs.mkdir()
    return sae, runs, build_campaigns(sae, runs, mode=mode, n_labelled=n)  # type: ignore[arg-type]


def test_split_loading(tmp_path: Path) -> None:
    sae, runs, built = _env(tmp_path)
    tune = ledger.labelled_campaigns(sae, "default", runs, "tune", False)
    sealed = ledger.labelled_campaigns(sae, "default", runs, "sealed", False)
    assert [c.state.campaign_id for c in tune] == [b.campaign_id for b in built[:5]]
    assert [c.state.campaign_id for c in sealed] == [built[5].campaign_id]
    assert built[6].campaign_id not in {c.state.campaign_id for c in tune + sealed}  # unlabelled
    for c in tune + sealed:
        assert c.state.status == "closed" and c.label.campaign_id == c.state.campaign_id
        assert len(c.observations) == 120
        assert all(type(o) is Observation for o in c.observations)


def test_synth_only_with_flag(tmp_path: Path) -> None:
    sae, runs, built = _env(tmp_path, mode="synth")
    assert ledger.labelled_campaigns(sae, "default", runs, "tune", False) == []
    got = ledger.labelled_campaigns(sae, "default", runs, "tune", True)
    assert len(got) == 5 and got[0].state.campaign_id == built[0].campaign_id


def test_excluded_and_bad_label_skipped(tmp_path: Path) -> None:
    sae, runs, built = _env(tmp_path)
    from sae.contracts.c10_profile import CampaignEvent

    store.append_events(
        sae / store.INDEX_FILE,
        [CampaignEvent(seq=0, at=store.now(), campaign_id=built[0].campaign_id, event="excluded")],
    )
    label = store.profile_dir(sae, "default") / "labels" / f"{built[1].campaign_id}.json"
    label.write_text("{}", encoding="utf-8")
    ids = [c.state.campaign_id for c in ledger.labelled_campaigns(sae, "default", runs, "tune", False)]
    assert ids == [b.campaign_id for b in built[2:5]]


def test_corrupt_index_raises(tmp_path: Path) -> None:
    sae, runs, _ = _env(tmp_path)
    index = sae / store.INDEX_FILE
    lines = index.read_text(encoding="utf-8").splitlines()
    index.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")
    with pytest.raises(store.IndexCorrupt):
        ledger.labelled_campaigns(sae, "default", runs, "tune", False)


def test_ledger_retires_a_queried_sealed_campaign(penv: PolicyEnv) -> None:
    sae, runs = penv.sae, penv.runs
    built = build_campaigns(sae, runs, mode="live", n_labelled=14)  # sealed: #6, #10, #14
    states = campaign_state(store.read_index(sae / store.INDEX_FILE)[0])
    assert states[built[5].campaign_id].split == "sealed"
    sha = penv.policy(0.03)
    ev = penv.report(sha, 2, (sealed_name(built[5].campaign_id),))
    for _ in range(10):
        penv.row("stage2_pass", sha, "gate", evidence_sha256=ev)
    sealed = ledger.labelled_campaigns(sae, "default", runs, "sealed", False)
    tune = ledger.labelled_campaigns(sae, "default", runs, "tune", False)
    assert built[5].campaign_id not in {c.state.campaign_id for c in sealed}
    assert built[5].campaign_id in {c.state.campaign_id for c in tune}


def test_build_campaigns_is_seeded_and_shifted(tmp_path: Path) -> None:
    a = build_campaigns(tmp_path / "a", _mk(tmp_path / "ra"), mode="live", seed=3)
    b = build_campaigns(tmp_path / "b", _mk(tmp_path / "rb"), mode="live", seed=3)
    assert [x.campaign_id for x in a] == [x.campaign_id for x in b]
    bus_a = (tmp_path / "ra" / a[0].run_id / "bus.jsonl").read_text()
    assert bus_a == (tmp_path / "rb" / b[0].run_id / "bus.jsonl").read_text()
    assert len(a[1].segments) == 2 and not a[6].labelled


def _mk(p: Path) -> Path:
    p.mkdir(parents=True)
    return p
