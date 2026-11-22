# Engine-core optimisation review

Reviewer: engine-core specialist, 2026-11-14. Scope: `src/sae/core`, `src/sae/governor`, belief, counter table,
intents. Builds on `docs/plans/ARCH_OPTIMIZATION_REVIEW.md` ("the baseline"). Read-only: no code, contract or
other doc changed. Needs a `sae-adversary` review before any wave starts.

Measured on WSL2, Python 3.12.3, 34 synth runs (1,720 game years). Synth is circular (likelihoods match the
generator) and its AIs never react to intents, so every adaptiveness number is a property of the mechanism, not
evidence about real players. Scripts:
`/tmp/claude-1000/-mnt-c-Users-DangT-Documents-GitHub-Stellaris-Adaptive-AI-Engine/d4efc1c5-69e5-4ddc-879f-1e378d064932/scratchpad/opt/engine-core/`.

## 1. Verdict

1. **Speed and memory: change nothing.** The engine tick costs 0.20 ms per game month (0.44 ms at 16 AIs); core
   plus governor add 0.0 MB to the daemon; engine state is bounded.
2. **The gains are adaptiveness.** Sessions forget the campaign, the reward is a sign function, one likelihood has
   broken tails, and 79 % of the reaction time to a style change is commitment wait.
3. **Risk:** tuning on synth. Ship the structural fixes (1-3 below), then confirm with V-1 and V-3 on real saves.

## 2. Ranked recommendations

| # | Change | Path | Gain (method) | Effort (h) | Risk | Contract | Team |
|---|---|---|---|---|---|---|---|
| 1 | Carry belief, feature memory and commitment across sessions of one campaign, keyed by `save_folder_key` | `core/state.py` (store the key), `daemon/cli.py:79-84`, first keyed observation in `daemon/loop.py` | Measured, real engine on session 2 of a recorded campaign: a fresh state spends 24 of 120 months in baseline and writes 2 batches, the first reverting applied counters to baseline (log-first order), or picks a counter from 1 save (save-first order; which order happens live is UNVERIFIED). Carried state: 0 baseline months, 0 batches | 12 | Medium: key stability is UNVERIFIED (A-46) | Proposal (C8 §6; C5 note: a window may span runs) | T4 + architect |
| 2 | Scale-free outcome metrics (log or relative deltas) | `core/outcome.py:20,55,98-99` | Measured: fleet power ±2 % in a window gives reward ±0.92, ±5 % gives ±0.999. Synth gives 98.8 % of 826 rewards within ±0.02 because its AIs never react. No gradient either way | 5 | Low | Proposal (C10 `fixed.outcome_weights`, new PolicyVersion) | T4 + T9 |
| 3 | Clamp `expansion_rate` to [min μ, max μ] before the Gaussian | `core/belief.py:100-101`, mirroring `:98` | Measured: a rate ≤ −2 systems/year votes early_aggression, up to the full clip. Synth switches: belief flip 28.5 → 3.0 months, switch-to-counter 40.0 → 14.5, stationary accuracy 0.978 → 1.000. Synth overstates it (territory collapses at a switch) | 2 | Low | None; rerun CI-1 and synth acceptance | T4 |
| 4 | Game-month parse floor at H0 (config, default 3) | `daemon/saveparser.py:94-104`, `governor/core.py:303-308` | Estimated: 67 % fewer parser runs if autosave is monthly (UNVERIFIED setting), about +1 month reaction. Measured: evidence every 6 months instead of 3 costs +3.0 months switch-to-counter, accuracy unchanged | 4 | Low | Proposal (C7 §3, one number) | T5 + T4 |
| 5 | Stop keeping every `FixtureSpec` | `adapters/synth.py:165,314` | Measured: peak RSS 44.4 MB at 30 years, 57.6 MB at 200. All growth is this list. Test adapter only (tune, gate, CI) | 1 | Low | None | T2 |

Validation: (1) two-session replay test: 0 baseline months and 0 batches in session 2. (2) Unit test: ±25 % fleet
change gives |reward| < 0.5. (3) Property test: P(turtle) never rises with expansion rate. (4) Parse count per game
year ≤ 4 at H0. (5) RSS at 200 synth years within 2 MB of 30 years.

## 3. Evidence

### 3.1 Cost (measured, `fsync` off)

| Item | Number |
|---|---|
| `engine.on_observation`, pure harness | 196 µs per game month at 4 managed AIs, 436 µs at 16 (`max_managed`) |
| Of which expansion plus signature | 65 µs (33 %) at 4 AIs, 224 µs (52 %) at 16 |
| Same call inside the daemon, medians | 275 µs: features 40, belief 19, strategy 28 |
| Rest of the consumer per month, medians | publish 213 µs, `save_state` 286 µs (p95 8.8 ms without `fsync`), `dir_bytes` 118 µs. All in: about 0.9 ms (baseline said under 5 ms) |
| Governor | `poll` 88 µs every 2 s, `on_month` 9.5 µs |
| Memory | Daemon import graph 38.3 MB. Core, policy and governor on top of the contract models: 34.4 → 34.3 MB |

