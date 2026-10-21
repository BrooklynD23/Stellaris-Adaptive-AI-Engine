# X4 plan review — adversarial (Fable gate)

Reviewer: sae-adversary (Fable 5.1). Date: 2026-10-20. Subject: `docs/plans/X4_plan.md`, proposal 029.
Verdict: **ACCEPT WITH CHANGES** (6 blocking, fix before batch 1 lands).

## Blocking

1. **Exit code 3 collision (029 §2.5 vs C8 §5 line 153 "3 invalid config").** `sae.profile.cli._context` already
   returns 3 for invalid config (`src/sae/profile/cli.py:107`), and X4-D/E reuse it. `sae gate stage2 --objective
   beat_player` exiting 3 is then indistinguishable from a broken config. Fix: use **4** for "no result by
   design" in 029 §2.5, C8 §5, plan §1.4/§5, X4-E/X4-F work orders.
2. **Stage 2 "reported" checks cannot exist under the model.** `GateReport._passed_is_all` forces
   `passed == all(check.passed)` (`src/sae/contracts/c10_profile.py:515`); `detection_delay`, `false_lock`,
   `v3_signal` "(reported)" would gate or be unfailable. C10 §6 example (line 373: a failed check inside
   `passed: true`) already violates the validator. Fix in X4-A/C10 v1.2: state that stage2 pass = every
   `sealed_noninferior:<cid>` check; informational checks carry `passed: true` + `value` + `detail` by rule;
   fix the §6 example.
3. **Contract drift B↔C: `kept_observations` returns `list[_Kept]`, not `list[Observation]`.**
   `summary._kept_observations` (`summary.py:61`) yields `_Kept(obs, belief)`. X4-C must define
   `ledger.labelled_campaigns` as returning `list[Observation]` (`[k.obs for k in ...]`) and X4-B's
   `detector_trace(observations: Sequence[Observation], ...)` consumes exactly that. Write the signature in
   plan §1.2 before B and C start in parallel.
4. **Rollback e2e via in-process `sae synth` needs the Rust parser + full asyncio daemon.** `adapters/cli.py:156`
   requires `parser_exe`; `daemon.run` spins the whole loop. In `tests/unit/t9` this is slow and fails on a box
   without `parser/target/release/sae-parse`. Fix: assert `sae.daemon.policy_resolve.resolve_policy(cfg).ref.sha256
   == A` (pure, the same code path `wiring.py:89` uses for `run_started.policy`); keep the real `sae play` check
   as X4-G5.
5. **Deferred handoff must be honest about inputs.** X4-G1–G3 need 6 *closed, labelled, ~10-year live*
   campaigns in `<sae_dir>`; tomorrow's game PC will have autosaves, not that. `docs/handoff/deferred/X4.md`
   must list the prerequisite (`sae profile update/close/label` steps, est. time per campaign), say
   "V-1 NOT VERIFIED until then", and give the fallback command (`--include-synth`, diagnostic only, no
   registry row). No status doc may word synth results as verification.
6. **`loco_log_loss_delta` is not LOCO.** Plan §1.4 step 5 compares *held-in* loss of a candidate fit on all
   tune campaigns (biased for the candidate) against `min_effect`. Rename the check `heldin_log_loss_delta`
   or recompute true LOCO from `TuningManifest.kappa` + grid point; either way the `detail` must say which.

## Suggestions (not blocking)

- Sealed-budget bypass: re-propose the same `detector` with new `notes` → new sha → another sealed query.
  Add a stage1 check: sha256 of the `detector` block already has a `stage2_*` row → fail.
- `feel_commitment` is tautological (engine enforces windows); keep only if the safety-floor override exists.
- Cross-platform sha: `notes` floats from `math.log` can differ by an ulp between Windows/Linux; write `round2`
  values into `notes`.
- t7 tests importing the t9 fixture: no `tests/__init__.py`/`tests/unit/__init__.py`, so the import is
  `from t9._synth_campaigns import build_campaigns`; say so in X4-E.
- `docs/user_guide.md` is also touched by other live waves; expect merge conflicts, keep X4 edits to new sections.

## Checked and clean
Fog-of-war (bus.jsonl only, labels are player-given, offline); settled decisions (no new process, no LLM/GPU,
own-process priority only, CUSUM diagnostic, D1–D6 respected, `--objective` required); C8/C10 additive; file
sets disjoint except B/C shared signature (item 3); game-dependent items all behind `SAE_GAME_SAE_DIR` skips.

Bottom line: the architecture is right (judge owned by T7, ledger-backed sealed split, no daemon change) but two
contract clashes (exit 3, GateReport validator) and one fixture-type drift will break parallel batch 1; fix them
in X4-A/C before dispatch and make the handoff state the labelled-campaign prerequisite plainly.

## Plan review response

T9 lead, 2026-10-20. All 6 blockers closed in `docs/plans/X4_plan.md` and proposal 029; contract text lands via X4-A.

| # | Blocker | Resolution |
|---|---|---|
| 1 | Exit 3 collision | "No result by design" = **4** in 029 §2.5/§4, plan §1.4/§4/§5; X4-A adds 4 to C8 §5's exit-code line; X4-E/X4-F work orders use 4. 3 stays invalid config. |
| 2 | Reported checks vs validator | 029 §3b + plan §1.4: stage 2 pass = all `sealed_noninferior:<cid>`; `detection_delay`/`false_lock`/`v3_signal` are `passed: true` + `value` + `detail`. X4-A writes it into C10 v1.2 §6, renames the Stage 1 example check, adds a valid Stage 2 example. Note: C10 line 370-373 is the *invalid* example (correctly rejected); the valid example only needed the rename. |
| 3 | B↔C type drift | Plan §1.2 freezes `ledger.labelled_campaigns -> list[LabelledCampaign(state, label, observations: list[Observation])]`, `observations = [k.obs for k in kept_observations(...)]`; metrics takes `Sequence[Observation]`; `MonthPosterior` defined. X4-B/X4-C orders quote it. |
| 4 | Rollback e2e needs parser + daemon | Test asserts `sae.daemon.policy_resolve.resolve_policy(cfg).ref.sha256` (same call as `wiring.py:89`); live `sae play` stays X4-G5. |
| 5 | Handoff honesty | Plan §5 adds prerequisite X4-G0 (profile update/close/label per campaign, times), "V-1 NOT VERIFIED until 6 labelled campaigns exist", `--include-synth` diagnostic fallback with no registry row; X4-F forbids wording synth as verification. |
| 6 | `loco_log_loss_delta` not LOCO | Renamed `heldin_log_loss_delta`; `detail` states held-in/biased and points to tuner per-fold LOCO and Stage 2. Gate-side true LOCO = backlog. |

Suggestions taken: t7 import path `from t9._synth_campaigns import build_campaigns` (X4-E); `round2` values in
tuner `notes` (X4-D); user-guide edits limited to new sections (X4-F). Detector-block reuse check: taken as a
stage1 check `sealed_budget` (sha256 of canonical `detector` block already has a `stage2_*` row → fail) in X4-E.
`feel_commitment`: kept, cheap, guards a future safety-floor override.
