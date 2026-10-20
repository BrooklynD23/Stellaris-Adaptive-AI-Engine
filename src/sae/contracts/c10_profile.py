"""C10: campaign index, labels, profile, PolicyVersion, policy registry and gate evidence.

Semantics: ``contracts/C10_profile_policy.md`` (proposals 020 sections 1-2 and 021). Everything here is an
offline file under ``<sae_dir>`` (C8 section 3), never a bus event. The daemon may import this module (pydantic
and stdlib only) to load a policy read-only at start; it never writes any C10 file.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Annotated, Any, Final, Literal, get_args

from pydantic import AfterValidator, AwareDatetime, BaseModel, Field, model_validator

from sae.contracts.common import (
    COUNTER_TABLE,
    Archetype,
    EconPlan,
    FolderKey,
    Frozen,
    GameDate,
    Posture,
    ReasonKey,
    RunId,
    SemVer,
    Sha256Hex,
    Strategy,
)

SCHEMA_VERSION: Final = 1

#: ``c-`` + first 16 hex of sha256(folder_key + "|" + first run_id) (section 1).
CampaignId = Annotated[str, Field(pattern=r"^c-[0-9a-f]{16}$")]
PlayerKey = Annotated[str, Field(pattern=r"^[a-z0-9_]{1,32}$")]
Version = Annotated[str, Field(min_length=1, max_length=64)]
#: A finite float (no NaN/inf, so canonical JSON and hashes are well defined).
Real = Annotated[float, Field(allow_inf_nan=False)]
Prob = Annotated[float, Field(ge=0.0, le=1.0)]

ELIGIBLE_MIN_CAMPAIGNS: Final = 8  # section 2: profile eligibility
DRIFT_ALARM_MIN: Final = 3  # section 2: features outside the 90 % interval
HALF_LIFE_CAMPAIGNS: Final = 5.0  # section 2: decay weight 0.5 ** (age / 5)


def _complete(d: Mapping[Any, object], keys: Iterable[Any], what: str) -> None:
    missing = sorted(str(k) for k in set(keys) - set(d))
    if missing:
        raise ValueError(f"{what} lacks keys {missing}")


def _sums_to_one(d: Mapping[Any, float], what: str, tol: float) -> None:
    if abs(sum(d.values()) - 1.0) > tol:
        raise ValueError(f"{what} must sum to 1 (got {sum(d.values())})")


# --- Section 1: campaign index and labels ------------------------------------------------------------------


class CampaignEvent(Frozen):
    """One line of ``<sae_dir>/campaigns.jsonl`` (append-only; written by ``sae profile`` only)."""

    v: Literal[1] = 1
    seq: int = Field(ge=0, description="Gapless per file, from 0.")
    at: AwareDatetime
    campaign_id: CampaignId
    event: Literal["seen", "closed", "labelled", "excluded", "deleted"]
    folder_key: FolderKey | None = None
    start_date: GameDate | None = None
    last_date: GameDate | None = None
    run_ids: tuple[RunId, ...] = Field(default=(), max_length=1024)
    game_versions: tuple[Version, ...] = Field(default=(), max_length=16)
    policy_sha256: tuple[Sha256Hex, ...] = Field(default=(), max_length=64)
    label_sha256: Sha256Hex | None = None
    reason: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _fields_match_event(self) -> CampaignEvent:
        facts = (self.folder_key, self.start_date, self.last_date)
        if self.event == "seen":
            if None in facts or not self.run_ids:
                raise ValueError("a seen row needs folder_key, start_date, last_date and run_ids")
            if str(self.last_date) < str(self.start_date):
                raise ValueError("last_date before start_date")
        elif any(f is not None for f in facts) or self.run_ids or self.game_versions or self.policy_sha256:
            raise ValueError("only seen rows carry campaign facts")
        if (self.event == "labelled") != (self.label_sha256 is not None):
            raise ValueError("label_sha256 is required on labelled rows and forbidden elsewhere")
        return self


class LabelSegment(Frozen):
    """The player's archetype from ``from_date`` until the next segment."""

    from_date: GameDate
    archetype: Archetype


class CampaignLabel(Frozen):
    """Label manifest: ``profile/<key>/labels/<campaign_id>.json`` or ``<recording_dir>/label.json``."""

    schema_version: Literal[1] = SCHEMA_VERSION
    campaign_id: CampaignId | None = Field(description="None for a recording outside the index (synth).")
    source: Literal["human", "synth_schedule"]
    segments: tuple[LabelSegment, ...] = Field(min_length=1, max_length=16)
    labelled_at: AwareDatetime
    blind: bool = Field(description="True: the labeller was not shown SAE's beliefs.")
    note: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _increasing(self) -> CampaignLabel:
        dates = [s.from_date for s in self.segments]
        if any(a >= b for a, b in pairwise(dates)):
            raise ValueError("segments must have strictly increasing from_date")
        return self


