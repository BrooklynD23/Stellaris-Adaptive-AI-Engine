# IMPLEMENTATION_WAVES plan review (sae-adversary, pre-implementation gate)

Date 2026-11-15. Read-only: plan, the 5 `docs/status/opt_wave_*` reviews, `contracts/README.md`, and the code named
below. No test was run; every finding is from reading code. D1 (no guard), D2 (`sae play` update after exit) and the
Fable gate are taken as decided.

| Wave | Verdict | Blocking |
|---|---|---|
| 1 | ACCEPT WITH CHANGES | B1-B6 |
| 2 | ACCEPT WITH CHANGES | B7-B8 |
| 3 | ACCEPT WITH CHANGES | B9-B11 |
| 4 | ACCEPT WITH CHANGES | B12-B13 |

## Blocking changes

### Wave 1

**B1. W1-1 breaks a T7 gate check that is in no work order.**
- `src/sae/bench/gate/stage1.py:183-185,323-325`: `feel_commitment` fails when any two strategy changes are closer
  than 24 months (`COMMIT_MONTHS = 24`, line 50). `switch_months` sees only the strategy per month, not the reason.
- With D1: counter window ends at month 24 -> `below_threshold` baseline (switch) -> confident tick at month 27 ->
  counter (switch). Gap 3 < 24. Every candidate policy with one such campaign fails `sae gate stage1`.
- `tests/unit/t7/test_gate_stage1.py:158,301` assert on this check, so W1-1's own `uv run pytest -x -q` can go red
  in a T7 file.
- Fix: add **W1-8 (T7, 1.5 h)**, files `src/sae/bench/gate/stage1.py`, `tests/unit/t7/test_gate_stage1.py`. New
  rule: every switch *out of a non-baseline strategy* happens >= 24 months after that strategy started (computable
  from the trace alone). Test: trace `counter(0-23), baseline(24-26), counter(27-50)` passes; `counter(0-10),
  baseline` fails. W1-8 depends on W1-A0 and merges with W1-1.
- Fix: proposal 031 must also amend C10 §6 (GateReport pass rules, `feel_commitment`) and the C5 §2 `outcome` row
  ("commitment window end" is no longer the only emit point). See B5 for the version collision.
- W1-1 must run `tests/e2e/test_ci1.py` (H0 vs H2 switch matching, threshold 0.90) before hand-back and report the
  pooled rate before and after. A T7 test edit goes through W1-8, not W1-1.

**B2. W1-1 spec is ambiguous in 3 places; each needs a line in 031 and a test.**
- `StrategyState` (`core/strategy.py:22-33`) cannot tell an abstention window from a table-chosen baseline window:
  both have `strategy=BASELINE`, `opened_key=BASELINE`. Test (b) is unimplementable without a new field. Fix: add
  `abstain: bool = False` to `StrategyState` (same file; `state.json` round-trips it, `state_version` stays 1, a
  pre-W1 `state.json` loads with `False` = not pre-emptable until expiry; say so in 031).
- Confident `balanced` during an abstention window: the table returns `baseline`. Fix: it does **not** open a
  committed window; abstention stays open. Otherwise one transient `balanced` reading blocks every counter for 24
  months, the opposite of D1. Test (e): abstention + confident balanced -> `commit_until` unchanged, a later
  confident turtle tick still pre-empts.
- Pre-emption closes the abstention window early, so `engine_tick.py:96-98` emits an `outcome` for a window of 1 to
  23 months, which `tune/cli.py:_v3` and the gate read as a reward. Fix: keep the emit (no `engine_tick.py` edit),
  state it in 031, and add test (f): the outcome has `strategy=baseline` and `window_to - window_from` < 24.
- Also add test (g): a `safety_floor` baseline window is not an abstention (or is; pick one, the plan is silent).

**B3. D1 one-noisy-save risk: add the test that shows it (no guard).**
- Unit, `tests/unit/t4/test_strategy.py`: abstention window; tick t has posterior 0.60 for turtle; ticks t+1..t+23
  are uniform. Assert: strategy is `outscale_siege` with reason `commitment_hold` on all 23 ticks, `commit_until`
  = t+24, and tick t+24 returns to `below_threshold`. This pins "one tick locks 24 months" as documented behaviour.
- Demo number: over seeds 0-9 x 4 archetypes of `sae bench demo`, report the wrong-lock rate = first counter is not
  the label's counter. Quote it next to the lag figure. Plan risk 1 ("watch counts") is not a test.

**B4. W1-2 property test is unsatisfiable as written, and misses the fog-recovery spike.**
- "Losing one observer moves no posterior" is false in general: the lost view also changes `border_fort_ratio` and
  the `fleet_power_cat`/`tech_cat` medians (`core/features.py:137-145`). Fix: build views identical in every other
  feature; assert `expansion_rate is None` and that `"expansion_rate"` is absent from the evidence rows.
