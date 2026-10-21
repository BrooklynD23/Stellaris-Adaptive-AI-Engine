# 029-T9 — X4: dispatch `tune`/`policy`/`gate`, CLI verbs, sealed-query ledger convention

Status: **ACCEPTED** (architect, 2026-10-20, work order X4-A). Proposed by T9 lead, wave X4. Reporter: T9 (coordinating T7). Owner: architect.
Decide in X4 batch 1 (work order X4-A). Plan: `docs/plans/X4_plan.md`.
Contracts: C8 §5 (v1.5 → v1.6, additive); C10 §5, §6 (v1.1 → v1.2, clarifications only, no schema change).
Consumers: T9 (implements `sae tune`, `sae policy`), T7 (implements `sae gate`), T4 (none: daemon resolution
unchanged), T8 (user guide, deferred handoff).

## 1. Dispatch (C8 §5, `src/sae/cli.py`)
Move the three "Planned, not dispatched yet" rows to the dispatched table and add them to `_COMMANDS`:

| Command | Module (owner) | Contracted arguments |
|---|---|---|
| `sae tune detector` | `sae.tune.cli` (T9) | common flags; `--player <key>`; `--force` (run while the game is detected); `--include-synth` (test-only, C10 §1 rule 1). Reads only `split == "tune"` campaigns; writes `policies/<sha>.json` + one `propose` row `by: tuner`; never touches config or the running daemon |
| `sae policy propose <file>\|prereg <sha>\|approve <sha>\|reject <sha>\|promote <sha>\|rollback [<sha>]\|pin builtin\|show` | `sae.profile.policy_cli` (T9) | common flags; human rows via `append_registry_row` (TTY) plus a typed first-8-hex confirmation; `prereg` takes `--metric`, `--min-effect`, `--margin`, `--budget`, `--min-n` |
| `sae gate stage1 <sha>\|stage2 <sha>` | `sae.bench.gate.cli` (T7) | common flags; `--include-synth` (test-only); `stage1 [--repo <checkout>]`; `stage2 --objective detector_replay\|beat_player\|challenge_band` (**required**, no default; maintainer D6 is undecided) `[--out <protocol.json>]` |

Safe before the modules exist (dispatcher prints `not available yet (owner T9|T7)`, exit 2).
Module path `sae.profile.policy_cli` is the one C8 v1.5 already names. `sae.bench` is already in CI-4
`FORBIDDEN_SAE`, so `sae.bench.gate` needs no new entry; `sae.tune` is already listed.

## 2. CLI semantics made contract (C10 §5 text)
1. `rollback` without a sha = one step: target = `previous_sha256` of the latest `promote`/`rollback` row
   (`null` → built-in sha). With a sha: must be the built-in sha or a sha promoted earlier (fold rule unchanged).
2. `pin builtin` = a human `rollback` row targeting the built-in sha (`reason = "pin builtin"`), effective at the
   next daemon start, plus a printed hint for the hard pin `policy.pin = "builtin"`. The CLI never edits config.
3. `promote` refuses (exit 2) a candidate whose `game_version_pin` does not match every `game_versions` entry of
   the campaigns in its `tuning.campaign_ids` (C10 §4 step 5, offline half).
4. `propose` (human) copies the file to `policies/<sha>.json` only after the model validates and the name check
   passes; `by: human:<name>`, TTY required.
5. Exit codes for `tune`/`policy`/`gate`: 0 ok/pass, 1 stage fail or I/O/lock error, 2 usage or refused,
   3 invalid config (unchanged, C8 §5), **4 no result by design** (stage2 game-paired objectives only).
   C8 §5's "Exit codes for every command" line gains "4 no result by design (`sae gate stage2` game-paired)".

## 3. Sealed-query ledger convention (C10 §6, clarifies C10 §1 rule 5 and §2 rule 3)
A Stage 2 `detector_replay` GateReport names each sealed campaign it read in one check named
`sealed_noninferior:<campaign_id>` (≤ 64 chars: 19 + 18). The sealed-query count of a campaign = number of
`stage2_pass|stage2_fail` registry rows whose evidence GateReport contains that check name. Unreadable evidence
→ the row counts against **every** sealed campaign (conservative). Synth (`--include-synth`) Stage 2 runs write
no registry row and therefore never spend a query.

## 3b. GateReport pass rules (C10 §6 text; validator `passed == all(checks.passed)` unchanged)
Stage 2 `detector_replay` passes iff every `sealed_noninferior:<cid>` check passes. Informational checks
(`detection_delay`, `false_lock`, `v3_signal`) are written with `passed: true`, the number in `value` and the
meaning in `detail`; they never gate. Stage 1's log-loss check is named `heldin_log_loss_delta` (held-in,
biased toward the candidate, says so in `detail`); fix the C10 §6 valid Stage 1 example's check name to match and
add one valid Stage 2 example with a `sealed_noninferior:<cid>` check plus an informational check.

## 4. Stage 2 objectives (C10 §6 text)
`--objective detector_replay` runs the v1 sealed tripwire (plan §5.3 step 2). `beat_player` and `challenge_band`
are the two D6 options: the command writes a draft protocol (paired seeds, `min_n >= 30`, crash = loss, halt on
two crashes) to `--out`, appends **no** registry row and exits 4 with "no result: game-paired Stage 2 needs D6
and real paired runs (M4)". There is no flag that makes it report a result; the game-paired GateReport kind needs
its own proposal after M4 (plan §5.3 step 3).

## 5. Not changed
Schemas, fold rules, chain order, daemon resolution (C10 §4), `Preregistration.kind` (still only
`detector_replay`), process topology.

## Decision
**ACCEPTED** (architect, 2026-10-20, X4-A). Adversarial plan review (`docs/status/X4_plan_review.md`) blockers 1, 2
and 6 are resolved in this text (exit 4 not 3; informational checks `passed: true`; `heldin_log_loss_delta`).

Landed:
- §1 → C8 v1.6 §5: `tune`, `policy`, `gate` move to the dispatched table with the §1 arguments; `src/sae/cli.py`
  `_COMMANDS` gains `tune → sae.tune.cli (T9)`, `policy → sae.profile.policy_cli (T9)`, `gate → sae.bench.gate.cli
  (T7)`. Until each module exists the dispatcher prints `not available yet (owner T9|T7)` and exits 2 (tested).
- §2.5 → C8 §5 exit-code line gains `4 no result by design (sae gate stage2 game-paired objectives only)`.
- §2 → C10 v1.2 §5; §3, §3b, §4 → C10 v1.2 §6. Valid Stage 1 example renamed to `heldin_log_loss_delta`,
  one valid Stage 2 example added (checked by
  `tests/contract/test_doc_examples.py`). No schema or model change; `GateReport` validator unchanged.

Architect change: the Preregistration example keeps `primary_metric = "loco_log_loss"`. The model field is
`Literal["loco_log_loss", "detection_delay_months"]` (`src/sae/contracts/c10_profile.py`), so `heldin_log_loss`
would be a schema change, out of scope for this clarification-only bump; the preregistered metric names the
tuner's LOCO numbers, the Stage 1 check reports the held-in delta. Adding a value needs its own proposal. Exit 4 is
reserved for `sae gate stage2 --objective beat_player|challenge_band`; no other command may return it without a
proposal. The game-paired GateReport kind and any result for those objectives remain out of scope until M4 + D6.