# --- Section 2: campaign summary and profile ---------------------------------------------------------------

FeatureName = Literal[
    "expansion_rate_early",
    "expansion_rate_late",
    "border_fort_ratio",
    "first_war_year",
    "war_share",
    "fleet_power_cat_mean",
    "tech_cat_mean",
    "economy_cat_mean",
]
FEATURE_NAMES: Final[tuple[str, ...]] = get_args(FeatureName)


class SummaryFeature(Frozen):
    """One profile feature of one campaign (None = never observed, or never at war for first_war_year)."""

    value: Real | None
    n_obs: int = Field(ge=0)


class CampaignSummary(Frozen):
    """``profile/<key>/summaries/<campaign_id>.json`` (a rebuildable cache)."""

    schema_version: Literal[1] = SCHEMA_VERSION
    campaign_id: CampaignId
    builder_version: Version
    game_versions: tuple[Version, ...] = Field(max_length=16)
    policy_sha256: tuple[Sha256Hex, ...] = Field(max_length=64)
    start_date: GameDate
    last_date: GameDate
    features: dict[FeatureName, SummaryFeature]
    final_belief: dict[Archetype, Prob] | None

    @model_validator(mode="after")
    def _check(self) -> CampaignSummary:
        _complete(self.features, FEATURE_NAMES, "features")
        if self.final_belief is not None:
            _complete(self.final_belief, Archetype, "final_belief")
            _sums_to_one(self.final_belief, "final_belief", 1e-6)
        if self.last_date < self.start_date:
            raise ValueError("last_date before start_date")
        return self


class FeaturePosterior(Frozen):
    """Normal-Normal partial pooling of one feature (hyperprior and this player's posterior)."""

    hyper_mu: Real
    hyper_sd: float = Field(gt=0, allow_inf_nan=False)
    player_mu: Real
    player_sd: float = Field(gt=0, allow_inf_nan=False)
    n_eff: float = Field(ge=0, allow_inf_nan=False, description="Decayed campaign count, half-life 5 campaigns.")


class FoldScore(Frozen):
    """Rolling origin: fit campaigns 1..k, score campaign k+1."""

    k: int = Field(ge=1)
    log_score_delta: Real = Field(description="Profile minus hyperprior; > 0 = profile better.")


class ProfileDoc(Frozen):
    """``profile/<key>/profile.json``, rebuilt from scratch by every ``sae profile update``."""

    schema_version: Literal[1] = SCHEMA_VERSION
    player_key: PlayerKey
    mode: Literal["strict"]
    builder_version: Version
    built_at: AwareDatetime
    source_campaigns: tuple[CampaignId, ...] = Field(max_length=10_000)
    archetype_alpha: dict[Archetype, Annotated[float, Field(gt=0, allow_inf_nan=False)]]
    features: dict[FeatureName, FeaturePosterior]
    folds: tuple[FoldScore, ...] = Field(max_length=10_000)
    eligible: bool
    drift_alarm: bool
    drift_outside: int = Field(ge=0, le=len(FEATURE_NAMES))

    @model_validator(mode="after")
    def _derived_flags(self) -> ProfileDoc:
        _complete(self.archetype_alpha, Archetype, "archetype_alpha")
        _complete(self.features, FEATURE_NAMES, "features")
        mean = math.fsum(f.log_score_delta for f in self.folds) / len(self.folds) if self.folds else 0.0
        if self.eligible != (len(self.source_campaigns) >= ELIGIBLE_MIN_CAMPAIGNS and mean > 0):
            raise ValueError("eligible must equal: >= 8 source campaigns and mean fold log_score_delta > 0")
        if self.drift_alarm != (self.drift_outside >= DRIFT_ALARM_MIN):
            raise ValueError("drift_alarm must equal drift_outside >= 3")
        return self


# --- Section 4: PolicyVersion ------------------------------------------------------------------------------


def _check_cat5(v: tuple[float, ...]) -> tuple[float, ...]:
    if min(v) < 1e-4 or abs(math.fsum(v) - 1.0) > 1e-6:
        raise ValueError(f"categorical must have every p >= 1e-4 and sum 1: {v}")
    return v


Sd = Annotated[float, Field(gt=0, le=100)]
Gauss = tuple[Real, Sd]  # (mu, sigma)
Cat5 = Annotated[tuple[Prob, Prob, Prob, Prob, Prob], AfterValidator(_check_cat5)]  # p(-2..2)


