# 019-T9 — Campaign identity on the bus, policy identity on `run_started` (+ deferred `priors_loaded`)

Status: PROPOSED (2026-10-19, T9 lead; revised after Fable proposal review B3, architecture review S1/S2 and
Fable plan review 1, 3, 7, 9, 13, 14). Part A is proposed now; **Part B is DEFERRED** until a profile is eligible
(≥ 8 closed real campaigns, proposal 020) and must be re-submitted then.
Reporter: T9. Contracts: C6 §2 (v1.2 → v1.3, two additive `Observation` fields); C5 §3 (v1.2 → v1.3,
`run_started` payload gains `policy`); C8 §5.1 (v1.2, `open_sink` keyword). Consumers: T2 (Observation fields),
T4 (resolves the policy, passes it to `open_sink`), T6 (`open_sink`, readers, console), T7 (bus checker, e2e),
T9 (offline campaign index). Depends on: 021 (PolicyVersion). Plan: `docs/expansion/player_profile_and_rsi_plan.md` §1, §6.

## Problem
1. Nothing on `bus.jsonl` names the campaign or the game version (S2).
2. No decision can be traced to the exact policy, so gate scores cannot be bound to a hash (S1).

## Part A (proposed now)

### C6 §2: two `Observation` fields

| Field | Source | Rule |
|---|---|---|
| `save_folder_key` | adapter | `"f-" + hmac_sha256(install_salt, <save folder name, UTF-8>).hexdigest()[:16]` of the latest parsed save's parent folder under `save_dir` (A-46). Synth: `"f-synth-" + 8 hex of sha256(run_id)` (unique per run, so synth runs never merge). `None` before the first parsed save. Carried forward like `player_legal`. |
| `game_version` | adapter | latest parsed save's `meta.game_version` (C4); `None` before the first parse; carried forward |

```python
save_folder_key: str | None = Field(default=None, pattern=r"^f-(synth-)?[0-9a-f]{8,16}$")
game_version: str | None = Field(default=None, max_length=64)
```

`install_salt`: 32 bytes from `secrets.token_bytes`, file `<sae_dir>/salt` (020), created with `O_CREAT|O_EXCL`;
a loser of the race re-reads the winner's file. The folder name never reaches any SAE file.

Campaign identity is decided offline (C10 §1, 020), not by the daemon.

### C5 §3: `run_started.policy`

```python
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]      # new alias, common.py

class PolicyRef(Frozen):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    sha256: Sha256Hex
    source: Literal["builtin", "registry", "pinned"]   # pin = "builtin" → "builtin"; pin = <sha> → "pinned"
    fallback_reason: str | None = Field(default=None, max_length=256)

class RunStartedPayload(Frozen):        # existing fields unchanged
    ...
    policy: PolicyRef | None = None     # None only on pre-v1.3 buses
```

### C8 §5.1 v1.2: `open_sink` keyword (T6)
`sae.bus.open_sink(run_dir, run_id, config, *, resume=False, policy: PolicyRef | None = None)`; wiring (T4)
resolves the policy (021) first and passes it. `--resume` re-resolves; a different sha → exit 1
("policy changed; start a new run").

### Determinism and existing tests
With the built-in policy, buses differ from today's only by the `run_started.policy` field. Tests that compare
whole buses drop that field; replay-determinism hashes also drop `save_folder_key` (salted per install).

## Part B (DEFERRED): `priors_loaded` and the contamination rule
Kept here so the design is not lost; not to be accepted now. When re-submitted:
- Event `priors_loaded` (fow `none`), emitted **only when `profile.mode != "off"`** (no `seq` shift otherwise);
  payload `profile` (player_key, mode `strict`, sha256, n_campaigns ≥ 8), `status`
  (`pending_campaign | applied | self_contaminated | invalid_profile`), `b0`, `overrides`, `detail`.
- Rule: designer b0 until the first observation with `save_folder_key`; folder ∈ `EnginePriors.source_folders` →
  `self_contaminated` for the whole run; else profile b0 before the first update; epoch reset → b0 in force.
- `--resume` never re-reads priors: it restores b0 and status from `state.json` and re-emits `priors_loaded`.

## Migration
Additive. C6 v1.3, C5 v1.3, C8 v1.2; `v = 1`, `schema_version = 1` stay (README §3.7). Old buses: no `policy`
(readers assume built-in 0.1.0), no folder key (campaign unknown).

## Follow-ups
architect: models, docs, examples, schemas, `Sha256Hex`. T2: assembler fields, salt. T4: wiring order. T6:
`open_sink` keyword, `v_runs` view with policy sha. T7: bus checker; whole-bus comparisons drop
`run_started.policy`. T8: A-46.

## UNVERIFIED
| Id | Assumption | M0 test | Fallback |
|---|---|---|---|
| A-46 | Each campaign's autosaves live in one folder under `save games/`; a folder name is reused only by a new game of the same empire name | M0-11 | Offline split rule (020) + `sae profile exclude` |
