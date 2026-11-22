# SAE sprintboard (master progress document)

Single place to check progress. Ask "check the sprintboard" and the agent reads this file, verifies it against
git and the status files, and reports drift. **Last verified: 2026-11-21 at commit `a0d87c9`**
(branch `feat/opt-wave-a1`, PR #5, base `feat/x2-m0-field-kit`, stacked on PR #4).

Nothing here has run in the real game. Every Stellaris script, console and save behaviour is **UNVERIFIED** until
M0 (`docs/m0_checklist.md`). All measured numbers are synthetic data only.

Legend: DONE = delivered and gated · FOLLOW-UP = done with named open items · IN REVIEW = PR open · PLANNED =
designed, no code · NOT STARTED · BLOCKED = needs the game or the maintainer.

## 1. Dashboard

| Area | State | Source |
|---|---|---|
| Wave 0 contracts C1-C10 | DONE | `contracts/`, `contracts/CHANGES.md` |
| Teams T1-T9 (MVP, game-free) | DONE | `docs/status/T*.md` |
| X2 M0 field kit | DONE (not run on Windows) | `docs/plans/X2_field_kit_plan.md` |
| X4 tune, gate, policy scaffold | DONE (game checks deferred) | `docs/status/T9.md`, `docs/handoff/deferred/X4.md` |
| Opt wave A1 | DONE, IN REVIEW (PR #5) | `docs/status/opt_wave_A1_result_review.md` |
| Opt waves 1-2 | DONE, IN REVIEW (PR #5) | `docs/status/opt_w12_*_review.md` |
| Opt wave 1.5 | DONE with follow-ups, IN REVIEW (PR #5) | `docs/status/opt_wave_1_5_*.md` |
| Opt wave 3 (campaign memory) | PLANNED, design reviewed | `docs/plans/opt/W3_DESIGN.md`, `docs/status/opt_w3_design_review.md` |
| Opt wave 4 (scale-free reward) | NOT STARTED | `docs/plans/opt/IMPLEMENTATION_WAVES.md` |
| M0 weekend (real game) | BLOCKED, needs Stellaris | `docs/m0_checklist.md` |
| Validation backlog V-1..V-5 | BLOCKED, needs real saves or the game | `docs/TEAM_PLAN.md` |

## 2. Waves

Plan: `docs/plans/opt/IMPLEMENTATION_WAVES.md` (rev 2, 80.5 h total). Each wave: plan gate (adversary), code gate
(Opus reviewer), architecture gate (adversary), closure (CLAUDE.md "Review gates").

| Wave | Theme | Hours | State | Evidence |
|---|---|---|---|---|
| A1 | fsync gating, parse peak commit, `sae play` stdin | n/a | DONE, commit `32004eb` | proposal 030, C4 v1.1, parser 0.1.1 |
| 1 | Demo v1: faster reaction, faster tools (W1-A0, W1-1..W1-9) | 23.5 | DONE, commit `bf2e56b` | proposals 031-033; `DEMO_V1_NUMBERS.md` |
| 2 | Truthful telemetry + M0 kit (W2-A0, W2-1..W2-5) | 18.5 | DONE, commit `bf2e56b` | proposals 034-035; W2-6 CI kit rebuild PENDING (needs push and approval) |
| 1.5 | Single-tick false-confidence fixes | n/a | DONE with follow-ups, commits `162df53`, `a0d87c9` | below |
| 3 | Campaign memory (carry), W3-A0, W3-1b, W3-1..W3-5 | 26 | PLANNED | proposal 036 (C10 v1.5, C8 v1.9); goldens captured on the merged 1-2 + 1.5 commit |
| 4 | Scale-free outcome metrics, W4-A0, W4-1..W4-4 | 12.5 | NOT STARTED | proposal 037 (C10 v1.6); after W3-4 |

### Wave 1.5 detail

| Item | State |
|---|---|
| C4 v1.3 `player_offensive_war_count` (proposal 038), parser 0.1.3, 2 fixtures | DONE |
| C10 v1.4 feature validity (proposal 039): `MIN_FRONTIER=3`, reporting-set rule, builder/tuner 0.2.0 | DONE |
| Profile path carries the evidence tick's verdict to stale log ticks (review R1) | DONE, regression test |
| Fog measure tool: (g) keeps real views, (h) partial flicker, (i) full id swap | DONE |
| Code review: CHANGES REQUIRED, then CLOSED (R1 fixed, R2/N1 TEXT, N2 deleted by maintainer) | DONE |
| Architecture review: DONE WITH FOLLOW-UPS (below) | FOLLOW-UP |

Measured (`tools/bench/fog_adversarial_measure.py`, 40 runs per row, synth, uniform observer views):

| Check | Target | Before | After |
|---|---|---|---|
| (d) war started by a managed AI | 0 wrong | 3 | 0 |
| (g) 3 observers x 1 frontier system | 0 wrong | 4 | 0 |
| (h) partial flicker, kept fraction | >= 0.5 | 1.000 | 1.000 |
| (i) every observer id swapped | by design | 1.000 | 0.042 |
| CI-1 pooled switch match | >= 0.90 | 1.000 | 1.000 (20 and 40 switches) |

### Open follow-ups

| # | Item | Owner | Where |
|---|---|---|---|
| 1 | Kept-fraction replay step for `fleet_power_cat` / `tech_cat` on a real run; act below 0.5 by taking the median over observers in both ticks | T8 / validation backlog | `docs/status/opt_wave_1_5_arch_review.md` blocking 2 |
| 2 | Telemetry: a dropped and an unreported categorical are both `null` on the bus | T4 / T6 | same review, benefit 2 AT RISK |
| 3 | Adaptive engine AT RISK: real-valued single ticks still lock (first save 0.685, `border_fort_ratio` 0.0 at 0.681) | T4 | same review, benefit 3 |
| 4 | How often a real balanced player has fewer than 3 frontier systems (thin-border lock risk) | T4, Wave 3 | `DEMO_V1_NUMBERS.md` |
| 5 | A-55 kit step in M0-11 (war screen vs save `attackers` / `defenders`) | T8 | `docs/m0_checklist.md` |
| 6 | Partial-flicker measure with differing observer values (Fable probe: 0.78-0.91 kept) | T7 | `DEMO_V1_NUMBERS.md` |
| 7 | PR #5: Windows CI job result; retarget base to `main` after PR #4 merges | maintainer | GitHub |

## 3. Components and features

Three processes: daemon, per-autosave parser that exits, optional telemetry. No LLM in the runtime loop, no GPU use
through M4. Contract change history: `contracts/CHANGES.md`; 39 proposals in `contracts/proposals/`.

| Id | Component | Paths | Functionality | State |
|---|---|---|---|---|
| T1 | Mod | `mod/sae_bridge/**` | Clausewitz driver `sae.0`, SNAP/PLYR/HB log lines, C2 apply with 8 ACK pairs, managed-set cap 16 | DONE, static only |
| T2 | Adapters + simulator | `src/sae/adapters/**` | live, replay, synth adapters, mod simulator, lockstep, PLYR redaction, write guard | DONE |
| T3 | Parser | `parser/**` (Rust `sae-parse` 0.1.3) | `parse`, `fixture`, `--version`; meta-only fast path; offensive-war count; peak-commit cost | DONE |
| T4 | Engine + daemon | `src/sae/core/**`, `src/sae/daemon/**` | fog-legal features, forgetful Bayes belief, counter table with 24-month commitments, safety floor, validator, ack lifecycle, D1 pre-emption, statefile, re-exec guard | DONE |
| T5 | Governor + platform | `src/sae/governor/**`, `src/sae/platform/**` | spm mean ratio trigger, sensors incl. private bytes, process priority rules | DONE |
| T6 | Bus + telemetry | `src/sae/bus/**`, `control/**`, `telemetry/**`, `tools/console/**` | bus writer/readers, ctl, command expiry, rerun mapping, DuckDB console views | DONE |
| T7 | QA, CI, bench | `src/sae/bench/**`, `tests/e2e/**` | CI-1..CI-5, `sae bench`, `sae gate`, metrics, demo | DONE |
| T8 | Release, docs, M0 kit | `src/sae/m0/**`, `tools/m0/**`, `docs/**` | `sae m0` (17 checks, 8 subcommands), checklist, field-kit packaging | DONE, game-free |
| T9 | Profile, tuning, policy | `src/sae/profile/**`, `src/sae/tune/**` | player profile summaries, tuner, sealed-query ledger, policy registry and rollback, `sae play` | DONE |
| X2 | M0 field kit | `tools/m0/**`, Windows CI artifact | setup.ps1, doctor, pack | DONE, not run on Windows |
| X4 | tune / gate / policy | `src/sae/tune/**`, `sae gate`, `sae policy` | detector, gate refusal, policy verbs | DONE, game checks deferred |
| X1 | Player profile + bounded RSI expansion | `docs/expansion/player_profile_and_rsi_plan.md` | profile wiring (proposals 019-025) | verify state before reporting; see `docs/status/X1_plan_review.md` |

## 4. Tests

Collected on 2026-11-21 (`pytest --collect-only`, includes `slow`): **1678** Python tests plus about 90 Rust
`#[test]` functions in `parser/`.

| Area | Python tests |
|---|---|
| `tests/unit/t2` adapters | 246 |
| `tests/unit/t4` engine, daemon | 480 |
| `tests/unit/t5` governor, platform | 118 |
| `tests/unit/t6` bus, telemetry | 147 |
| `tests/unit/t7` bench, gate | 75 |
| `tests/unit/t8` M0 kit | 96 |
| `tests/unit/t9` profile, tune, policy | 178 |
| `tests/contract` contracts and schemas | 210 |
| `tests/e2e` CI-1, CI-5, fog, replay chain, archetype acceptance | 63 |
| `tests/integration` | 50 |
| `tests/test_cli.py` | 15 |

Gates (CLAUDE.md "Testing"): tier 1 targeted while iterating; tier 2 area command before hand-back (plus
`ruff`, `mypy --strict`; `cargo clippy -j 1 -- -D warnings` and `cargo test -j 1` in `parser/`); tier 3 full
suite once per wave, coordinator only. The pre-commit hook runs ruff, `mypy --strict src/sae` and the fast tier
(unit + contract, not `slow`); the commit-msg hook requires a `Reviewed-by:` trailer.

Last full-suite result recorded: before the R1 fix, no failures and 2 skipped; not re-run in full after it
(hook fast tier passed on `162df53` and `a0d87c9`). Run tier 3 before the next wave starts.

Key behaviour tests: fog-of-war legality (`tests/e2e/test_fog*.py`, `tests/unit/t4/test_boundary.py`), record/replay
determinism (`test_replay_chain.py`), CI-1 strategy match >= 0.90 (`test_ci1.py`, currently 1.000), single-tick
property (`tests/unit/t4/test_single_tick_property.py`), proposal 038 contract (`tests/contract/test_proposal_038.py`).

Deferred (marker `game`, env `SAE_GAME_*`, run on the Stellaris PC from `docs/handoff/GAME_PC_AGENT.md`):
`docs/handoff/deferred/X2.md`, `X4.md`.

## 5. Validation backlog and M0 (needs the real game or saves)

| Id | Test | State |
|---|---|---|
| V-1 | Calibrate on 5 hand-labelled real campaigns, ECE <= 0.10 | BLOCKED |
| V-2 | Knob A/B in game, fleet power B/A >= 1.3 after 15 years (M0-8) | BLOCKED |
| V-3 | Adaptation has signal: > 1 real switch, rewards spread beyond +-0.02 | BLOCKED |
| V-4 | Turtle vs builder counters differ (M0-7), else collapse the table to 3 counters | BLOCKED |
| V-5 | Fog perturbation on real saves, belief change = 0 | BLOCKED |

M0 checklist: 14 rows M0-1..M0-14 in `docs/m0_checklist.md` (core: 1-6, 8, 11, 12; stretch: 7, 13, 14; later: 9, 10),
all UNVERIFIED. Go/no-go: M0-3, M0-6, M0-11 pass, then live dev mode is viable.

## 6. Settled decisions (do not reopen without the maintainer)

No LLM in the runtime loop. No GPU use through M4. Three processes. Never touch the game's priority or affinity.
Ironman has no live loop. v0/v1 strategy selection is a belief to counter-strategy table with 24-month commitments
and a safety floor. CUSUM is diagnostic only. Fleet-composition bias is out of v0. Full list: `CLAUDE.md`,
`docs/TEAM_PLAN.md`.

## 7. How to keep this file current

1. After each wave or merge, update sections 1, 2 and 4 in the same change set, with the commit hash.
2. Take numbers from the review and measure files, never from memory; cite the file.
3. When asked "check the sprintboard": run `git log --oneline -5`, `git status -sb`, compare with the "Last verified"
   line, check each open follow-up, list what drifted, then update the file.
4. Doc diffs go to the maintainer before any git command (CLAUDE.md rule).
