# Architecture alignment review 2 (Fable, 2026-10-19, post T9 expansion wave X0 / WU-1..WU-4, WU-8)

Scope: the wave against the design report, the resource proposal + signoff, contracts (CHANGES.md W1-1..W1-11, C10 v1),
`docs/expansion/player_profile_and_rsi_plan.md` §8 (D1–D6), the validation backlog and review 1
(`docs/status/ARCHITECTURE_alignment_review.md`). Read-only; game behaviour stays UNVERIFIED. Measurements: WSL2,
synth adapter, commands in §6.

| Benefit | Verdict | One line |
|---|---|---|
| 1. Performance and memory | **ON TARGET** | 60 synth years: peak RSS 47 MiB, bus 4.7 KB/month (11.4 MB per 200 years); new policy/registry reads are capped at 1 MiB |
| 2. Telemetry | **ON TARGET** | review-1 gaps closed and measured on the bus (`spm_med6/24`, `parse_*`, `deferred` audits, `v_runs`); one new noise source (§2 R1) |
| 3. Properly adaptive engine | **AT RISK** (unchanged) | policy seam done; evidence still circular: 60 years, 1 switch, 29 rewards in [−0.014, +0.018] |
| 4. Ready to learn from saves | **AT RISK** | S1/S4/S5 landed; the hygienic recording the learning loop depends on no longer replays cleanly (e2e red, root cause below); index/profile writers not started |

## 0. Settled decisions and D1–D6

Checked: no LLM/GPU imports (CI-4 closure unchanged); three processes; priority untouched; runtime self-modification
off (`daemon/policy_resolve.py` reads the policy once before `open_sink`, `wiring.py:89`); D1 023 parked, no
`GodView.snapshot`; D2 priors not in the engine (`c8_config.py:81` "daemon reads no profile file"); D4 no channels or
signing; D5 `sae play` in C8 §5 only, dispatcher untouched (W1-11). No decision reopened.

## 1. Performance and memory — ON TARGET

Measured, `uv run sae synth --years 60 --record` (run `20261020-031847-synth-82a9`):

| Metric | Value | Review 1 (200 y) | Budget |
|---|---|---|---|
| Peak RSS (uv + daemon) | 47,352 KB = 46 MiB | 55 MiB | ≤ 150 MiB |
| Wall / CPU | 29.7 s, 3.2 user + 1.9 sys | 110 s | — |
| `bus.jsonl` | 3,405,959 B / 720 months = 4.73 KB/month → **11.4 MB / 200 y** | 4.5 KB/month | ≤ 100 MB |
| `bus.god.jsonl` | 294,454 B | — | ≤ 300 MB |
| Recording (60 y, 720 fixture saves, redacted) | 1.5 MB | — | `record.max_bytes` 4 GiB cap, then saves stop (`recording.py:46-56`) |

New code paths are bounded: policy file and registry ≤ 1 MiB (`policy_resolve.py:29-30`), `recorded_sha` reads one
line capped at 1 MiB and refuses an unterminated line (`policy_resolve.py:155-163`), salt file exactly 32 bytes with
`O_CREAT|O_EXCL` (`keys.py:49`). `load_salt` runs synchronously on the event loop once (3 × 10 ms retry worst case):
acceptable. Bus growth from `save_folder_key`, `game_version` and the three `parse_*` keys is +5 %.

Risk (minor): `record.max_bytes` counts `game.log` bytes but only stops save copies (`recording.py:40-42`): the log
itself is uncapped by design; at ~10 KB/month that is harmless, documented in C8.

## 2. Telemetry — ON TARGET

Review-1 actions verified on the bus, not only in code:

- `governor/core.py:100` now queues `Trigger.BUDGET_BREACH` (review-1 #1).
- Monthly `health` carries `spm`, `spm_med6`, `spm_med24`, `parse_rss_peak_bytes`, `parse_cpu_s`, `parse_wall_s`
  (`daemon/loop.py:262-278`; measured keys in run `…-82a9`) (review-1 #2).
- Deferred batches are audited as `intent_rejected` rule `deferred` (`core/engine_tick.py:51-57,117,128`) (review-1 #3).
- `run_started.policy` = `{version, sha256, source, fallback_reason}` measured; `tools/console/views.sql` `v_runs`
  exposes it; `policy_pin_mismatch` emitted once per process (`loop.py:321-332`, docstring states the reexec caveat).

R1 (new noise, owner T2+T7): a replay of a hygienic recording emits one `health log_parse_error`
`invalid: SAE|v1|PLYR|<date>` **per game month** (24 in a 2-year replay, run `…-032108-replay-f7b7`), because the
recorder writes the bus-style redacted PLYR stub into `game.log` (`recording.py:34`) and the replay parser rejects it.
Real replays would carry 2,400 spurious health lines per 200 years. Fix in §4.

R2 (minor, T8): `.coverage` is untracked at the repo root and not in `.gitignore`; T4 reports a corrupted baseline from
concurrent writers. Add `.coverage*` to `.gitignore` and set `[tool.coverage.run] parallel = true` or
`COVERAGE_FILE` per worker.

## 3. Properly adaptive engine — AT RISK (unchanged)

Landed: a versioned, hashed policy drives the detector and templates (`core/policy.py` `from_doc`,
`loop.py:313-319` passes `likelihood/templates/outcome_weights` into `EngineCtx`); built-in 0.1.0 is pinned equal to
the code constants (`tests/unit/t4/test_policy.py:75,81`) and `likelihood_source: "synth_aligned"` is now recorded in
the policy's `TuningManifest` (review-1 3a done). `test_builtin_policy_is_identical_to_no_policy_and_other_tables_change_output`
(`test_policy.py:415`) proves a different table changes output: the seam is live, not decorative.

Not moved: 60 synth years give 456 months baseline → 264 months `outscale_siege`, **1 switch**, 29 outcome rewards in
**[−0.014, +0.018]**, 0 `intent_rejected`. Same shape as review 1. The `early_punish`/`outscale_siege` template
identity (review-1 §3.2) was not re-examined this wave and has no CHANGES entry: still open (V-4). The C8
`engine.*` → null flip is staged (W1-1); until then a resolved config written with every field present always wins
over the policy (`policy_resolve.py:186-190`, documented), so a resumed run cannot pick up a policy's detector numbers
except through the CLI's persisted effective config (`daemon/cli.py:127-128`, tested at `test_policy.py:229`).

Evidence that changes the verdict is unchanged: V-1 (6 labelled campaigns, D3), V-2 (M0-8 knob A/B), V-3 (switches
and reward spread on real recordings).

## 4. Ready to learn from saves — AT RISK

Seam status from review-1 §4:

| Seam | Status | Evidence |
|---|---|---|
| S1 policy identity | **done** | `PolicyRef` on `run_started`; `policy_sha256` canonical JSON; `fold_registry` chain `propose→prereg→stage1→stage2→approve`, promote needs full chain, rollback only to builtin/promoted (`c10_profile.py:376-416`); pin must be in `approved` (W1-3) |
| S2 campaign identity | **half** | `save_folder_key` (HMAC, salt per install) + `game_version` on save observations, measured `f-synth-2270a4ba` / `Cepheus v4.4.4`; `CampaignEvent` model exists; no writer (WU-5), so no `campaigns.jsonl` yet |
| S3 snapshot | parked (D1) | correct |
| S4 labels | **done for synth** | `label.json` written by `sae synth --record` (`recording.py:60-70`), measured; human labels (`source` other than `synth_schedule`) have no tool yet (WU-6) |
| S5 hygiene | **done, with a regression** | cap + `every_n` + redaction; `redact_plyr=false` is a config error outside `dev` (`c8_config.py:122`) |

**Blocking regression (B1, owner T2 + T7, architect for the one-line C6 §3 wording):** `tests/e2e/test_replay_chain.py::test_echo_chain_clean`
is red because 022's redaction strips the PLYR line to `SAE|v1|PLYR|<date>`; the replay echo simulator registers the
human country only from a parsed `PlayerRecord` (`adapters/replay.py:278-280`), so every intent targets a sid that is
not `living` and is acked `REJECTED/BAD_TARGET` (`simulator.py:432-434`). Reproduced: original synth run 3 × APPLIED;
replay of its recording 3 × REJECTED, identical decision stream otherwise. Proposal 022 line 35 ("a replay of a
redacted recording is unchanged for the engine") is true for beliefs and intents and false for acks and health, which
is exactly what the fog tests F1 compare. Consequences: the e2e gate is red, V-5 real-recording replays would never
show an APPLIED ack, and I1 evaluators that read `intent_ack` would be scored on simulator artefacts.
Smallest fix: keep `sid=<n>` in the recorded PLYR stub (`SAE|v1|PLYR|<date>|sid=<n>`; the sid is a country index,
not god data — C1 §53 lists it beside `name`, which stays dropped) and have `parse_log_line` accept that minimal
PLYR form as a `PlayerRecord` with no stats. That removes R1 as well. Alternative if the architect wants C1 untouched:
drop PLYR lines from recordings entirely and let the echo simulator register the human from `HELLO`.

Fairness check: `save_folder_key` and `game_version` reach the engine on the legal `Observation`; neither is used by
`core/features.py`, `belief.py` or `engine_tick.py` (grep: 0 hits) and neither reveals fog information. F4 greps
`core/` and `daemon/` for god and profile names (`tests/integration/test_fog_f4.py`) with non-vacuity checks. F0's
`LeakyAssembler` mutant is caught (`tests/e2e/test_fog.py:40`). F1's PLYR leg is vacuous by construction (lines are
appended after the last save; `docs/status/T7.md:45-48`) and is the same seam B1 breaks: fixing B1 makes the PLYR leg
real.

Registry trust: `by: human:<name>` is a self-declared string checked only by shape (`c10_profile.py:350-361`); any local
process can write an "approve" row. Acceptable under D4 (personal only) but must be stated in C10 §5, and WU-7's
`append_registry_row` must at least require a TTY for human rows as plan §5.3 says.

## 5. Are the new tests meaningful?

Sampled: `tests/unit/t4/test_policy.py` (36 tests), `tests/unit/t2/test_wu3.py` (29), `tests/unit/t5/test_win32.py`
(15), `tests/contract/test_c10_profile.py` (44), `tests/e2e/test_fog.py` (2), `tests/integration/test_fog_f4.py` (4).

- Meaningful: every `_Fallback` reason has a test with a real file shape (missing, too large, invalid, not JSON, hash
  mismatch, seq gap, unreadable, pinned-not-approved); resume refusal is parametrised over corrupt first lines
  (`test_policy.py:297-317`); the salt race patches `os.open` to raise `FileExistsError` (`test_wu3.py:68`); the cap test
  is property-style (`test_gate_never_exceeds_cap`); the fog tests carry sensitivity controls (F0 mutant, "vacuous"
  asserts).
- Coverage-driven but honest: `test_registry_policy_with_different_fixed_tables_falls_back` monkeypatches `_load_file`
  to reach a branch `PolicyVersion` validation already makes unreachable (T4 says so). Keep, but mark the branch
  `# defence in depth` so nobody deletes the test when the branch is "obviously dead".
- Weak: `t5/test_win32.py` proves call shapes against fake DLLs, not behaviour; still the only Windows evidence besides
  R-5. F4 is a name grep, easily dodged by a rename; it is a smoke check, not a boundary proof (CI-4's import closure is
  the real one).
- No padding found: every team reported baseline ≥ 95 % and added no coverage-only tests; total 96.67 %, 0 pragmas
  added this wave. The `.coverage` corruption (R2) means the "before" numbers in team reports are approximate.
- Not green: 1 failed / 1080 passed. A wave is not done with a red e2e gate.

## 6. Commands

`/usr/bin/time -v uv run sae synth --years 60 --runs-dir <scratch>/runs --record <scratch>/rec60`;
`uv run sae synth --archetype turtle --years 2 --seed 1 --record <scratch>/e2e/rec` then
`uv run sae replay <scratch>/e2e/rec --speed max --ack-model echo`; bus counts via a 12-line Python read of `bus.jsonl`.

## 7. Top corrective actions

1. **T2 + T7 (architect: one C6 §3 line):** fix B1 — recorded PLYR stub keeps `sid`, replay accepts it as a stat-less
   `PlayerRecord`; `test_echo_chain_clean` green and `log_parse_error` count 0 on a replayed hygienic recording. Blocks the wave.
2. **T7:** apply proposal 024 (`tests/contract/test_doc_examples.py:52,104` glob `C[0-9]*_*.md`) so C10's examples are
   checked; today C10 v1 has zero doc-example coverage.
3. **T8:** `.coverage*` in `.gitignore`; `[tool.coverage.run] parallel = true` (or per-run `COVERAGE_FILE`) so team baselines
   stop corrupting each other.
4. **T4 + architect (WU-2 close):** land the staged `engine.*` → null flip (W1-1) with the `check_resume` sha-mismatch
   CLI exit-1 test T6 handed over; until then a resumed resolved config silently overrides the policy.
5. **Architect:** state in C10 §5 that `by: human:*` is trust-on-write and that WU-7's writer requires a TTY; add the
   `early_punish` vs `outscale_siege` template decision (review-1 3b, V-4) to WU-2 or TEAM_PLAN so it stops being dropped.

Bottom line: budgets, telemetry and the policy seam are now real and measured, and no settled decision was touched.
The one thing the learning loop needs most, a recording that replays the way the live run went, broke under the new
hygiene and is visible as the red e2e test; fix that before calling the wave done.

## Review 2 follow-up (Fable, 2026-10-20)

Re-verified by running the named tests, `uv run mypy --strict src/sae`, and reading the files. Game behaviour stays UNVERIFIED.

| # | Action | Status | Evidence |
|---|---|---|---|
| 1 | B1 PLYR stub keeps `sid`; replay accepts it | **CLOSED** | `src/sae/adapters/recording.py:21-29` `_plyr_form` writes `SAE\|v1\|PLYR\|<date>\|sid=<n>`; `src/sae/contracts/c1_log.py:120` `PlayerRecord.name: str \| None`; `src/sae/adapters/replay.py:280` names a stub human `sid <n>`; `contracts/C6_game_adapter.md:153-157`. Tests green: `tests/e2e/test_replay_chain.py::test_echo_chain_clean`, `tests/unit/t2/test_replay.py::test_hygienic_recording_replays_without_parse_errors` (0 `log_parse_error`), `tests/contract/test_doc_examples.py::test_plyr_stub_round_trips_byte_identical`. R1 noise closed by the same change. |
| 2 | Proposal 024 doc-example glob | **CLOSED** | `tests/contract/test_doc_examples.py:52,104-105` glob `C[0-9]*_*.md`, asserts 10 contracts; C10 examples now validated. |
| 3 | `.coverage*` ignored, parallel coverage | **CLOSED** | `.gitignore:17-18`; `pyproject.toml:58-61` `[tool.coverage.run] parallel = true`. |
| 4 | `engine.*` → null flip (W1-1) + resume sha-mismatch exit-1 test | **CLOSED** | `src/sae/contracts/c8_config.py:56-59` `rho/likelihood_clip/belief_threshold` default `None`; `src/sae/daemon/policy_resolve.py:175`; `tests/unit/t4/test_cli.py:103` `test_resume_with_changed_policy_sha_exits_1` asserts resolved `engine.rho is None` and exit 1 on a new sha. Passes. |
| 5 | C10 §5 trust-on-write + TTY; D-V4 tracked | **CLOSED** | `contracts/C10_profile_policy.md:268-278` (trust-on-write, `isatty()` refusal, test required); `docs/TEAM_PLAN.md:132-137` decision D-V4, validation row V-4 line 231. The TTY writer itself is WU-7 (not started), so the refusal test does not exist yet: tracked under WU-7, not this action. |

Verdicts (unchanged unless stated):

| Benefit | Verdict | Change |
|---|---|---|
| 1. Performance and memory | **ON TARGET** | No new measurement this follow-up; the flip and stub add no allocation paths. |
| 2. Telemetry | **ON TARGET** | R1 (2,400 spurious `log_parse_error` per 200 y) closed; R2 closed. |
| 3. Properly adaptive engine | **AT RISK** | Unchanged on evidence: still 1 switch / 60 synth years. The flip now lets a resumed run follow the policy, which removes the silent override, not the circularity. V-1..V-3 still open. |
| 4. Ready to learn from saves | **AT RISK** | B1 closed, hygienic recordings replay with APPLIED acks and 0 parse errors. Index/profile writers (WU-5/6/7) still not started; F1 PLYR leg still vacuous (T7 KNOWN GAP V-5, `tests/e2e/_fog.py` appends PLYR after the last save). |

Still red, not covered by actions 1-5:

1. **T2:** `tests/unit/t2/test_synth_record.py:92` builds `EngineCtx(cfg=SaeConfig().engine)` with null `belief_threshold`; raises `ValueError` at `src/sae/core/strategy.py:69`. Fix: build the ctx from the resolved/effective config (the same helper the CLI uses after `resolve_policy`). Full suite: 1 failed / 1097 passed.
2. **T8 (`src/sae/m0/**`, TEAM_PLAN line 42):** `src/sae/m0/evidence.py:182` mypy strict: `names` is `str | None`-typed; `"[" in n` and `sum(... for n in names if ...)` fail. Guard `None` before the comprehension.
3. **T7:** make the F1 PLYR leg non-vacuous by injecting PLYR lines before a save offset in `tests/e2e/_fog.py` (V-5).
4. **T4 + maintainer:** D-V4 decision waits on M0-7; no action until then.
5. **T9 (WU-7):** registry writer with `isatty()` refusal and its test, per C10 §5.

Wave gate: actions 1-5 closed, but the pytest and mypy gates are red on items 1 and 2 above. Not done until both are green.
