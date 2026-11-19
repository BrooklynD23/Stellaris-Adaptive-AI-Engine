# SAE optimization study: implementation waves (rev 2)

2026-11-15, branch `feat/opt-wave-a1`. Plan only; no repo file changed. Rev 2 folds in every blocking change from
`IMPLEMENTATION_WAVES_review.md` (B1-B13 and the carried-over items); see §8.

## 1. Summary

| Wave | Theme | Work orders | Hours | Demo after |
|---|---|---|---|---|
| 1 | Demo wave: faster reaction, faster tools | A0 + 9 | 23.5 | Demo v1 |
| 2 | Truthful telemetry + M0 kit | A0 + 5 (+ W2-6 CI run, not counted) | 18.5 | Demo v2 |
| 3 | Campaign memory (carry) | A0 + 6 | 26 | Demo v3 |
| 4 | Reward that ranks strategies | A0 + 4 | 12.5 | Demo v4 |

- Total: 80.5 h, plus about 1 h per `sae-adversary` gate (Fable 5.2 medium plan review before code, result review after).
- Critical path to the first demo: **W1-A0 (031) -> W1-1 with W1-8 -> W1-9 re-measure -> result review -> Demo v1.**
  About 9 h serial; the rest of Wave 1 runs in parallel.
- Wave 2 blocks the M0 weekend (kit rebuild), not Demo v1.

Every wave:
1. A0 is ACCEPTED before dependent code starts (`contracts/README.md` §3 rule 4).
2. File sets inside a wave are disjoint, except where an order is marked "after" another.
3. Implementers never touch `changelog.d/`. Each team lead writes its fragment once, after its orders merge (B6).
4. Doc diffs go to the maintainer before any git command.

## 2. Waves and work orders

Model: sonnet unless marked opus. Every order also runs `uv run pytest -x -q`, mypy strict and ruff.

### Wave 1 (23.5 h)