At 10 s per game month (UNVERIFIED pace) the engine tick is 0.004 % of one core. `state.json` is 8 KB (39 KB at 16
AIs with no ACKs). At exit 0 `Observation` objects are alive.

### 3.2 Adaptiveness (synth, current code unless stated)

1. Campaign start: belief is correct and above 0.45 at month 3; the counter starts at month 24. The first tick has
   no evidence and still opens a 24-month baseline window (`core/strategy.py:102,119-126`).
2. Style switch, 6 switches × 24 window phases (n = 144): after the belief flips, the wait for the window to end is
   mean 11.5 months, p90 21, max 23. With change 3 that is 79 % of the 14.5-month reaction.
3. New session: `sae play` always starts a new run with `EngineState()` (`profile/launcher.py:68`,
   `daemon/cli.py:79-84`, `core/engine.py:46`).
4. First evidence gets full weight (`core/features.py:23`, `core/belief.py:163`): one save moves the top posterior
   from 0.25 to 0.60-0.98, enough to open a 24-month counter window. Input for V-1, no change proposed.
5. Belief evidence per year does not depend on parse cadence by design (`w = k/12`, `core/belief.py:163`), while
   the parse gate counts saves, not game time (`daemon/saveparser.py:101-102`).

### 3.3 Where I differ from the baseline

1. Baseline 4 says "widen the outcome reward's range". Wrong cause: the simulated AIs ignore intents
   (`adapters/synth.py:230-232` keeps fleet/naval cap at 80), and on any reacting world the reward saturates. The
   V-3 check `spread > 0.04` (`tune/cli.py:80`) would then pass on saturation alone.
2. Baseline 3 (`economy_cat`) stays valid but ranks after changes 1-3; its gain cannot be measured on synth.
3. "Already incremental": agreed. The monthly re-expansion is the only full recompute and costs 65-224 µs.

### 3.4 Sources

| Claim | Source |
|---|---|
| Fixed-share forgetting, the update the belief uses | Herbster and Warmuth, "Tracking the Best Expert", Machine Learning 32, 1998: https://mlanthology.org/mlj/1998/herbster1998mlj-tracking |
| Autosave frequency is a player setting (Settings, Gameplay) | https://stellaris.paradoxwikis.com/User_interface |

Not found: a primary source for the autosave interval options and their default (UNVERIFIED).

## 4. Do-not-do list

| Idea | Why it loses |
|---|---|
| Skip expansion and signature when nothing changed | Saves at most 224 µs per game month (16 AIs) |
| Suppress no-op `belief_update` and `commitment_hold` events | 41 % of events and 24 % of bytes, but the bus is 4.8 KB per game month; needs a C5 change |
| pydantic `defer_build` | Measured: import 38.1 → 37.3 MB, after a 30-year run 44.6 → 45.6 MB |
| Dataclasses or slots for `EngineState`, numpy for the belief | All `model_copy` calls total 0.04 s per 30 game years under cProfile; a belief update is 19 µs |
| Tune ρ, clip or threshold on synth | Circular; this is `sae tune` on labelled campaigns (V-1) |

## 5. Needs maintainer decision

| Topic | Settled text it touches | Benefit (measured on synth) | Cost | Recommendation |
|---|---|---|---|---|
| A `below_threshold` window is an abstention: one table decision may pre-empt it | C3 §6 "every strategy is held for `commitment_months`" | Confident belief to counter at campaign start: 21 → 0 months for 3 of 4 archetypes. Months in baseline 18.5 % → 16.0 %. Strategy changes unchanged (55 vs 55 over 34 runs) | Short baseline reward windows; the T4 hypothesis test and the gate "feel" bound need an exception; 6 h | Approve. Counter windows keep 24 months |
| Early break of a counter window (CUSUM or belief flip) | CUSUM diagnostic only; 24-month commitment | Removes up to the 11.5-month mean wait (79 % of reaction time) | Oscillation risk; shorter reward windows | Keep closed until V-3 runs on real recordings |
| Per-AI safety floor from each observer's own `fleet_power_cat` | The floor is a static check that is always true (`core/strategy.py:73-75`) | Not measurable: synth AIs do not react; posture effect UNVERIFIED (A-23) | C3 proposal, about 10 h | Decide after M0-6 |

Next action: send changes 1-3 to `sae-adversary` for the pre-implementation review.
