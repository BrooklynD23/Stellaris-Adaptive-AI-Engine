# C3 — Intent schema v1 and mapping to C2

| | |
|---|---|
| Version | 1.2 (`schema_version = 1`, written to the game as `sae_p_schema = 1`) |
| Status | FROZEN v1.2 (2026-10-19; v1.1 minus `FLEET_COMPOSITION_BIAS`, maintainer decision; v1.2 audit rule id `deferred`, proposal 018-T4) |
| Owner | architect (contract) |
| Producers | Engine strategy expansion (T4); operator via C9 `force_strategy` (indirectly) |
| Consumers | Validator and compiler (T4); LiveAdapter `emit` (T2); bus `intent_emitted` (T6); mod `sae.0` (T1) via C2 |
| Code | `src/sae/contracts/c3_intent.py` — intent models, `IntentBatch`, `compile_batch`; enums in `common.py` |

## 1. Pipeline

`strategy → intents (T4) → validator (T4, §4) → IntentBatch → compile_batch (§3) → render_ini (C2) → atomic write (T2) → player runs file → mod applies → ACK (C1) → intent_ack (C5)`.
No planner, operator or future LLM ever writes console text. Intents are enums and bounded
integers only.

## 2. Vocabulary (advisor-reconciled)

Every intent has: `schema_version` (1), `intent_id` (`i-NNNNNN`, unique per run), `decision_id`
(`d-YYYY.MM-NN`), `country` (managed AI `sid`), `ttl_months` (1–36, default 3), `reason_key`
(optional), `source` (`engine` | `operator`), and a `kind`:

| kind | Parameters | C2 variables | Status |
|---|---|---|---|
| `SET_ECON_PLAN` | `plan` ∈ balanced, military, research, expansion | `sae_p_econ` = 1..4 | flag-gating verified (StarNet); efficacy A-21 |
| `SET_MILITARY_POSTURE` | `posture` ∈ defensive, deterrent, offensive; `target` (sid, required) | `sae_p_mil` = 1..3, `sae_p_mil_target` | opinion modifier A-23 |
| `DIPLO_STANCE` | `stance` ∈ friendly, neutral, rival, hostile; `target` (sid) | `sae_p_dip` = 1..4, `sae_p_dip_target` | weight hooks A-23 |
| `NOOP_HEARTBEAT` | — | header variables only | bridge liveness test |
| `PRIORITIZE_TECH` | reserved | — | **deferred**: no live research hook; validator rejects (`reserved_kind`) |
| `FLEET_COMPOSITION_BIAS` | reserved | (`sae_p_ship_<size>`, always -1) | **dropped from v0** (maintainer 2026-10-19): no AI script reads the per-size knob (A-22). Validator rejects (`reserved_kind`). Re-entry: M0-7 proves an AI hook → proposal restoring the v1 kind (weights 0..100 per size, 50 = neutral) |

Integer codes are declaration order (1-based) and are frozen: `EconPlan`, `Posture`, `Stance`,
`ReasonKey` in `common.py`. `ReasonKey` codes: 1 turtle_siege, 2 tech_rush_punish, 3 aggression_hold,
4 baseline, 5 operator_forced, 6 safety_floor; 0 = none. Localisation key: `sae_reason_<value>`.

Role-based fleet composition (artillery, carrier, ...) is cut (advisor); per-size bias is dropped
until M0-7 (above).

## 3. Mapping to C2 (`compile_batch`)

1. Group the batch's intents by `country`; one C2 country line per country, ascending `sid`.
2. `sae_seq_pending` = `batch.seq` on every line. `sae_p_schema` = 1.
3. `sae_p_expires` = `batch.issued_m + min(ttl_months of that country's intents)`.
4. `sae_p_reason` = the code of the first intent (batch order) for that country with a `reason_key`, else 0.
5. Each intent kind sets only its own variables (table above); all others are -1 (keep). The five
   reserved `sae_p_ship_<size>` slots are always -1.
6. `batch.knobs` (Governor/preset) becomes the C2 knob line.

**Desired-state rule (T4 obligation).** A batch is not a diff. For every country it names, the
engine includes intents restating **every** knob it currently wants non-default for that country
(current econ plan, posture + target, stance). Therefore any single applied batch brings
the country to the full desired state, and an overwritten (superseded) file loses nothing.

Constraints enforced by `IntentBatch`: ≤ 1 intent per (country, kind); unique `intent_id`s;
at least one intent or a knob change. Enforced by `CountryCommand`: no self-target, target ≠ 0.

## 4. Validator rules (T4 implements; rule ids are C5 `RejectRule`)

Applied in this order; the first failing rule rejects the intent, which is logged as
`intent_rejected` and never written.