- New: after an observer is lost, `samples` stores the lower `now_sys` (`features.py:130`). When the observer
  returns, `_rate` is large and positive, the top clamp turns it into a full-strength vote, and D1 can lock a
  counter on it. Add a test: observer lost at month 12, back at month 24; record the rate and the top posterior.
  If the posterior crosses 0.45 from this alone, report it in the result review (no code fix required in W1).
- `changelog.d/T4.md` note: detector behaviour changes while builtin policy 0.1.0 keeps its sha256.

**B5. Contract version targets collide.**
- 031 must touch C10 §6 (B1), 036 adds a public split signature to C10, 037 claims "C10 -> v1.3". Fix: 031 = C3
  v1.3 + C10 v1.3 + C5 doc wording; 036 = C10 v1.4; 037 = C10 v1.5.
- 034 and 035 both claim "C5 v1.4". Fix: merge them into one proposal, or 034 = C5 v1.4 and 035 = C5 v1.5 (then
  036 = C5 v1.6). One proposal, one version line (README §3 rule 5).
- 035 adds `daemon_private_bytes` to `SensorSample`, a C7 model (`contracts/c7_governor.py:130`). List C7 in 035.
- 037: if the C10 model stops accepting the old `outcome_weights` keys, that is breaking (rule 6: C10 v2,
  `schema_version = 2`). Fix: the model accepts both key sets, chosen by PolicyVersion major, so it stays v1.N.
  State this in 037.
- W1-A0 also fixes: `contracts/C5_bus.md` header says Version 1.3 but Status "FROZEN v1.2".

**B6. Work orders in Wave 1 that share or omit files.**
- W1-3 cannot be done in `input.rs` alone. `input::load(path)` does not know the sections
  (`parser/src/main.rs:76-83`), and `extract::extract` runs `split(gamestate)?` before it reads meta
  (`extract.rs:475-498`). Fix: file set = `parser/src/input.rs`, `main.rs`, `extract.rs`,
  `parser/tests/meta_fast_path.rs`, `parser/tests/cli.rs` (line 69 pins `sae-parse 0.1.1`), `Cargo.toml`,
  `Cargo.lock`, `README.md`. The "forced full path" is a library argument, not a new CLI flag (a flag is a C4 §1
  change). Add one test for the accepted divergence: valid meta + corrupt gamestate -> fast path exit 0, full path
  the old error code. "Identical over every fixture" must exclude exactly that case by name.
- W1-5 imports `_game_running`/`_lower_priority` from `src/sae/profile/cli.py`, which W1-7 edits. Fix: W1-5 imports
  the private names unchanged (as line 17 already does with `_context`) and edits nothing there; W1-7 must not
  rename them.
- `changelog.d/T4.md` (W1-1, W1-2), `T7.md` (W1-4, W1-5, W1-8), `T9.md` (W1-6, W1-7) are shared. Fix: the team lead
  writes the fragment once after its orders merge; implementers do not touch `changelog.d/`.
- W1-7: `sae profile update` exits 1 when Stellaris is running (`profile/cli.py:203-204`). After the daemon exits
  the game is often still open, so the update is refused every time. Fix: test "update exits 1 because the game
  runs -> launcher prints `run sae profile update after closing the game`, returns the daemon's code". Never pass
  `--force`. Add to the C8 §5 text: a Windows console close kills the launcher within about 5 s, so no update runs
  (UNVERIFIED on Windows). The pty test's order assertions must be rewritten, not "still pass".

### Wave 2

**B7. W2-2 spec omits 3 of the 4 keys it must emit.** `_metrics` (`daemon/loop.py:265-280`) is the only place
health keys are written. Fix: W2-2 emits `engine_ms`, `daemon_private_bytes`, `spm_ratio`, `spm_armed`; W2-2 starts
after W2-1 and W2-3 (not parallel). `metrics` values are floats: 034 states `spm_armed` is 1.0/0.0 and `spm_ratio`
is omitted when unarmed. Add to the W2-2 test: `wall_s == (now - last_hb_poll) / pending_k` (B-review change 1).

**B8. W2-6 is a CI run, so it needs a push.** Mark it "after maintainer approval of the doc diffs and the git
commands" and move it out of the 18 h estimate.

### Wave 3

**B9. W3-1 cannot reuse `profile/index.py`.** `tests/integration/test_ci4_imports.py:16` forbids `sae.profile` and
`sae.tune` in the daemon's import graph. Fix: W3-A0 puts the split predicate in `src/sae/contracts/` (pure function
plus `SPLIT_GAP_MONTHS`); W3-2 makes `profile/index.py` call it; W3-1 imports it from contracts. W3-1 then depends
on W3-A0 only.