class LikelihoodParams(Frozen):
    """Per-archetype likelihood tables (``sae.core.belief.LikelihoodModel``); all four archetypes required."""

    border: dict[Archetype, Gauss]
    expansion: dict[Archetype, Gauss]
    fleet: dict[Archetype, Cat5]
    tech: dict[Archetype, Cat5]
    war_now: dict[Archetype, Annotated[float, Field(gt=0, lt=1)]]

    @model_validator(mode="after")
    def _all_archetypes(self) -> LikelihoodParams:
        for name in ("border", "expansion", "fleet", "tech", "war_now"):
            _complete(getattr(self, name), Archetype, name)
        return self


class DetectorParams(Frozen):
    """The only mutable surface in v1 (bounds match C8 ``engine``)."""

    rho: float = Field(ge=0, le=0.2)
    likelihood_clip: float = Field(gt=0, le=10)
    belief_threshold: float = Field(ge=0.3, le=0.9)
    likelihood: LikelihoodParams


class TemplateSpec(Frozen):
    """One strategy template (``sae.core.expansion.Template``)."""

    econ: EconPlan
    posture: Posture
    reason_key: ReasonKey


class FixedParams(Frozen):
    """Copied verbatim from the parent and never tuned in v1; the counter table must equal C3 section 6."""

    counter_table: dict[Archetype, Strategy]
    templates: dict[Strategy, TemplateSpec]
    outcome_weights: dict[Annotated[str, Field(max_length=64)], Real] = Field(max_length=32)

    @model_validator(mode="after")
    def _c3_table(self) -> FixedParams:
        if self.counter_table != COUNTER_TABLE:
            raise ValueError("fixed.counter_table must equal the C3 section 6 counter table")
        _complete(self.templates, Strategy, "templates")
        return self


class TuningManifest(Frozen):
    """How a policy was produced."""

    method: Literal["builtin", "manual", "loco_shrinkage"]
    likelihood_source: Literal["synth_aligned", "fitted_real"]
    campaign_ids: tuple[CampaignId, ...] = Field(default=(), max_length=1024)
    synth_seeds: tuple[int, ...] = Field(default=(), max_length=1024)
    kappa: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    tuner_version: Version


class PolicyVersion(Frozen):
    """``<sae_dir>/policies/<sha256>.json`` or the built-in package file; identity = :func:`policy_sha256`."""

    schema_version: Literal[1] = SCHEMA_VERSION
    version: SemVer
    created_at: AwareDatetime
    game_version_pin: str = Field(pattern=r"^v\d+\.\d+\.(\d+|\*)$", description='e.g. "v4.4.*"')
    parent_sha256: Sha256Hex | None
    detector: DetectorParams
    fixed: FixedParams
    tuning: TuningManifest
    notes: str | None = Field(default=None, max_length=2048)


def canonical_json(model: BaseModel) -> bytes:
    """Sorted keys, ``(",", ":")`` separators, UTF-8, no NaN: the bytes C10 hashes."""
    return json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def policy_sha256(policy: PolicyVersion) -> str:
    """The policy's identity: sha256 of its canonical JSON (a policy file's name must equal it)."""
    return hashlib.sha256(canonical_json(policy)).hexdigest()


_GAME_VERSION = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


def pin_matches(pin: str, game_version: str) -> bool:
    """True if the first ``vX.Y.Z`` in ``game_version`` (C4 meta, e.g. ``Cepheus v4.4.4``) fits ``pin``."""
    found = _GAME_VERSION.search(game_version)
    if found is None:
        return False
    want = pin.removeprefix("v").split(".")
    return all(w in ("*", str(int(g))) for w, g in zip(want, found.groups(), strict=True))


# --- Section 5: registry -----------------------------------------------------------------------------------

RegistryAction = Literal[
    "propose", "prereg", "stage1_pass", "stage1_fail", "stage2_pass", "stage2_fail",
    "approve", "reject", "promote", "rollback",
]  # fmt: skip
CHAIN: Final[tuple[str, ...]] = ("propose", "prereg", "stage1_pass", "stage2_pass", "approve")
_HUMAN_ONLY = frozenset({"prereg", "approve", "reject", "promote", "rollback"})
_GATE_ONLY = frozenset({"stage1_pass", "stage1_fail", "stage2_pass", "stage2_fail"})
_NEEDS_EVIDENCE = _GATE_ONLY | {"prereg"}


