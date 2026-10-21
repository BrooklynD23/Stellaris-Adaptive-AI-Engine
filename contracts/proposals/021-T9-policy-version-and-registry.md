# 021-T9 — PolicyVersion file, append-only registry, gate evidence (C10 §4–§6; C8 v1.2)

Status: **ACCEPTED-WITH-CHANGES** as C10 v1 §4–§6 + C8 v1.2 + C5 v1.3 (2026-10-19, architect, WU-1; changes W1-1..W1-5, W1-9, W1-10 in `contracts/CHANGES.md`; policies personal only, maintainer D4).
Original: PROPOSED (2026-10-19, T9 lead). Reporter: T9. Contracts: new C10 §4–§6 (with proposal 020);
C8 §2 (`engine.rho`, `engine.likelihood_clip`, `engine.belief_threshold` defaults become `null` = from policy;
new `policy` section). Consumers: T4 (loads policy read-only at start), T7 (gate writes evidence rows),
T9 (tuner writes candidates), T6 (views), T8 (docs). Plan: `docs/expansion/player_profile_and_rsi_plan.md` §5.

## Problem
Settled: runtime self-modification is OFF; only validated, versioned policies ship and a human approves
each. Today the detector parameters are hard-coded HYP defaults (`src/sae/core/belief.py:29`
`LikelihoodModel`) plus three `engine.*` config keys. There is no artefact to version, hash, gate or roll back.

## Decision
1. A **PolicyVersion** is one JSON file holding every behaviour constant that is not a contract rule:
   detector parameters (`detector`, the **only mutable surface** in v1) plus the counter table, strategy
   templates and outcome weights (`fixed`, today's code constants `COUNTER_TABLE`, `DEFAULT_EXPANSIONS`,
   `outcome.WEIGHTS`), so the hash identifies the whole policy (architecture review S1). The gate rejects any
   candidate whose `fixed` differs from its parent; the loader rejects a `fixed.counter_table` that differs from
   C3 §6. Commitment months, safety floor, intent vocabulary, validator rules and fog code stay in code and
   contracts (immutable surfaces; normal review and the change protocol only).
2. Its identity is `sha256` of its canonical JSON (sorted keys, `separators=(",", ":")`, UTF-8, LF-free).
3. A per-user **registry** (`<sae_dir>/policies/registry.jsonl`) is an append-only log. "Current" is a pure fold.
4. The daemon resolves the policy once at start: `policy.pin` (`"builtin"` → source `builtin`, a sha → `pinned`) → registry current (`registry`) → built-in. It verifies
   hash, game-version pin and the approval chain; on any failure it uses the built-in policy and says so in
   `priors_loaded` (proposal 019). It never writes a policy file and never switches policy during a run.
5. The built-in policy `0.1.0` is today's `LikelihoodModel()` defaults, `engine` defaults, `COUNTER_TABLE`,
   `DEFAULT_EXPANSIONS` and `WEIGHTS`, serialised and shipped as package data (`src/sae/policies/builtin-0.1.0.json`,
   `likelihood_source = "synth_aligned"`), so existing behaviour is bit-identical. A test asserts the file equals the
   serialised code constants until T4 deletes the constants in favour of the loaded policy.

## C10 §4 — PolicyVersion

```python
Mu = float
Sd = Annotated[float, Field(gt=0, le=100)]
Cat5 = tuple[float, float, float, float, float]          # p(-2..2); validator: each ≥ 1e-4, sum 1 ± 1e-6

class LikelihoodParams(Frozen):
    border: dict[Archetype, tuple[Mu, Sd]]
    expansion: dict[Archetype, tuple[Mu, Sd]]
    fleet: dict[Archetype, Cat5]
    tech: dict[Archetype, Cat5]
    war_now: dict[Archetype, Annotated[float, Field(gt=0, lt=1)]]

class DetectorParams(Frozen):
    rho: float = Field(ge=0, le=0.5)
    likelihood_clip: float = Field(gt=0, le=10)
    belief_threshold: float = Field(ge=0.3, le=0.9)
    likelihood: LikelihoodParams

class TemplateSpec(Frozen):
    econ: EconPlan
    posture: Posture
    reason_key: ReasonKey

class FixedParams(Frozen):                    # v1: copied verbatim from parent; never tuned
    counter_table: dict[Archetype, Strategy]  # must equal C3 §6
    templates: dict[Strategy, TemplateSpec]
    outcome_weights: dict[str, float]         # keys of outcome metrics (src/sae/core/outcome.py), HYP

class TuningManifest(Frozen):
    method: Literal["builtin", "manual", "loco_shrinkage"]
    likelihood_source: Literal["synth_aligned", "fitted_real"]      # builtin 0.1.0 = synth_aligned (arch. review §3a)
    campaign_ids: tuple[CampaignId, ...] = Field(max_length=1024)   # tune split only; gate rejects any sealed id
    synth_seeds: tuple[int, ...] = Field(max_length=1024)
    kappa: float | None = Field(default=None, ge=0)                   # shrinkage strength toward parent
    tuner_version: str

class PolicyVersion(Frozen):
    schema_version: Literal[1] = 1
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    created_at: AwareDatetime
    game_version_pin: str = Field(pattern=r"^v\d+\.\d+\.(\d+|\*)$")  # e.g. "v4.4.*"
    parent_sha256: Sha256Hex | None
    detector: DetectorParams
    fixed: FixedParams
    tuning: TuningManifest
    notes: str | None = Field(default=None, max_length=2048)
```