**B10. `engine.carry_state` scope.** The flag itself is fine: it is a C8 key declared in 036, default off. Two
gaps:
- Add a flag-off test: a 6-year synth run gives a normalised bus and `state.json` equal to the pre-W3 commit.
- C-review B3 covers regression "carry or in-session". Say whether the in-session fix (decay, void outcome) is
  behind the flag. Recommended: not behind the flag, as its own order W3-1b (`core/engine.py`), so the cross-
  timeline outcome bug is fixed for everyone. Then W3-1 shrinks below 14 h.
- Carried abstention windows keep `abstain=True` (D1 still pre-empts in session 2). One test.

**B11. Demo v3 must use the flag and a recorded live-shape fixture.** Synth keys are `f-synth-` + sha256(run_id)
(`contracts/common.py:63-65`), so two synth runs never share a key. State in the demo: flag off by default,
identity rests on UNVERIFIED A-46.

### Wave 4

**B12. W4-1 changes `state.json` without a version bump.** `AiMetrics.fleet_share` is stored in
`EngineState.window_open` (`core/state.py`, `core/outcome.py:67`). Raw fleet power needs a new field; models forbid
unknown fields, so an old `state.json` fails `--resume` as "corrupt". Fix: add the field with a default and keep
`fleet_share`; test: a pre-W4 `state.json` loads.

**B13. W4-1 (b) depends on W3-4 and on B5's C10 rule.** Keep W3-4 in Wave 3; add "W4-1 starts after W3-4 merged".

## Mandatory changes from the earlier reviews

| Review item | Carried? |
|---|---|
| A B1-B3, A1 blocking 1-3 | Done in commit 32004eb (C4 is v1.1, proposal 030) |
| A B4 (item 8) | Yes, deferred table |
| A B5, B6 | Yes, W2-5 |
| A item 9 equality test | Yes, W1-6 |
| B item 1 changes 1-4 + 5 tests | Yes, W2-1/W2-2/034 (see B7) |
| B 2b | Partly: the "cpu_s within 10 % of the daemon" check is dropped. Add it to M0-11 in W2-5 |
| B 2c | Yes, deferred |
| B item 6 changes 1-3 | Yes, W1-3/032 (see B6) |
| C B1-B4 | Yes, W3-1 (B1's "reuse index.py" conflicts with CI-4, see B9) |
| C B5, B6 | Yes, W4-1, W1-2 (test wrong, see B4) |
| D D1.1, D1.2, 7b.1-7b.4, ordering 1, stack 2 | Yes |
| D D1.3 (whitelist from `Need`), D1.4 mutation corpus | Not written down. Add to the deferred row |
| D ordering 4 (multi-parser in SYNTHESIS §7), §5 do-not-do additions | Missing. Add to W4-4 |
| A1 non-blocking 5 (`black_box`) | Missing. Add to W1-3 |

## Planner's own decisions

The plan does not list its 5 decisions. The ones found:
1. Pre-empt only `below_threshold` windows: accept, with B2.
2. Parser patch bump 0.1.2: accept (B6 for `cli.rs`).
3. `engine.carry_state` default off: accept, with B10.
4. UV mirror pinned to GitHub (A-review option 1): accept.
5. Carry hold cap of 12 months: accept, but it delays the first reaction by up to 12 months when the flag is on;
   state it next to the D1 lag figure.
6. Version targets: reject as written (B5).

## Non-blocking

1. Demo v1 quotes study figures (lag 21 -> 0, tune 54 -> 12 s, probe 0.236 -> 0.007 s). Re-measure all three on the
   merged branch; W1-2 lands in the same wave, so the lag change is not from D1 alone.
2. The 4 overclaim flags are right. Add a 5th: "the safety floor is vacuous today" (`strategy.py:73-75`).
3. `sae play` holds the terminal up to 600 s after exit (`UPDATE_TIMEOUT_S`). Print one line before the update.
4. W1-5 makes 3 call sites of the guard (profile, tune, gate). Move it to `sae.platform` when a 4th appears.
5. Nothing in Waves 1-4 needs the game PC. W1-3's fast path is inert if real `meta` lacks `date`/`version` (A-18).
6. W3-1 at 14 h is one order across 7 files; split after B10.

## Architecture ratings (after the blocking changes)

| Area | Rating | Why |
|---|---|---|
| 1. Performance and memory | ON TARGET | Meta probe and tuner memo are real; no resident growth; CI-4 boundary kept once B9 lands |
| 2. Telemetry | AT RISK | W2-2 as specified emits 1 of 4 keys (B7); C5/C7/C10 version lines collide (B5) |
| 3. Adaptive AI engine | AT RISK | D1 makes a single tick a 24-month lock and fails the gate's `feel_commitment` (B1-B3); all evidence is synth |
| 4. Learning profile from saves | AT RISK | D2 update is refused while the game runs (B6); short abstention outcomes enter tune (B2); carry rests on A-46 |