class RegistryRow(Frozen):
    """One line of ``<sae_dir>/policies/registry.jsonl`` (append-only)."""

    v: Literal[1] = 1
    seq: int = Field(ge=0, description="Gapless per file, from 0.")
    at: AwareDatetime
    action: RegistryAction
    sha256: Sha256Hex = Field(description="The policy this row is about (rollback: the target).")
    by: str = Field(pattern=r"^(gate|tuner|human:[A-Za-z0-9_.-]{1,64})$")
    evidence_sha256: Sha256Hex | None = Field(default=None, description="GateReport or Preregistration file.")
    previous_sha256: Sha256Hex | None = Field(default=None, description="promote/rollback: pointer before this row.")
    reason: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _writer_matches_action(self) -> RegistryRow:
        human = self.by.startswith("human:")
        if self.action in _HUMAN_ONLY and not human:
            raise ValueError(f"{self.action} rows are human-only")
        if self.action in _GATE_ONLY and self.by != "gate":
            raise ValueError(f"{self.action} rows are written by the gate")
        if self.action == "propose" and self.by == "gate":
            raise ValueError("propose rows come from the tuner or a human")
        if (self.action in _NEEDS_EVIDENCE) != (self.evidence_sha256 is not None):
            raise ValueError("evidence_sha256 is required on prereg/stage rows and forbidden elsewhere")
        return self


class RegistryError(ValueError):
    """The registry is corrupted or inconsistent; the daemon uses the built-in policy (fallback_reason)."""


@dataclass(frozen=True)
class RegistryState:
    """Result of :func:`fold_registry`."""

    current: str | None  # None = nothing promoted: use the built-in policy
    approved: frozenset[str]  # shas with a complete, unbroken chain through ``approve`` (pinnable)


def fold_registry(rows: Sequence[RegistryRow], builtin_sha256: str) -> RegistryState:
    """Pure fold of registry rows (C10 section 5).

    A sha's chain is ``propose -> prereg -> stage1_pass -> stage2_pass -> approve`` in exactly that order; a
    chain row out of order, a ``*_fail`` or a ``reject`` breaks the sha for good. ``promote`` needs a complete
    unbroken chain; ``rollback`` targets the built-in sha or a sha promoted earlier; both must name the pointer
    they replace in ``previous_sha256``.

    Raises:
        RegistryError: seq gap/repeat, an invalid promote/rollback, or the current sha was broken later.
    """
    progress: dict[str, int] = {}
    broken: set[str] = set()
    promoted: set[str] = set()
    current: str | None = None
    for i, row in enumerate(rows):
        if row.seq != i:
            raise RegistryError(f"registry seq gap: line {i} has seq {row.seq}")
        sha = row.sha256
        if row.action in CHAIN:
            if CHAIN.index(row.action) == progress.get(sha, 0):
                progress[sha] = progress.get(sha, 0) + 1
            else:
                broken.add(sha)
        elif row.action in {"stage1_fail", "stage2_fail", "reject"}:
            broken.add(sha)
        else:
            if row.previous_sha256 != current:
                raise RegistryError(f"seq {row.seq}: previous_sha256 is not the current pointer")
            if row.action == "promote":
                if sha in broken or progress.get(sha, 0) < len(CHAIN):
                    raise RegistryError(f"seq {row.seq}: promote of {sha[:8]} without a valid chain")
                promoted.add(sha)
            elif sha != builtin_sha256 and sha not in promoted:
                raise RegistryError(f"seq {row.seq}: rollback target {sha[:8]} was never promoted")
            current = sha
    if current is not None and current in broken:
        raise RegistryError(f"current policy {current[:8]} was rejected or failed after promotion")
    approved = frozenset(s for s, n in progress.items() if n == len(CHAIN) and s not in broken)
    return RegistryState(current=current, approved=approved)


# --- Section 6: gate evidence ------------------------------------------------------------------------------


class Preregistration(Frozen):
    """Written by a human (``sae policy prereg``) before Stage 1; file ``policies/evidence/<sha256>.json``."""

    schema_version: Literal[1] = SCHEMA_VERSION
    candidate_sha256: Sha256Hex
    incumbent_sha256: Sha256Hex
    kind: Literal["detector_replay"]
    primary_metric: Literal["loco_log_loss", "detection_delay_months"]
    min_effect: Real
    non_inferiority_margin: float = Field(ge=0, allow_inf_nan=False)
    budget: int = Field(ge=1, le=1000)
    min_n: int = Field(ge=1)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _budget_covers_min_n(self) -> Preregistration:
        if self.budget < self.min_n:
            raise ValueError("budget < min_n: Stage 2 could never run")
        return self


class GateCheck(Frozen):
    """One named gate check."""

    name: str = Field(max_length=64)
    passed: bool
    value: Real | None = None
    detail: str | None = Field(default=None, max_length=512)


class GateReport(Frozen):
    """Output of ``sae gate stage1|stage2``; file ``policies/evidence/<sha256>.json``."""

    schema_version: Literal[1] = SCHEMA_VERSION
    stage: Literal[1, 2]
    candidate_sha256: Sha256Hex
    incumbent_sha256: Sha256Hex
    gate_version: Version
    checks: tuple[GateCheck, ...] = Field(min_length=1, max_length=256)
    passed: bool
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _passed_is_all(self) -> GateReport:
        if self.passed != all(c.passed for c in self.checks):
            raise ValueError("passed must equal all(check.passed)")
        return self