Files: `<sae_dir>/policies/<sha256>.json` (content-addressed; the file name must equal its hash) and the
built-in under package data.

## C10 §5 — Registry (`<sae_dir>/policies/registry.jsonl`)

```python
class RegistryRow(Frozen):
    v: Literal[1] = 1
    seq: int = Field(ge=0)                    # gapless
    at: AwareDatetime
    action: Literal["propose", "prereg", "stage1_pass", "stage1_fail", "stage2_pass", "stage2_fail",
                    "approve", "reject", "promote", "rollback"]
    sha256: Sha256Hex                         # the policy this row is about
    by: str = Field(pattern=r"^(gate|tuner|human:[A-Za-z0-9_.-]{1,64})$")
    evidence_sha256: Sha256Hex | None = None  # GateReport (§6) or Preregistration file
    previous_sha256: Sha256Hex | None = None  # promote/rollback: the pointer before this row
    reason: str | None = Field(default=None, max_length=512)
```

Fold `current(rows) -> sha256 | None` (one pointer; no channels in v1), valid only if, for that sha, in order:
`propose` → `prereg` → `stage1_pass` → `stage2_pass` → `approve` (by `human:*`, after `stage2_pass`) →
`promote`. A `rollback` row's `sha256` is the target (a sha that was promoted earlier with a valid chain, or
the built-in's sha) and its `previous_sha256` is the pointer it replaces. Any `*_fail` or `reject` after `propose` makes that sha unpromotable. Who writes:

| action | writer |
|---|---|
| propose | tuner (`sae tune …`, T9) or a human |
| stage1_*, stage2_* | gate (`sae gate …`, T7) |
| prereg, approve, reject, promote, rollback | human only: `sae policy prereg|approve|reject|promote|rollback`, interactive TTY, asks the user to type the first 8 hex of the sha (Fable plan review 6: an agent must not set its own margin) |

All writers append through one helper, `c10_profile.append_registry_row(path, row)`: exclusive lock file
`registry.jsonl.lock`, re-read last `seq`, write `seq+1` with flush+fsync. Readers skip and count invalid lines
(C5 §5 rule) and treat a `seq` gap as a corrupted registry → built-in policy.

Rollback is one row and takes effect at the next daemon start. `sae policy pin builtin` is the emergency exit.

## C10 §6 — Gate evidence

```python
class Preregistration(Frozen):
    schema_version: Literal[1] = 1
    candidate_sha256: Sha256Hex
    incumbent_sha256: Sha256Hex
    kind: Literal["detector_replay"]          # v1 only; game-paired candidates need their own proposal after M4
    primary_metric: Literal["loco_log_loss", "detection_delay_months"]
    min_effect: float                         # Stage 1 step 6: candidate must beat the incumbent's LOCO value by ≥ min_effect
    non_inferiority_margin: float = Field(ge=0)
    budget: int = Field(ge=1, le=1000)        # replay sessions or paired runs
    min_n: int = Field(ge=1)                  # gate refuses to start Stage 2 if budget < min_n
    created_at: AwareDatetime

class GateCheck(Frozen):
    name: str = Field(max_length=64)          # e.g. "ci_suite", "fog_perturbation", "sealed_split"
    passed: bool
    value: float | None = None
    detail: str | None = Field(default=None, max_length=512)

class GateReport(Frozen):
    schema_version: Literal[1] = 1
    stage: Literal[1, 2]
    candidate_sha256: Sha256Hex
    incumbent_sha256: Sha256Hex
    gate_version: str
    checks: tuple[GateCheck, ...]
    passed: bool                              # all checks passed
    created_at: AwareDatetime
```

Reports live at `<sae_dir>/policies/evidence/<sha256>.json`. Sealed-split checks report `passed` and a value
rounded to 2 significant digits only (limits adaptive overfitting of the holdout; plan §4).

## C8 v1.2 additions

| Section | Key (default) | Rule |
|---|---|---|
| `engine` | `rho`, `likelihood_clip`, `belief_threshold` (`null`) | `null` = take from the policy; a number overrides it and is listed in `priors_loaded.overrides` |
| `policy` | `pin` (`null`) | `null`, `"builtin"` or a sha256 |

## Not in v1 (said so to keep the surface small)
Template θ, counter-table entries and code changes are not policy content. They need M4 evidence and a C3
proposal first (plan §5.5). No signatures: hashes plus the human-only rows are enough for one machine;
distribution to other players needs a signing decision (plan §6, maintainer).

## Migration
Additive except the three `engine` defaults (`number` → `null`): behaviour is identical because the built-in
policy carries the old numbers; C8 v1.2. The daemon import graph gains only `sae.contracts.c10_profile`
(pydantic, stdlib).

## Follow-ups
architect: C10 doc + models + schemas; package data entry in `pyproject.toml`. T4: `src/sae/daemon/policy_load.py`
(resolve, verify, build `LikelihoodModel` from `DetectorParams`), `belief.update` takes the model from state.
T7: `sae gate` (stage 1 and 2 runners) under `src/sae/bench/gate/`. T9: `sae policy` and `sae tune` CLIs.
T8: docs.