| Rule id | Rejects when |
|---|---|
| `schema` | The intent does not validate against this contract. |
| `reserved_kind` | `kind` is reserved (`PRIORITIZE_TECH`, `FLEET_COMPOSITION_BIAS`). |
| `mode` | Adapter mode is `release` (no live input; Ironman). (At level S no intents are generated at all.) |
| `not_managed` | `country` is not in the latest `Observation.managed`. |
| `bad_target` | `target` is the country itself, or is not the player's `sid` (v0 models only the player). |
| `not_in_contact` | Posture/stance toward the player while that country's `LegalView.in_contact` is false (fairness: no intent may reference an unobserved entity). |
| `safety_floor` | Any fleet-bias weight < `engine.safety_floor_ship_min` (default 10). No v1.1 kind can fail it (fleet bias dropped); the rule id stays for re-entry. |
| `rate_limit` | More than one batch per game month, or more than `engine.max_managed` countries in a batch. Re-emission after LOST is exempt from the per-month limit. |

Non-validator audit id (proposal 018-T4, v1.2): **`deferred`**. The engine held a proposed intent back
without refusing it and re-proposes it later. Every proposed-but-unemitted intent is audited
(design report), so each hold is one `intent_rejected` with rule `deferred` and a detail starting
`deferred: `: `deferred: no HB month counter yet (m=None); re-sent when it arrives` or
`deferred: one batch per game month; re-sent next month`. `rate_limit` keeps meaning a refusal
(e.g. more than `engine.max_managed` countries). Readers count `deferred` as an audit row, not a
validator failure.

Raw (non-`Intent`) input, e.g. a dict from a replay or a test: `reserved_kind` is checked **before**
`schema` (reserved kinds are not in the `Intent` union, so it would otherwise always fail as `schema`).
A `schema` or `reserved_kind` rejection of raw input has no valid `Intent` to put in `intent_rejected`;
it is logged as `health` `kind=invalid_event`, `component=engine`, with the rule id in `detail`. In v0
the engine only generates valid `Intent`s, so this path has no runtime effect (proposal 005-T4).

## 5. Lifecycle and acks

Unit of acknowledgement: one **(country, seq)** pair. All intents of that country in that batch share
its fate.

| Status | Set by | Rule |
|---|---|---|
| `APPLIED`, `REJECTED`, `STALE`, `DUPLICATE`, `SCHEMA_MISMATCH` | mod ACK line (C1) | terminal |
| `SUPERSEDED` | engine | an ACK with a **higher** seq arrived for the same country and none arrived for this seq (`code=NEWER_BATCH`) |
| `LOST` | engine | no ACK for this (country, seq) and no higher-seq ACK for the country within `ACK_TIMEOUT_MONTHS = 2` game months of `issued_date` (`code=TIMEOUT`) |

