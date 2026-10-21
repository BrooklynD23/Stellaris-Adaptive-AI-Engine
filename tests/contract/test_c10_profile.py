"""C10 models, canonical hashing, pin matching and registry fold (behaviour and error paths)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from sae.contracts.c10_profile import (
    FEATURE_NAMES,
    CampaignEvent,
    CampaignLabel,
    CampaignSummary,
    DetectorParams,
    FixedParams,
    GateCheck,
    GateReport,
    LikelihoodParams,
    PolicyVersion,
    Preregistration,
    ProfileDoc,
    RegistryError,
    RegistryRow,
    TemplateSpec,
    TuningManifest,
    canonical_json,
    fold_registry,
    pin_matches,
    policy_sha256,
)
from sae.contracts.common import COUNTER_TABLE, Archetype, EconPlan, Posture, Strategy

NOW = datetime(2026, 1, 1, tzinfo=UTC)
CID = "c-" + "a" * 16
FK = "f-" + "b" * 16
RUN = "20260101-000000-live-abcd"
H = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
BUILTIN = "9" * 64
ARCH = list(Archetype)


def _seen(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "seq": 0, "at": NOW, "campaign_id": CID, "event": "seen", "folder_key": FK,
        "start_date": "2200.01.01", "last_date": "2200.02.01", "run_ids": (RUN,),
    }  # fmt: skip
    return base | kw


class TestCampaignEvent:
    def test_valid_seen(self) -> None:
        assert CampaignEvent(**_seen()).event == "seen"

    @pytest.mark.parametrize("drop", ["folder_key", "start_date", "last_date", "run_ids"])
    def test_seen_needs_facts(self, drop: str) -> None:
        kw = _seen()
        kw[drop] = () if drop == "run_ids" else None
        with pytest.raises(ValidationError, match="seen row needs"):
            CampaignEvent(**kw)

    def test_seen_dates_ordered(self) -> None:
        with pytest.raises(ValidationError, match="last_date before start_date"):
            CampaignEvent(**_seen(start_date="2200.05.01", last_date="2200.01.01"))

    def test_non_seen_rejects_facts(self) -> None:
        with pytest.raises(ValidationError, match="only seen rows"):
            CampaignEvent(seq=1, at=NOW, campaign_id=CID, event="closed", folder_key=FK)

    def test_labelled_requires_hash_and_only_labelled(self) -> None:
        ok = CampaignEvent(seq=1, at=NOW, campaign_id=CID, event="labelled", label_sha256=H)
        assert ok.label_sha256 == H
        with pytest.raises(ValidationError, match="label_sha256"):
            CampaignEvent(seq=1, at=NOW, campaign_id=CID, event="labelled")
        with pytest.raises(ValidationError, match="label_sha256"):
            CampaignEvent(seq=1, at=NOW, campaign_id=CID, event="deleted", label_sha256=H)


class TestCampaignLabel:
    def _label(self, dates: list[str]) -> CampaignLabel:
        return CampaignLabel(
            campaign_id=None, source="synth_schedule", labelled_at=NOW, blind=True,
            segments=tuple({"from_date": d, "archetype": "turtle"} for d in dates),  # type: ignore[arg-type]
        )  # fmt: skip

    def test_increasing_ok(self) -> None:
        assert len(self._label(["2200.01.01", "2210.01.01"]).segments) == 2

    @pytest.mark.parametrize("dates", [["2200.01.01", "2200.01.01"], ["2210.01.01", "2200.01.01"]])
    def test_non_increasing_rejected(self, dates: list[str]) -> None:
        with pytest.raises(ValidationError, match="strictly increasing"):
            self._label(dates)


def _features() -> dict[str, dict[str, Any]]:
    return {n: {"value": 1.0, "n_obs": 3} for n in FEATURE_NAMES}


def _summary(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "campaign_id": CID, "builder_version": "1", "game_versions": (), "policy_sha256": (),
        "start_date": "2200.01.01", "last_date": "2200.06.01", "features": _features(),
        "final_belief": {a.value: 0.25 for a in ARCH},
    }  # fmt: skip
    return base | kw


class TestCampaignSummary:
    def test_valid_and_none_belief(self) -> None:
        assert CampaignSummary(**_summary()).final_belief is not None
        assert CampaignSummary(**_summary(final_belief=None)).final_belief is None

    def test_missing_feature(self) -> None:
        f = _features()
        del f["war_share"]
        with pytest.raises(ValidationError, match="war_share"):
            CampaignSummary(**_summary(features=f))

    def test_belief_incomplete(self) -> None:
        with pytest.raises(ValidationError, match="final_belief lacks"):
            CampaignSummary(**_summary(final_belief={"turtle": 1.0}))

    def test_belief_not_normalised(self) -> None:
        with pytest.raises(ValidationError, match="sum to 1"):
            CampaignSummary(**_summary(final_belief={a.value: 0.5 for a in ARCH}))

    def test_dates_ordered(self) -> None:
        with pytest.raises(ValidationError, match="last_date before start_date"):
            CampaignSummary(**_summary(start_date="2200.06.01", last_date="2200.01.01"))


def _posterior() -> dict[str, float]:
    return {"hyper_mu": 0.0, "hyper_sd": 1.0, "player_mu": 0.1, "player_sd": 0.5, "n_eff": 2.0}


def _profile(n_sources: int = 8, deltas: tuple[float, ...] = (0.5,), outside: int = 0, **kw: Any) -> dict[str, Any]:
    eligible = n_sources >= 8 and sum(deltas) / len(deltas) > 0 if deltas else False
    base: dict[str, Any] = {
        "player_key": "p1", "mode": "strict", "builder_version": "1", "built_at": NOW,
        "source_campaigns": tuple(f"c-{i:016x}" for i in range(n_sources)),
        "archetype_alpha": {a.value: 1.0 for a in ARCH},
        "features": {n: _posterior() for n in FEATURE_NAMES},
        "folds": tuple({"k": i + 1, "log_score_delta": d} for i, d in enumerate(deltas)),
        "eligible": eligible, "drift_alarm": outside >= 3, "drift_outside": outside,
    }  # fmt: skip
    return base | kw


class TestProfileDoc:
    def test_eligible_profile(self) -> None:
        assert ProfileDoc(**_profile()).eligible is True

    def test_no_folds_not_eligible(self) -> None:
        assert ProfileDoc(**_profile(deltas=())).eligible is False

    def test_wrong_eligible_flag(self) -> None:
        with pytest.raises(ValidationError, match="eligible must equal"):
            ProfileDoc(**_profile(eligible=False))

    def test_wrong_drift_flag(self) -> None:
        with pytest.raises(ValidationError, match="drift_alarm"):
            ProfileDoc(**_profile(outside=3, drift_alarm=False))
        assert ProfileDoc(**_profile(outside=3)).drift_alarm is True

    def test_missing_alpha_and_feature(self) -> None:
        with pytest.raises(ValidationError, match="archetype_alpha lacks"):
            ProfileDoc(**_profile(archetype_alpha={"turtle": 1.0}))
        feats = {n: _posterior() for n in FEATURE_NAMES[1:]}
        with pytest.raises(ValidationError, match="features lacks"):
            ProfileDoc(**_profile(features=feats))


def _likelihood() -> dict[str, Any]:
    g = {a.value: (0.0, 1.0) for a in ARCH}
    c = {a.value: (0.1, 0.2, 0.4, 0.2, 0.1) for a in ARCH}
    return {"border": g, "expansion": g, "fleet": c, "tech": c, "war_now": {a.value: 0.3 for a in ARCH}}


def _policy(**kw: Any) -> PolicyVersion:
    spec = {"econ": EconPlan.BALANCED, "posture": Posture.DEFENSIVE, "reason_key": "baseline"}
    base: dict[str, Any] = {
        "version": "1.0.0", "created_at": NOW, "game_version_pin": "v4.4.*", "parent_sha256": None,
        "detector": {"rho": 0.05, "likelihood_clip": 5, "belief_threshold": 0.6, "likelihood": _likelihood()},
        "fixed": {
            "counter_table": dict(COUNTER_TABLE),
            "templates": {s.value: spec for s in Strategy},
            "outcome_weights": {"w": 1.0},
        },
        "tuning": {"method": "builtin", "likelihood_source": "synth_aligned", "tuner_version": "1"},
    }  # fmt: skip
    return PolicyVersion(**(base | kw))


class TestPolicy:
    def test_templates_incomplete(self) -> None:
        with pytest.raises(ValidationError):
            TemplateSpec(econ=EconPlan.BALANCED, posture=Posture.DEFENSIVE, reason_key="nope")  # bad reason key
        with pytest.raises(ValidationError, match="templates lacks"):
            FixedParams(counter_table=dict(COUNTER_TABLE), templates={}, outcome_weights={})

    def test_counter_table_must_match_c3(self) -> None:
        bad = dict(COUNTER_TABLE) | {Archetype.TURTLE: Strategy.BASELINE}
        with pytest.raises(ValidationError, match="counter table"):
            FixedParams(counter_table=bad, templates={}, outcome_weights={})

    def test_likelihood_needs_all_archetypes(self) -> None:
        lk = _likelihood()
        del lk["war_now"]["turtle"]
        with pytest.raises(ValidationError, match="war_now lacks"):
            LikelihoodParams(**lk)

    @pytest.mark.parametrize("cat", [(0.5, 0.5, 0.0, 0.0, 0.0), (0.1, 0.1, 0.1, 0.1, 0.1)])
    def test_cat5_rejected(self, cat: tuple[float, ...]) -> None:
        lk = _likelihood()
        lk["fleet"]["turtle"] = cat
        with pytest.raises(ValidationError, match="categorical"):
            LikelihoodParams(**lk)

    def test_detector_bounds(self) -> None:
        with pytest.raises(ValidationError):
            DetectorParams(rho=0.5, likelihood_clip=5, belief_threshold=0.6, likelihood=_likelihood())  # type: ignore[arg-type]

    def test_tuning_kappa_nonneg(self) -> None:
        with pytest.raises(ValidationError):
            TuningManifest(method="manual", likelihood_source="fitted_real", tuner_version="1", kappa=-1.0)

    def test_canonical_json_and_hash_deterministic(self) -> None:
        p = _policy()
        raw = canonical_json(p)
        assert raw == canonical_json(_policy())
        assert b" " not in raw.split(b'"notes"')[0].replace(b"v4.4.*", b"")
        assert len(policy_sha256(p)) == 64
        assert policy_sha256(p) == policy_sha256(_policy())
        assert policy_sha256(p) != policy_sha256(_policy(version="1.0.1"))

    def test_pin_pattern(self) -> None:
        with pytest.raises(ValidationError):
            _policy(game_version_pin="4.4")


class TestPinMatches:
    @pytest.mark.parametrize(
        ("pin", "version", "expected"),
        [
            ("v4.4.*", "Cepheus v4.4.4", True),
            ("v4.4.4", "Cepheus v4.4.4", True),
            ("v4.4.4", "Cepheus v4.4.5", False),
            ("v4.3.*", "Cepheus v4.4.4", False),
            ("v4.4.*", "no version here", False),
        ],
    )
    def test_cases(self, pin: str, version: str, expected: bool) -> None:
        assert pin_matches(pin, version) is expected


def _row(seq: int, action: str, sha: str = H, by: str | None = None, **kw: Any) -> RegistryRow:
    if by is None:
        by = (
            "gate" if action.startswith("stage")
            else "tuner" if action == "propose" else "human:alice"
        )  # fmt: skip
    if action in ("prereg", "stage1_pass", "stage1_fail", "stage2_pass", "stage2_fail"):
        kw.setdefault("evidence_sha256", H3)
    return RegistryRow(seq=seq, at=NOW, action=action, sha256=sha, by=by, **kw)  # type: ignore[arg-type]


def _chain(sha: str = H, start: int = 0) -> list[RegistryRow]:
    acts = ["propose", "prereg", "stage1_pass", "stage2_pass", "approve"]
    return [_row(start + i, a, sha) for i, a in enumerate(acts)]


class TestRegistryRow:
    @pytest.mark.parametrize("action", ["prereg", "approve", "reject", "promote", "rollback"])
    def test_human_only(self, action: str) -> None:
        with pytest.raises(ValidationError, match="human-only"):
            _row(0, action, by="tuner", evidence_sha256=H3 if action == "prereg" else None)

    def test_gate_only(self) -> None:
        with pytest.raises(ValidationError, match="written by the gate"):
            _row(0, "stage1_pass", by="human:bob")

    def test_propose_not_gate(self) -> None:
        with pytest.raises(ValidationError, match="propose rows"):
            _row(0, "propose", by="gate")

    def test_evidence_rules(self) -> None:
        with pytest.raises(ValidationError, match="evidence_sha256"):
            _row(0, "stage1_pass", evidence_sha256=None)
        with pytest.raises(ValidationError, match="evidence_sha256"):
            _row(0, "propose", evidence_sha256=H3)


class TestFoldRegistry:
    def test_empty(self) -> None:
        st = fold_registry([], BUILTIN)
        assert st.current is None
        assert st.approved == frozenset()

    def test_full_chain_then_promote(self) -> None:
        rows = [*_chain(), _row(5, "promote", H, previous_sha256=None)]
        st = fold_registry(rows, BUILTIN)
        assert st.current == H
        assert st.approved == {H}

    def test_approved_without_promote(self) -> None:
        st = fold_registry(_chain(), BUILTIN)
        assert st.current is None
        assert st.approved == {H}

    def test_seq_gap(self) -> None:
        with pytest.raises(RegistryError, match="seq gap"):
            fold_registry([_row(1, "propose")], BUILTIN)

    def test_out_of_order_breaks_chain(self) -> None:
        rows = [_row(0, "propose"), _row(1, "stage1_pass"), _row(2, "prereg")]
        assert fold_registry(rows, BUILTIN).approved == frozenset()
        rows2 = [*rows, _row(3, "promote", H, previous_sha256=None)]
        with pytest.raises(RegistryError, match="without a valid chain"):
            fold_registry(rows2, BUILTIN)

    @pytest.mark.parametrize("breaker", ["stage1_fail", "stage2_fail", "reject"])
    def test_fail_breaks_for_good(self, breaker: str) -> None:
        rows = [*_chain(), _row(5, breaker, H)]
        assert fold_registry(rows, BUILTIN).approved == frozenset()

    def test_promote_incomplete_chain(self) -> None:
        rows = [_row(0, "propose"), _row(1, "promote", H, previous_sha256=None)]
        with pytest.raises(RegistryError, match="without a valid chain"):
            fold_registry(rows, BUILTIN)

    def test_promote_wrong_pointer(self) -> None:
        rows = [*_chain(), _row(5, "promote", H, previous_sha256=H2)]
        with pytest.raises(RegistryError, match="previous_sha256"):
            fold_registry(rows, BUILTIN)

    def test_rollback_to_builtin_and_to_earlier(self) -> None:
        rows = [
            *_chain(H), _row(5, "promote", H, previous_sha256=None),
            *_chain(H2, 6), _row(11, "promote", H2, previous_sha256=H),
            _row(12, "rollback", H, previous_sha256=H2),
            _row(13, "rollback", BUILTIN, previous_sha256=H),
        ]  # fmt: skip
        assert fold_registry(rows, BUILTIN).current == BUILTIN

    def test_rollback_never_promoted(self) -> None:
        rows = [_row(0, "rollback", H2, previous_sha256=None)]
        with pytest.raises(RegistryError, match="never promoted"):
            fold_registry(rows, BUILTIN)

    def test_current_broken_later(self) -> None:
        rows = [*_chain(), _row(5, "promote", H, previous_sha256=None), _row(6, "reject", H)]
        with pytest.raises(RegistryError, match="rejected or failed"):
            fold_registry(rows, BUILTIN)


class TestEvidence:
    def _prereg(self, **kw: Any) -> Preregistration:
        base: dict[str, Any] = {
            "candidate_sha256": H, "incumbent_sha256": H2, "kind": "detector_replay",
            "primary_metric": "loco_log_loss", "min_effect": 0.01, "non_inferiority_margin": 0.0,
            "budget": 10, "min_n": 5, "created_at": NOW,
        }  # fmt: skip
        return Preregistration(**(base | kw))

    def test_prereg_ok_and_equal_boundary(self) -> None:
        assert self._prereg().budget == 10
        assert self._prereg(budget=5).min_n == 5

    def test_budget_below_min_n(self) -> None:
        with pytest.raises(ValidationError, match="budget < min_n"):
            self._prereg(budget=4)

    def _report(self, passed: bool, checks: list[bool]) -> GateReport:
        return GateReport(
            stage=1, candidate_sha256=H, incumbent_sha256=H2, gate_version="1",
            checks=tuple(GateCheck(name=f"c{i}", passed=p) for i, p in enumerate(checks)),
            passed=passed, created_at=NOW,
        )  # fmt: skip

    def test_gate_report_passed_is_all(self) -> None:
        assert self._report(True, [True, True]).passed is True
        assert self._report(False, [True, False]).passed is False
        with pytest.raises(ValidationError, match=r"all\(check.passed\)"):
            self._report(True, [True, False])
