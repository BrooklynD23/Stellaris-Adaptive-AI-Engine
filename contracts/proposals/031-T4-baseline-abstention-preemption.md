# 031-T4 — Baseline-abstention pre-emption (D1)

Status: **ACCEPTED** (2026-11-15; maintainer-approved, Wave 1). Reporter: T4 (optimization study, Wave C). Owner:
architect. Work order W1-A0. Plan: `docs/plans/opt/IMPLEMENTATION_WAVES.md` §3; plan review
`docs/status/opt_implementation_waves_review.md` B1, B2, B3.
Contracts: C3 §6 (v1.2 → **v1.3**, rule change); C10 §6 (v1.2 → **v1.3**, gate pass rule); C5 §3 `outcome` row
(wording only, C5 stays v1.3). No model, schema or wire change (`schema_version`, `v`, `sae_p_schema` stay 1).
Consumers: T4 (`core/strategy.py`, W1-1), T7 (`bench/gate/stage1.py`, W1-8; CI-1 `tests/e2e/test_ci1.py`),
T9 (`sae tune`: short baseline outcomes), T6 (bus readers: `outcome` emit points), T8 (docs).

## Problem
Below the belief threshold the engine opens a 24-month `baseline` window. A confident reading that arrives one
month later waits out the rest of that window, so the first counter can lag the evidence by up to 23 months. The
`baseline` chosen for lack of evidence is an abstention, not a strategy the engine committed to.

## Decision record
Maintainer decision D1 (2026-11-15): pre-empt `below_threshold` windows, **no evidence-tick guard**. Recorded
once: the Wave C reviewer advised deferral, because one noisy save can lock a 24-month counter. The maintainer
approved without a guard. The behaviour is pinned by W1-1 test (h) and measured by the W1-9 wrong-lock rate.

## Change 1 — C3 §6 (v1.3)
New subsection "Baseline-abstention pre-emption", and the sentence "Every strategy is held for
`engine.commitment_months` (24); the reward window equals the commitment window" gains the exception.

Definition: an **abstention window** is a `baseline` window opened by a decision whose reason is
`below_threshold`. A `baseline` window opened by `table` (confident `balanced`), by `safety_floor` or by an
operator force is **not** an abstention window.

1. **Pre-emption.** While an abstention window is open and no operator force is active, a tick whose top
   archetype has posterior ≥ `belief_threshold` and whose counter-table strategy is not `baseline` closes the
   window at that tick and opens a full commitment window for the counter: `strategy_selected.reason = "table"`,
   `switched = true`, `commit_from` = the tick's month, `commit_until` = `commit_from` + `commitment_months`.
   One confident tick is enough.
2. **Confident `balanced` opens no window.** During an abstention window a confident `balanced` tick changes
   nothing: strategy `baseline`, reason `commitment_hold`, `commit_until` unchanged, the window is still an
   abstention window. A later confident counter tick still pre-empts.
3. **Safety floor.** If the pre-empting counter fails the static floor check, the abstention window is untouched:
   reason `commitment_hold`, `commit_until` unchanged, no window closed, no `outcome`.
4. **Nothing else is pre-empted.** Counter windows and non-abstention `baseline` windows are held for the full
   `commitment_months`; only an operator force or clear-force (C9) reopens them, as before.
5. **Order per tick** is unchanged: forced > (pre-emption, else commitment hold) > table / below threshold.
6. **Expiry.** At `commit_until` the normal re-evaluation runs: below threshold opens a new abstention window;
   confident `balanced` opens a `table` baseline window (24 months, not pre-emptable).
7. **Outcome.** Pre-emption closes the abstention window early, so the engine emits its `outcome` at that tick:
   `strategy = "baseline"`, `forced = false`, `window_to - window_from` of 1 to 23 months, `decision_id` of the
   decision that opened it. These are real rewards over a short window, not void rows.
8. **State.** T4's `StrategyState` gains `abstain: bool = False` (in `src/sae/core/strategy.py`, not a contract
   model), set only when a `below_threshold` decision opens the window and kept across a session reanchor.
   `state.json` round-trips it; `state_version` stays 1. A `state.json` written before v1.3 loads with
   `abstain = false`, so its open baseline window is not pre-emptable until it expires.

## Change 2 — C10 §6 (v1.3), new rule 4
`feel_commitment` (Stage 1) passes iff, in every replayed campaign's candidate strategy trace, every switch
**out of a non-baseline strategy** happens ≥ 24 months (`commitment_months`) after that strategy started.
Switches out of `baseline` are not constrained (C3 §6 pre-emption). Computed from the per-month strategy trace
alone. `feel_switches` is unchanged. Examples: `counter(0-23), baseline(24-26), counter(27-50)` passes;
`counter(0-10), baseline` fails.

Before: the check failed when any two strategy changes were closer than 24 months, which every campaign with one
pre-emption violates (counter ends at 24 → abstention → counter at 27: gap 3).

## Change 3 — C5 §3 (wording, no version change)
`outcome` trigger: "commitment window end" → "window close: commitment end, operator force or clear-force, or
pre-emption of an abstention window (C3 §6; `baseline`, 1–23 months)". The decision-chain sentence says "at window
close". `OutcomePayload` is unchanged (it never constrained the window length).

## Model and schema impact
None in `src/sae/contracts/**`: `SelectReason` already has `table`, `commitment_hold`, `below_threshold`;
`OutcomePayload` and `GateCheck` are unchanged. No schema re-export.

## Migration and consumer notes
- T4 (W1-1): implement rules 1–8 in `core/strategy.py` only; no `engine_tick.py` edit.
- T7 (W1-8): new `feel_commitment` rule; merges with W1-1. CI-1 pooled switch-match threshold 0.90 is not edited.
- T9 / T7 readers of `outcome`: baseline windows shorter than 24 months now exist. Do not assume a fixed length.
- Bus traces from before v1.3 stay valid; no reader rejects either shape.

## UNVERIFIED
All evidence for the lag gain is synthetic (V-1 to V-5 open). One confident tick locks 24 months by design
(rule 1); the wrong-lock rate on real play is unknown until M0/M4.