| Id | Team | Files (exclusive) | Proposal | Spec | Tests | h |
|---|---|---|---|---|---|---|
| W1-A0 | architect (opus) | `contracts/**`, `src/sae/contracts/**`, `tests/contract/**` | 031, 032, 033 | See §3. Also fix the C5 header (Version 1.3 vs "FROZEN v1.2") and the stale README §1 index | `uv run pytest tests/contract` | 4 |
| W1-1 | T4 (opus) | `src/sae/core/strategy.py`, `tests/unit/t4/test_strategy.py` | 031 | D1, no guard. Add `abstain: bool = False` to `StrategyState`; set only by a `below_threshold` window. One confident table decision for a counter pre-empts an abstention window and opens a 24-month counter window. Confident `balanced` during abstention opens no committed window. A `safety_floor` baseline window is not an abstention. Counter windows stay closed. No `engine_tick.py` edit: the early close emits a 1-23 month baseline outcome. No T7 file edit | (a) abstention + confident counter tick -> counter window, `commit_until` = +24; (b) table-chosen baseline (`abstain=False`) not pre-empted; (c) counter window never pre-empted; (d) safety floor still checked; (e) abstention + confident balanced -> `commit_until` unchanged, later confident turtle still pre-empts; (f) pre-emption outcome has `strategy=baseline`, `window_to - window_from` < 24; (g) `safety_floor` window not pre-empted; (h) B3: tick t posterior 0.60 turtle, t+1..t+23 uniform -> `outscale_siege` with `commitment_hold` on all 23, `commit_until` = t+24, tick t+24 back to `below_threshold`; (i) pre-W1 `state.json` loads with `abstain=False`. Run `tests/e2e/test_ci1.py`; report the pooled switch-match rate before and after | 4 |
| W1-2 | T4 | `src/sae/core/features.py`, `src/sae/core/belief.py`, `tests/unit/t4/test_belief.py`, `tests/unit/t4/test_features.py` | none | C-review B6: `expansion_rate < 0` -> `None`; clamp top at max mu. T4 fragment notes: detector behaviour changes, builtin policy 0.1.0 sha256 does not | x in [0, 10]: P(turtle) non-increasing, P(early_aggression) non-decreasing. Views identical in every other feature, one observer lost: `expansion_rate is None` and no `"expansion_rate"` evidence row. Observer lost at month 12, back at 24: record the rate and top posterior; report in the result review if it crosses 0.45 | 2.5 |
| W1-3 | T3 | `parser/src/input.rs`, `main.rs`, `extract.rs`, `parser/tests/meta_fast_path.rs`, `parser/tests/cli.rs`, `parser/Cargo.toml`, `Cargo.lock`, `parser/README.md` | 032 | B-review item 6: skip gamestate inflate when zip `meta` has `date` and `version`; keep the `gamestate` presence check; "forced full path" is a library argument, not a CLI flag; bump to 0.1.2 (`cli.rs:69` pin) | Fast vs forced-full: identical JSON except `cost` over every fixture + synthetic save, excluding by name the one accepted divergence: valid meta + corrupt gamestate -> fast exit 0, full the old error code. Meta without `date`, without `version` -> full path; malformed date and no-gamestate zip -> same exit code. `cargo clippy -j 1 -- -D warnings`, `cargo test -j 1` | 4 |
| W1-4 | T7 | `src/sae/bench/cli.py`, `tests/unit/t7/test_bench_cli.py` | none | B-review 2b: `bench parse` and `spy` pass `--background`; no "matches production" claim | Runner receives `--background` | 1 |
| W1-5 | T7 | `src/sae/bench/gate/cli.py`, `tests/unit/t7/test_gate_cli.py` | 033 | D-review 7b: import `_game_running`/`_lower_priority` from `sae.profile.cli` unchanged (edits nothing there); own exit code; `--force` | Injected `game_running=True` -> new exit code, stage1 never called, priority set on own process only | 1.5 |
| W1-6 | T9 | `src/sae/tune/fit.py`, `tests/unit/t9/test_fit_memo.py` | none | A-review item 9: memoise `fast_lls`, dicts local to `loco()` | >= 2 kappas and >= 2 thresholds: memoised `loco()` equals unmemoised for every `Candidate` and fold score; policy sha256 unchanged | 2 |
| W1-7 | T9 | `src/sae/profile/launcher.py`, `src/sae/profile/cli.py` (message only; no rename of `_game_running`/`_lower_priority`), `tests/unit/t9/test_launcher.py` | 033 | D2: daemon first, `sae profile update` after it exits; return the daemon's code. Print one line before the update (it can hold the terminal up to 600 s). Never pass `--force` | Spy order daemon then update; update exits 1 because the game runs -> prints `run sae profile update after closing the game`, returns the daemon's code; update failure only warns; pty and Windows `NUL` tests rewritten for the new order | 2 |
| W1-8 | T7 | `src/sae/bench/gate/stage1.py`, `tests/unit/t7/test_gate_stage1.py` | 031 | B1: `feel_commitment` = every switch out of a non-baseline strategy is >= 24 months after it started. Merges together with W1-1 | `counter(0-23), baseline(24-26), counter(27-50)` passes; `counter(0-10), baseline` fails | 1.5 |
| W1-9 | T7 | none (measurement, after all merge) | none | Re-measure on the merged branch: reaction lag per archetype, tune time, meta-probe time, and wrong-lock rate (first counter is not the label's counter) over `sae bench demo` seeds 0-9 x 4 archetypes | Numbers recorded with exact commands for Demo v1 | 1 |

### Wave 2 (18.5 h)

| Id | Team | Files (exclusive) | Proposal | Spec | Tests | h |
|---|---|---|---|---|---|---|
| W2-A0 | architect (opus) | as W1-A0 | 034, 035 | See §3 | contract tests | 3 |
| W2-1 | T5 (opus) | `src/sae/governor/core.py`, `tests/unit/t5/test_governor.py`, `test_governor_props.py` | 034 | B-review item 1: window all samples; ratio = mean(6)/mean(24) only when median(24) >= 5 x `poll_s`; step test unchanged; expose ratio and armed state | Real governor, 600 months, seeds 0-7: constant 11/23/40 s + 5 s flush jitter -> 0 H1; pauses (25 % of months, +2 to +10 s) -> <= 1 H1 per 100 months; +20 % step -> H1 within 8 months 8/8; +12 % -> no H1; median 9.9 s unarmed, 10.1 s armed | 4 |
| W2-3 | T5 | `src/sae/platform/sensors.py`, `tests/unit/t5/test_sensors.py` | 035 | `daemon_private_bytes` on `SensorSample`; keep `daemon_rss_bytes` | Fake process: both set; probe failure keeps the last value | 1 |
| W2-2 | T4 (opus), **after W2-1 and W2-3** | `src/sae/daemon/loop.py`, `tests/unit/t4/test_loop.py` | 034, 035 | Poll-tick sampler (count HB months in `_on_obs`, one `SpmSample` per poll). `_metrics` emits all 4 keys: `engine_ms`, `daemon_private_bytes`, `spm_ratio` (omitted when unarmed), `spm_armed` (1.0/0.0). Mode-set complement assert | 2 HBs in one poll -> one `SpmSample(months=2)` with `wall_s == (now - last_hb_poll) / pending_k`; 0 HB -> none; all 4 keys on a synth month | 4.5 |
| W2-4 | T6 | `src/sae/telemetry/mapping.py`, `tools/console/views.sql`, `tests/unit/t6/` (mapping test) | 034, 035 | Surface the 4 keys | Mapping test per key; view returns them on a synth bus | 1.5 |
| W2-5 | T8 | `tools/m0/**`, `src/sae/m0/checks.py`, `src/sae/m0/evidence.py`, `docs/m0_checklist.md`, `docs/plans/X2_field_kit_plan.md`, `tests/unit/t8/` | none | A-review B5 (pin `UV_PYTHON_INSTALL_MIRROR` to GitHub, fix the 3.12 condition, add `files.pythonhosted.org` + GitHub asset host, update D9). A-review B6 (M0-12 row required: private bytes + `daemon_rss_bytes` p95). D-review D1 blocker 1 (M0-11 parses a copy of the largest save with `--background`; records text bytes, commit, largest span). D-review 7b.4 (process name at M0-11). B-review 2b: M0-11 records bench `cpu_s` vs the daemon's `parse_cpu_s` on the same save, pass within 10 % | M0-11 check contains the largest-save step with `--background` and the CPU comparison; template has the new rows; consent text lists exactly the script's hosts | 4.5 |
| W2-6 | T7 | none (CI run; needs a push) | none | Rebuild the X2 field kit after W1-3 and W2-5 merge. Runs only after the maintainer approves the doc diffs and the git commands | Kit reports parser 0.1.2; `uv -v tool install` host list recorded | (0.5, not counted) |

### Wave 3 (26 h)

| Id | Team | Files (exclusive) | Proposal | Spec | Tests | h |
|---|---|---|---|---|---|---|
| W3-A0 | architect (opus) | as W1-A0 | 036 | See §3. Includes the split predicate as a pure function + `SPLIT_GAP_MONTHS` in `src/sae/contracts/` (B9) | contract tests; predicate equals current `profile/index.py` behaviour on its fixtures | 4 |
| W3-1b | T4 (opus) | `src/sae/core/engine.py`, `tests/unit/t4/test_engine.py` | 036 | C-review B3 in-session, **not behind the flag**: regression of D months decays the posterior `(1-rho)^D` toward uniform, uniform if D > `commitment_months`, `window_open=None`, closing outcome `metrics["void"]=1` | Reload 30 years earlier: posterior uniform, outcome void; D = 6: decayed, window dropped | 4 |
| W3-1 | T4 (opus), **after W3-1b** | `src/sae/core/engine.py`, `core/engine_tick.py`, `core/state.py`, `src/sae/daemon/statefile.py`, `daemon/cli.py`, `daemon/wiring.py`, `daemon/__init__.py`, `tests/unit/t4/test_carry.py` | 036 | C-review B1, B2, B4 behind `engine.carry_state` (default off). Carry on same `save_folder_key` + `player_sid` + contracts split predicate not firing; policy sha differs -> drop the window. Hold (no select, no batch) until keyed, cap 12 months. Merge `features`, `belief`, `strategy` only; reuse W3-1b decay. Emit `carried_from` / `carry_refused`. Imports nothing from `sae.profile` (CI-4) | Flag off: 6-year synth run, normalised bus and `state.json` equal the pre-W3 commit. Flag on, recorded live-shape fixture (fixed salt + folder): same strategy, remaining `commit_until`, no batch before keyed; 3 negatives emit `carry_refused`; `observe_only` survives; carried abstention window keeps `abstain=True`. `tests/integration/test_ci4_imports.py` green | 10 |
| W3-2 | T9 | `src/sae/profile/index.py`, `src/sae/tune/fit.py`, `tests/unit/t9/` | 036 | `index.py` calls the contracts predicate; tune skips `void` outcomes | Index fixtures unchanged; void rows excluded | 2 |
| W3-3 | T7 | `src/sae/bench/gate/metrics.py`, `tests/unit/t7/test_gate_metrics.py` | 036 | Gate / V-3 skip `void` outcomes | Void row changes no metric | 1 |
| W3-4 | T2 | `src/sae/adapters/synth.py`, `tests/unit/t2/` | none | Reacting synth: injectable per-strategy effect, default off | Default run equals today (normalised); effect on -> counter fleet growth >= +10 % vs baseline | 4 |
| W3-5 | T8 | `docs/handoff/GAME_PC_AGENT.md`, `docs/m0_checklist.md` | none | C-review validation 4: two real sessions + same-name new game; record ordering and A-46 | Doc review | 1 |

### Wave 4 (12.5 h)

| Id | Team | Files (exclusive) | Proposal | Spec | Tests | h |
|---|---|---|---|---|---|---|
| W4-A0 | architect (opus) | as W1-A0 | 037 | See §3 | contract tests | 2 |
| W4-1 | T4, **after W3-4 merged** | `src/sae/core/outcome.py`, `core/policy.py`, `core/state.py`, `src/sae/policies/**`, `tests/unit/t4/test_outcome.py`, `test_policy.py`, `test_statefile.py` | 037 | C-review B5: keys `d_log_fleet_power`, `d_log_systems`, `d_log_alloys` = `log((y+1)/(x+1))`; new builtin policy, major version. Raw fleet power is a new `AiMetrics` field with a default; `fleet_share` stays (B12) | (a) +-25 % fleet -> abs(reward) < 0.5, 0 systems does not raise; (b) reacting synth: better strategy first in >= 8 of 10 seeds; (c) baseline-only: abs(corr(reward, window start year)) < 0.3; (d) a pre-W4 `state.json` loads | 5.5 |
| W4-2 | T9 | `src/sae/tune/fit.py`, `tests/unit/t9/` | 037 | Tune never mixes old-key and new-key outcomes | Mixed history: old-key rows dropped, count reported | 2 |
| W4-3 | T7 | `src/sae/bench/gate/metrics.py`, `tests/unit/t7/` | 037 | Same rule for gate / V-3 | Mixed history test | 1.5 |
| W4-4 | T8 | `docs/**` (README, design notes, `docs/plans/opt/SYNTHESIS.md`) | none | Say the per-AI safety floor is vacuous today. "3.13/3.14 measured worse" -> "no gain". SYNTHESIS §7 gains "several parser processes for bulk re-parse" (D ordering 4). Do-not-do gains "removing EcoQoS" and "live telemetry for players" | Doc review | 1.5 |

## 3. Contract proposals (architect drafts first; one proposal, one version line per contract)

| No. | Wave | Title | Contract change |
|---|---|---|---|
| 031 | 1 | T4 baseline-abstention pre-emption (D1) | C3 §6 -> v1.3. C10 §6 `feel_commitment` rule -> C10 v1.3. C5 §2 `outcome` row wording (window end is no longer the only emit point; doc only). States: `abstain` field, `state_version` stays 1, pre-W1 state loads as not pre-emptable; confident balanced opens no window; short 1-23 month baseline outcomes exist; `safety_floor` is not an abstention. Records once: the Wave C reviewer advised deferral (one noisy save can lock a 24-month counter); maintainer approved without a guard |
| 032 | 1 | T3 meta-only fast path | C4 §1 -> v1.2; names the accepted divergence (corrupt `gamestate` not detected by a meta-only call) |
| 033 | 1 | T7/T9 gate refusal; `sae play` update after exit | C8 §5 -> v1.7: new `sae gate` exit code; `sae play` row reordered; the daemon runs on the previous session's policy; update refused while the game runs; a Windows console close kills the launcher within about 5 s, so no update runs (UNVERIFIED) |
| 034 | 2 | T5 spm mean ratio (T4 co-owner) | C7 §4.3 item 4 replaced, 14.3 % effective threshold stated -> C7 v1.2. C5 additive `spm_ratio` (omitted when unarmed), `spm_armed` (1.0/0.0) -> C5 v1.4 |
| 035 | 2 | T5/T4 flip metrics | C7 `SensorSample.daemon_private_bytes` -> C7 v1.3. C5 §3 additive `engine_ms`, `daemon_private_bytes` -> C5 v1.5. Stack flip rule restated on these keys |
| 036 | 3 | T4 carried campaign state | C8 §6 layout + `engine.carry_state` -> v1.9 (v1.8 was taken by the 2026-11-16 re-exec rule). C5 additive `carried_from`/`carry_refused`, `metrics.void` -> C5 v1.6. C10 split predicate moved to contracts -> C10 v1.5 (v1.4 is proposal 039, Wave 1.5). States which part is behind the flag |
| 037 | 4 | T4 scale-free outcome metrics | C10 `outcome_weights` accepts both key sets, chosen by PolicyVersion major -> C10 v1.6 (no v2). No-mixing rule |
| 038 | 1.5 | T3/T4 offensive war count | C4 additive `LegalView.player_offensive_war_count` -> C4 v1.3. C6 §4 doc note (no bump). A-55. ACCEPTED 2026-11-16 (`WAVE_1_5_PLAN.md`) |
| 039 | 1.5 | T4 feature validity | C10 §2 `war_share` / `first_war_year` mean offensive wars; denominator rule; HYP priors -> C10 v1.4. C5 doc note (no bump). ACCEPTED 2026-11-16 |

## 4. Demo plan (game-free only)

| After | Show | Command | What the stakeholder sees |
|---|---|---|---|
| Wave 1 | Engine picks a counter per player style | `uv run sae bench demo --years 6 --seed 2`; `uv run sae telemetry --runs-dir runs --run <id> --rrd out.rrd` | 4 archetypes -> 4 strategies; belief and intent timeline in Rerun. Quote only W1-9 figures: lag per archetype, with the wrong-lock rate (40 runs) next to it |
| Wave 1 | Tooling speed | `uv run sae tune ...`, `uv run sae bench spy ...` | Tune and meta-probe times from W1-9 |
| Wave 2 | Honest resource numbers | console views (`tools/console/query.py`) on a synth bus | `engine_ms`, `daemon_private_bytes`, `spm_ratio`/`spm_armed` |
| Wave 3 | SAE remembers a campaign | two runs of the recorded live-shape fixture with `engine.carry_state=true` | Session 2 shows `carried_from=<run_id>`, same strategy. Say: flag is off by default; identity rests on UNVERIFIED A-46; the hold delays the first reaction by up to 12 months |
| Wave 4 | Reward ranks strategies | reacting synth, 10 seeds | Better strategy first in >= 8 of 10 |

Study figures (lag 21 -> 0, tune 54 -> 12 s, probe 0.236 -> 0.007 s) are not demo numbers. Re-measure all on the
merged branch; W1-2 lands in the same wave, so the lag change is not from D1 alone.

Overclaim flags to state in the demo:
1. Every adaptiveness number is synth and circular: the likelihoods were fitted to the synth generator. V-1 to V-5 are open.
2. Do not quote "belief flip 28.5 -> 3.0 months" (synth territory collapse).
3. No in-game effect is proven (V-2 / M0-8). Memory and CPU figures are WSL2; Windows is UNVERIFIED.
4. The Wave 4 ranking uses an injected effect; it proves the reward maths, not Stellaris behaviour.
5. The safety floor is vacuous today (`strategy.py:73-75`); do not cite it as protection.

## 5. Deferred, needs game PC / M0

| Item | Trigger |
|---|---|
| B 2c thread background mode | Game-PC A/B, available RAM < 2 GiB, n >= 10 per mode, game faults and frame p99 no worse (R-5 / A-28) |
| Item 8 hot-folder polling | M0 records the save-folder count; then the A-review B4 list + C6 §1 proposal |
| Streaming filter (+ zlib-rs, bundled) | M0-11 largest save: commit > 250 MB or > 25 % of `avail_ram_bytes`; after A-19 key fixes. Whitelist derived from `Need` (D1.3). Differential + mutation corpus, window sizes 1/2/7/4096, same exit code and failure kind (D1.4) |
| `MAX_SPAN` | M0-11 largest entry span: max(16 MiB, 4 x span) |
| Turn `engine.carry_state` on | M0-11 two-session test passes, A-46 holds |
| Stack flip (msgspec) | M0-12: private bytes > 100 MiB, CPU > 1 % of a core, or `engine_ms` > 50 |

Already done on this branch: fsync gating, `stdin=DEVNULL`, Windows peak commit (030, C4 v1.1, parser 0.1.1), and
the A1 `black_box` fix in `cost.rs`.

## 6. Risks

1. D1 makes one confident tick a 24-month lock. Now pinned by W1-1 test (h) and measured by the W1-9 wrong-lock rate.
2. W3-1 is 10 h and rests on UNVERIFIED A-46. Mitigation: default-off flag and the flag-off equality test.
3. `loop.py`, `engine.py`, `tune/fit.py`, `gate/metrics.py` are touched in more than one order. Run them in the stated order.
4. D2: the update is refused while the game is open and skipped on a Windows console close, so some sessions never update.
5. W1-2 fog-recovery spike can feed D1 a false confident tick. W1 only measures it.

## 7. Planner's own decisions (beyond the inputs)

1. Pre-empt only `below_threshold` windows; `safety_floor` baseline is not an abstention.
2. Parser patch bump to 0.1.2.
3. `engine.carry_state`, default off until M0-11; the in-session regression fix (W3-1b) is not behind it.
4. UV mirror pinned to GitHub (A-review option 1).
5. Carry hold cap of 12 months.
6. Version targets as in §3; 034 and 035 stay separate proposals with sequenced versions.
7. Reacting synth (W3-4) placed in Wave 3 so Wave 4 can use it.
8. Gate reuses the guard from `sae.profile.cli`; move it to `sae.platform` when a 4th call site appears.
9. Profile-update cache dropped (D2 makes it unnecessary for now).
10. `sae play` keeps `stdin=DEVNULL` after the reorder.
11. W1-9 added as a measurement order so demo numbers come from the merged branch.

## 8. Changes from review

| Id | Landed in |
|---|---|
| B1 | W1-8 (new); 031 amends C10 §6 and the C5 `outcome` row; W1-1 runs `test_ci1.py` |
| B2 | W1-1 spec + tests (b), (e), (f), (g), (i); 031 text |
| B3 | W1-1 test (h); W1-9 wrong-lock rate; Demo v1 row |
| B4 | W1-2 tests and T4 fragment note |
| B5 | §3 versions: C10 v1.3/1.5/1.6 (v1.4 = 039, Wave 1.5), C5 v1.4/1.5/1.6, C7 v1.2/1.3; 037 accepts both key sets; C5 header fix in W1-A0 |
| B6 | W1-3 file set + divergence test; W1-5/W1-7 `profile/cli.py` rule; changelog rule in §1; W1-7 game-running test; 033 text |
| B7 | W2-2 emits 4 keys, runs after W2-1 and W2-3; 034 float rule |
| B8 | W2-6 marked, out of the estimate |
| B9 | W3-A0 predicate in contracts; W3-2; W3-1 |
| B10 | W3-1 flag-off test and `abstain` test; W3-1b (new) |
| B11 | Demo v3 row |
| B12 | W4-1 field with default, test (d) |
| B13 | W4-1 "after W3-4 merged" |
| B 2b CPU check | W2-5 (M0-11) |
| D1.3, D1.4 | Deferred table, streaming-filter row |
| D ordering 4, do-not-do additions | W4-4 |
| A1 `black_box` | Already done on this branch (§5) |
| Non-blocking 1, 2, 3, 6 | Demo wording; overclaim flag 5; W1-7 line; W3-1 split |

## 9. Rev 2.1 — Fable closure check (GO for Wave 1), mandatory edits

1. **W1-1 test (d), floor failure during pre-emption:** when a pre-emption attempt fails the safety floor (`core/strategy.py:103-105`, e.g. a policy's `templates` lacks the strategy), the abstention window stays untouched: `abstain=True`, `commit_until` unchanged, no `WindowClosed`. Test (d) asserts exactly this.
2. **W1-1 and W1-2 file sets:** each may update expectations in `tests/unit/t4/**` and `tests/e2e/**` that its behaviour change breaks, listing every such file in its hand-back. `tests/unit/t7/**` failures go to W1-8. No assertion is weakened.
3. **W1-1 stop rule:** if the `test_ci1.py` pooled rate falls below 0.90, stop and escalate to the maintainer. The threshold is never edited.

Before Demo v1 (not before code): add overclaim flag 6 (meta fast path UNVERIFIED on real saves, A-18); W1-7 prints the fix-it hint on any exit 1 from `sae profile update`; W1-9 commits the wrong-lock one-liner with the recorded commands.

## 10. Amendment from the Wave 3 design gate (2026-11-15)

Review B6 (`docs/status/opt_w3_design_review.md`), design `docs/plans/opt/W3_DESIGN.md` rev 2 supersedes the
Wave 3 rows above (35.5 h). W4-1 and W4-3 each gain: "every new reader of an outcome `reward` calls
`outcome_is_void`; contract test D-10b stays green".