On `LOST` with `attempt = 1`: if no newer batch for that country exists, re-emit the same intents
(same `intent_id`s) once in a new batch with a new `seq` and `attempt = 2`. A second `LOST` raises
`health` `ack_alarm` ("press the run hotkey" in hotkey-only v0) and stops re-emission of that batch.
The engine keeps deciding and writing new batches as usual (each restates the desired state, so one
hotkey press catches up); `ack_alarm` repeats at most once per 12 game months while no ACK arrives.
A mod ACK that arrives after the engine marked `LOST`/`SUPERSEDED` is still recorded as its own
`intent_ack` event; consumers take the **last** event per (country, seq).
`seq` is strictly increasing per run. **Seq floor rule:** on start (sources: `state.json` for
`--resume`, C8 §6) and on every observation and ACK, if any `ManagedState.applied_seq`,
`ManagedState.pending_seq` or ACK `seq` is ≥ the next seq, the next seq becomes that maximum + 1. A
`DUPLICATE/SEQ_OLD` ACK carries `applied` (the mod's `sae_seq_applied`); next seq becomes
`max(next, applied + 1)`, and the engine re-emits the current desired state in a
new batch. So a reloaded save or a different campaign loaded mid-run cannot leave new batches looking old.

## 6. Counter table and safety floor (v0/v1, settled)

| Top archetype (posterior ≥ `belief_threshold`, default 0.45) | Strategy | reason_key | Default expansion per managed AI in contact |
|---|---|---|---|
| turtle | `outscale_siege` | turtle_siege | econ military; posture offensive → player |
| tech_rush | `early_punish` | tech_rush_punish | econ military; posture offensive → player |
| early_aggression | `hold_counterpunch` | aggression_hold | econ military; posture defensive → player |
| balanced, or below threshold | `baseline` | baseline | econ balanced; posture deterrent → player |

Fleet-bias columns (turtle: cruiser 70, battleship 80, corvette 20; others) are removed with the kind
(v1.1).

**Maintainer decision (2026-10-19): keep both counters in v0.** `outscale_siege` (turtle counter) and
`early_punish` (builder/tech-rush counter) both stay in the strategy set. In v0 they write identical
game values (econ military; posture offensive → player) and differ only in `reason_key`, i.e. the
`sae_p_reason` flavour (C2). Their behavioural split returns with the hull mix after M0-7 (A-22), via
the C3 v1.2 fleet-bias proposal (§8). T4 must not merge them, and a strategy change between the two
is still a strategy change (new commitment window, new `reason_key`).

The expansion numbers are T4 defaults (configurable, not part of the frozen schema). Every
strategy is held for `engine.commitment_months` (24); the reward window equals the commitment window.
Safety floor (a rule, not an LCB): (1) every SAE budget/subplan entry in the mod is **additive**, so no
knob can push a budget share below vanilla baseline; (2) the `safety_floor` validator rule;
(3) below the belief threshold the engine runs `baseline`. No intent adds resources.

## 7. Examples

The batch below compiles to the first C2 example (`compiled-from=batch-17`):

```json model=IntentBatch id=batch-17
{
  "seq": 17,
  "issued_date": "2231.04.01",
  "issued_m": 375,
  "knobs": null,
  "intents": [
    {"intent_id": "i-000381", "decision_id": "d-2231.04-07", "country": 3, "kind": "SET_ECON_PLAN",
     "plan": "military", "ttl_months": 3, "reason_key": "turtle_siege"},
    {"intent_id": "i-000382", "decision_id": "d-2231.04-07", "country": 3, "kind": "SET_MILITARY_POSTURE",
     "posture": "offensive", "target": 1, "ttl_months": 3, "reason_key": "turtle_siege"},
    {"intent_id": "i-000384", "decision_id": "d-2231.04-07", "country": 4, "kind": "NOOP_HEARTBEAT",
     "ttl_months": 2}
  ]
}
```

A single operator intent:

```json model=Intent
{"intent_id": "i-000400", "decision_id": "d-2232.01-01", "country": 5, "kind": "DIPLO_STANCE",
 "stance": "rival", "target": 1, "source": "operator"}
```

A knob-only batch (Governor H3):

```json model=IntentBatch
{"seq": 18, "issued_date": "2231.05.01", "issued_m": 376, "intents": [], "knobs": {"emit_period": 12}}
```

Invalid: deferred kind.

```json model=Intent invalid
{"intent_id": "i-000401", "decision_id": "d-2232.01-01", "country": 5, "kind": "PRIORITIZE_TECH",
 "category": "physics", "weight": 1.5}
```

Invalid: posture without target.

```json model=Intent invalid
{"intent_id": "i-000402", "decision_id": "d-2232.01-01", "country": 5, "kind": "SET_MILITARY_POSTURE",
 "posture": "offensive"}
```

Invalid: dropped kind (v1.1; reserved until M0-7).

```json model=Intent invalid
{"intent_id": "i-000403", "decision_id": "d-2232.01-01", "country": 5, "kind": "FLEET_COMPOSITION_BIAS",
 "weights": {"cruiser": 70}}
```

Invalid: two econ intents for one country in one batch.

```json model=IntentBatch invalid
{"seq": 19, "issued_date": "2231.06.01", "issued_m": 377, "intents": [
  {"intent_id": "i-000404", "decision_id": "d-2231.06-01", "country": 3, "kind": "SET_ECON_PLAN", "plan": "military"},
  {"intent_id": "i-000405", "decision_id": "d-2231.06-01", "country": 3, "kind": "SET_ECON_PLAN", "plan": "research"}]}
```

Invalid: free-text field (extra fields are forbidden everywhere).

```json model=Intent invalid
{"intent_id": "i-000406", "decision_id": "d-2232.01-01", "country": 5, "kind": "NOOP_HEARTBEAT",
 "console": "effect add_resource = { energy = 1000 }"}
```

## 8. Versioning

`schema_version` and `sae_p_schema` move together. Adding a kind, a parameter, or an enum value is a
v2 change (the mod must learn it first, otherwise it answers `REJECTED/RANGE` or `SCHEMA_MISMATCH`).
Enum codes are never reused.

v1.1 removes `FLEET_COMPOSITION_BIAS` from the producer vocabulary only: the C2 template, the
variable order and `sae_p_schema = 1` are unchanged, and every file the engine now writes is one a v1
mod already accepts (ship slots -1 = keep), so no wire version moves. Restoring the kind after M0-7
also needs no mod change (the mod already range-checks and stores the slots); it is a C3 v1.2 proposal.

## 9. UNVERIFIED assumptions (M0 checklist)

| Id | Assumption | M0 test | Fallback |
|---|---|---|---|
| A-21 | Flag-gated additive `ai_budget`/subplan entries measurably change AI behaviour | M0-8 (and M0-13 for subplans) | Between-session defines; per-campaign adaptation |
| A-22 | A per-size ship-mix knob exists (StarNet `str_var_ai_ship_data_factor_<size>` or SAE script values) | M0-1, M0-7 | Kind stays dropped (v1.1 default until this passes) |
| A-23 | An opinion-modifier effect can express posture/stance toward a target | M0-6 (extended) | Flags only, weights unproven |
