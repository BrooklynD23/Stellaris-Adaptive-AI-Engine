# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Teams write fragments in `changelog.d/<TEAM>.md`; T8 merges them into `[Unreleased]` at Wave 3 with
`uv run python3 packaging/merge_changelog.py` (dry run: `--check`). The Wave 3 merge ran on 2026-10-19 and was then
edited by hand (duplicates dropped); rerunning the tool would overwrite those edits.

## [Unreleased]

Nothing here has run against the real game: every Stellaris script, console and save behaviour is UNVERIFIED
until M0 (`docs/m0_checklist.md`); release gates R-1..R-6 have not run. Contract history: `contracts/CHANGES.md`.

### Added

#### T1 Mod

- `sae_bridge` 0.1.0 Clausewitz mod: global monthly driver `sae.0`, which provides:
  - `sid` and `m` counters on the anchor country
  - staggered SNAP/PLYR records and a monthly HB that `sae_quiet` never suppresses
  - HELLO at game start and on first initialisation
  - C2 §5 apply with all 8 ACK status/code pairs
  - the `sae_emit_period`/`sae_quiet` knobs
  - flag-gated additive budgets, posture/stance opinion modifiers, and the `sae.10` flavour popup with 6 reason texts
- `mod/sae_bridge/SEMANTICS.md`: normative spec for T2's mod simulator.
- `mod/sae_bridge/UNVERIFIED.md`: generated per-construct assumption list for T8's M0 checklist.
- `mod/sae_bridge/tools/check_mod.py` static checker with tests.
- Managed-set cap `@sae_max_managed = 16` enforced in the mod (C6 §2, proposal 006 R4/R11); A-42 fallback `name=?`.
- Staged values must be integers: fractional `sae_seq_pending` is silently reset; any other fractional `sae_p_*` → `REJECTED/RANGE` (SEMANTICS R12).

#### T2 Adapters and bridge I/O

- `sae.adapters`: `make_adapter(config, parser, god_sink)` composition-root factory; `LiveAdapter` (live and release modes): byte-exact `game.log` tailer with truncation/rotation detection, autosave watcher (8 s monotonic stability, newest-only), atomic `sae_in.ini` writer with Windows retry, ACK matcher, fog-legal C6 observation assembly with god data routed only to the god sink, multiplayer write guard (C6 §1.6), optional session recording.
- `ReplayAdapter` and `SynthAdapter` (pull-driven, deterministic at `speed=max`) with ack models `echo`, `drop:<p>`, `delay:<k>`, `from_recording`; synth renders archetype fixtures through `sae-parse fixture`.
- Python mod simulator reproducing `SEMANTICS.md` §§1–8 (R1–R11), line-for-line with the mod's log templates.
- `sae replay` and `sae synth` CLIs (C8 §5).
- Follow-ups: keyword-only `health` hook on `make_adapter` and the replay/synth factories (C6 §1.8); C1 §5 PLYR redaction; `parser_exe` default finds `parser/target/release` then `debug` (proposal 014-T2); simulator follows SEMANTICS R12 (non-integer staged values).
- Session recorder for synth (proposal 015): `SynthAdapter(record_dir=...)`, `sae synth --record <dir>`; recorded runs replay to the same strategy sequence.
- Wave 3: `sae_in.ini` neutralised (knob-only) on close and on multiplayer (C2 §2.1); Ironman detected from `ironman.sav` metadata in live mode → observe-only, writes refused (C6 §1.10).

#### T3 Parser

- `sae-parse` Rust CLI implementing C4 v1: `parse`, `fixture`, `--version`; section-selective extraction; C4 §3 legal/god/self-state features; self-measured `cost`; Windows `--background` mode.
- Fixture renderer and 9 fixtures under `parser/fixtures/` (4 archetypes, observer mode, no contact, two-front war, plain text, pathological 2×).
- Hardening caps (zip size/entries, gamestate size, span size, entry count, name length) mapped to clean exit codes; fuzz, golden and memory-stress tests.
- Proposal 007: seq and `LegalView` counts above 1,000,000 now exit 5 `parse` (never clamped); fixture specs with seq above the cap exit 2.

#### T4 Engine core and daemon

- `sae.core`: fog-legal feature extraction, forgetful Bayes belief over the 4 contract archetypes (fixed-share ρ, clipped centred log-likelihoods, diagnostic-only `change_score`), counter table with 24-month commitments, C9 force/clear and a static safety floor, desired-state intent expansion, C3 §4 validator, ack lifecycle (LOST/SUPERSEDED/one re-emission/`ack_alarm`) with the seq floor rule, outcome reward, versioned `EngineState` (`state.json`).
- `sae.daemon`: `run()` / `run_engine()` main loop (serialised engine calls, Governor poll and wiring, C9 application, spm samples, RSS breach health, re-exec with a 3-per-hour guard), Governor-gated `SaveParser` for `sae-parse`, `sae daemon` CLI with `--resume`, CURRENT lock and lazy composition root.
- C6 v1.1 follow-ups: replay/synth lockstep in the daemon loop (proposal 011), adapter health hook wiring (proposal 009), multiplayer write-guard receipts handled without `ack_alarm`, fleet bias removed from expansion (C3 v1.1), int bounds (proposal 007).
- C9 v1.1 command expiry (proposal 016): stale inbox commands are expired, logged, never applied; canonical `set_layer`.
- `sae.daemon.run` owns `runs/CURRENT` in replay/synth too (so `sae ctl` reaches them); SIGTERM/console-close handler for a clean exit.
- Runs-dir size warning (`health budget_breach`, detail `runs_dir_bytes`), checked at start and monthly; never deletes.
- Proposal 017 (v1.2): adapter.close() before the sink on every exit (neutral ini), `parse(..., sections=)`, Ironman receipts untracked and audited; observe-only via `ironman_detected`.

#### T5 Governor and platform

- `sae.governor`: pure C7 Resource Governor (`make_governor`): RAM/fault/spm triggers with hysteresis, sustained-H2 → H3, 6-month de-escalation, preset floor, forced level, operator suspend with kill/knob latching, CLOSED wall-time months, date-jump discontinuity guard.
- `sae.platform`: `make_sensors` (psutil RAM, `/proc/vmstat` or PDH faults, read-only game probe, daemon RSS/CPU) and `make_platform` (daemon nice 10 / BELOW_NORMAL + EcoQoS; parse worker at SCHED_IDLE + ionice with systemd scope or RLIMIT_AS, or IDLE + Job Object with commit limit and KILL_ON_JOB_CLOSE; capped stdout).

#### T6 Telemetry and dev console

- `sae.bus`: C5 bus writer (`open_sink`): validated, unbuffered append-only `bus.jsonl` and `bus.god.jsonl` with gapless per-file seq, `run_started` rules incl. `--resume`, partial-line repair, 1 MiB cap, monthly fsync, `invalid_event` fallback and disk-full recovery; readers (`read_events`, `BusTail`, `final_acks`); offline god-file compaction.
- `sae.control`: C9 file inbox (`open_inbox`) with crash-safe at-most-once handling, and `sae ctl pause|resume|force-strategy|clear-force|status`.
- `sae telemetry`: optional third process streaming the bus to a remote Rerun viewer or a `.rrd` file; tiers T0–T2 with level throttling, diagnostic-only `change_score`, multiplayer-guard alert, RSS self-limit, Engine Console blueprint.
- `tools/console/`: offline DuckDB views (`v_decisions` and friends) and `compact_god.py`.
- Optional extras `telemetry` (`rerun-sdk==0.39.0`) and `console` (`duckdb`), proposal 010-T6.

#### T7 — QA, CI and bench

- `tests/integration/`: CI-4 checks: the import whitelist derived from `uv.lock`, a peak RSS limit of 150 MiB, and a zero-GPU manifest check. Also the CI-2 not-applicable record, a wrapper for T3's fixture validator, the workflow static checks, and a balloon planning test.
- `tests/e2e/`: a game-free end-to-end harness for `sae synth` and `sae replay`. It covers the decision-chain checker, replay determinism, PLYR redaction on the bus, CI-1 (H0 vs H2), CI-5 (worker memory cap leading to `budget_breach`), archetype acceptance, `.rrd` output, and the demo.
- `sae bench`: `parse`, `overhead` (R-1), `stutter` (R-2), `script-cost` (R-3), `bus --gate R-4|R-5|R-6`, `spy` and `demo`.
- `bench/`: R-1..R-6 procedures (these require the game), `balloon.py`, CSV templates, and `demo.sh`.
- `.github/workflows/ci.yml`: Linux Python, Linux Rust (fmt, clippy, test, Windows cross-check) and Windows jobs. Every action is pinned to a commit SHA. The workflow is validated with actionlint only; it has not run on GitHub.
- CI-1 now records each campaign with `sae synth --record` and compares `sae replay` at H0 and H2. The test-only session builder is removed. CI-4 pins the exact runtime distribution set.

#### T8 Release, docs and M0 kit

- `sae m0` spike kit: 17 checks with printed manual steps, read-only evidence analyzers (StarNet hook, `error.log`, `game.log` via the C1 parser, ACK match, timed `sae-parse` run), M0-4 timestamping tailer, per-check results, `m0_results.md` summary with the go/no-go verdict. Never launches the game or writes to its folders; files the game needs are staged for manual copy.
- `docs/m0_checklist.md` covering A-01..A-45, every M0 test and non-registry items, with a drift test against the registry and every UNVERIFIED tag (T2 and T5 platform items included); `tools/m0/README.md` weekend runbook and results template.
- User guide, developer guide and a rewritten `README.md`; docs reference test.
- `packaging/PLAN.md`, `CHANGELOG.md` and `packaging/merge_changelog.py` (fragment merge for Wave 3).
- User guide: Ironman observe-only, neutral `sae_in.ini`, recordings and sharing, disk use of `runs/`; README "Run the prototype" quickstart.

#### Validation backlog

- `docs/TEAM_PLAN.md` "Validation backlog" V-1..V-5 (Fable architecture review): real-play tests that replace
  today's circular synth evidence (likelihoods are fitted to the synth profiles), e.g. V-1 calibration on 5
  hand-labelled real campaigns, V-2 the in-game knob A/B (M0-8). None has run; all need the game or real saves.

#### Expansion X0 and coverage (2026-10-20)

- Contract C10 (profile store, campaign index, versioned policy and registry); proposals 019 Part A, 020 §1–§2,
  021, 022 and 024 accepted; C1 PLYR `name` optional for the redacted recording stub; C8 v1.3 (`engine.*`
  defaults null, filled from the policy). Details: `contracts/CHANGES.md`.
- WU-2 (T4): the daemon loads a versioned policy at start (built-in 0.1.0 equals the former code constants, sha
  `0455844e…`), puts it on `run_started`, and `--resume` exits 1 when the policy sha changed.
- WU-3 (T2): `save_folder_key` and `game_version` on observations; recording limits and PLYR redaction that still
  replays (stub `SAE|v1|PLYR|<date>|sid=<n>`); `label.json` from `sae synth --record`.
- WU-4 (T7): fog-perturbation checks F0–F4 (F3 waits for WU-5); C10 doc examples now validated; CI coverage step.
- WU-8 (T8): privacy, policy registry, rollback and recording-hygiene docs; A-46 in the M0 checklist.
- Coverage tooling: `pytest-cov` with `fail_under = 90` and parallel mode; measured 96.58% overall, every Python
  package ≥ 90%. Built-in policy JSON ships in the wheel.
- Fable architecture review 2 (`docs/status/ARCHITECTURE_alignment_review_2.md`): 5 of 5 actions closed.
- `docs/handoff/NEXT_ORCHESTRATOR.md`: handoff prompt for the next orchestrator session (state, decisions, next work).

#### Expansion and process

- T9 expansion plan (planning only, no code): `docs/expansion/player_profile_and_rsi_plan.md`, with proposals
  019–023 PROPOSED, the research re-verification and the Fable review of the proposal in
  `context/research_notes/Player profiling and RSI/`. Maintainer decisions D1–D6 recorded in the plan §8
  (no reputation mode in v1; priors once a profile is eligible; 6 labelled campaigns at M2; policies stay
  personal; profile update from a launcher; Stage 2 objective decided before M4).
- Git hooks in `.githooks/` (enable with `git config core.hooksPath .githooks`): `pre-commit` runs a secret scan,
  ruff, `mypy --strict src/sae`, the full pytest suite, and the mod or parser gates when those paths change;
  `commit-msg` requires a conventional subject and a `Reviewed-by:` trailer naming the review record.
- `CLAUDE.md`: Fable review gates — a Fable adversarial review before any plan is implemented and an
  architecture-alignment review after each wave.
- `docs/plans/MVP_2day_plan.md` (2-day prototype push; no scope cuts) and `docs/BUILD_REPORT.md`.

### Changed

#### Contracts and maintainer decisions, all teams

- Contracts C1–C9 frozen at v1, then v1.1/v1.2 without any wire change (`SAE|v1|`, `sae_p_schema = 1`, C4 `schema_version` 1). Proposals 001–018 were decided; 019–023 (T9 expansion) are PROPOSED. The full record is in `contracts/CHANGES.md`, not repeated here.
- Maintainer decisions (2026-10-19): CUSUM `change_score` is diagnostic only; `balanced` is the default preset and `low_impact` is the H1 floor; the fleet-size intent is dropped from v0 until M0-7 (C3 v1.1); the engine refuses `sae_in.ini` writes while `mp=1`; `outscale_siege` and `early_punish` share AI values and differ only in flavour text.
- Proposal 017: `sae_in.ini` is rewritten knob-only on exit, on SIGTERM and on multiplayer; Ironman is detected from save metadata and switches to observe-only (C2, C5, C6 v1.2). Amended by maintainer decision: the knob-only exit rewrite also happens under Ironman; only intent writes are blocked.
- Proposal 018: deferred batches are audited with their own `deferred` rule id, separate from real rejections (C3 v1.2).

#### CI

- `.github/workflows/ci.yml` also runs on pushes to `feat/**` and on manual dispatch; a new `mod` job runs
  `check_mod.py` and the mod tool tests; Linux clippy uses `--all-targets` to match the pre-commit hook.

### Fixed

#### Windows CI

- Product: `FileInbox` now reports a directory named `c-*.json` as `invalid` on Windows (`os.open` on a directory raises `PermissionError` there, so it was logged and skipped silently).
- Root cause of the Windows child-process failures (0xC0000142): `test_close_handle_noop_on_linux` called the real `CloseHandle(7)` on Windows and closed a live handle of the pytest process; it now pins `sys.platform` (proved on CI run 39449962091).
- `src/sae/platform/spawn.py`: `sys.platform` guards so `mypy --strict src` passes on Windows (POSIX calls were reachable only behind a runtime variable mypy cannot narrow); no behaviour change.
- CI: the CI-1/3/4/5 "collected" steps pass `-o addopts=` (the `-q` in addopts made `--co -q` print no node ids, so the count was 0).
- Tests only (portability): `/x/p` and `/x/sae-parse` path expectations built via `Path`/`json.dumps`; `\` vs `/` in the rerun-import guard (`as_posix`); POSIX-only systemd probe test marked `linux_only`; `_win32` guard test forces `sys.platform="linux"`; 6.1 KB parametrize id shortened (exceeded the 32767-char `PYTEST_CURRENT_TEST` limit); import-guard subprocess now reports rc/stderr (exit 0xC0000142 DLL_INIT_FAILED, cause unconfirmed, likely runner-side).

#### T6 Telemetry

- `sae telemetry` exceeded its 150 MiB RSS cap on the GitHub Linux runner (162-172 MiB, exit 1). CI run 39442249597 split that RSS into anon 59-60 MiB + file-backed 92-106 MiB (shared library pages). Fixes: (1) maintainer decision 2026-10-20 (proposal 026, C8 §2 v1.5): the 150 MiB cap now measures private memory (Linux `RssAnon`, Windows private bytes, RSS fallback elsewhere); the cap log line prints total RSS plus the anon/file split; (2) records go to Rerun as one `send_columns` per entity group (per-record `rr.log` grew with bus size: 3 MiB bus 162 MiB RSS); (3) 256 KiB tail polls (`BusTail(batch_bytes=)`, still reads any C5-valid 1 MiB line); (4) low-memory env defaults, `mallopt` and THP-off before `import rerun`. Local peak, 6-year synth run: private 59-60 MiB, RSS 115-121 MiB (was 128-133 MiB RSS). Regression test `tests/unit/t6/test_telemetry_rss.py` bounds peak private memory at the 150 MiB cap and prints private/RSS/split every run. CI result after this change is pending.

#### T4 Engine core and daemon

- early_aggression misclassified as turtle and 30-year belief drift (likelihoods aligned with synth profiles, memoryless `at_war` feature, `first_war_month` report-only); balanced xfails resolved; real `sae synth` acceptance test. `sae daemon` resolves `sae-parse` via T2's resolver (proposal 014).

#### Fable architecture review fixes

- Budget breaches now escalate with their own `budget_breach` trigger.
- Monthly health carries the spm and parse-cost metrics.
- A deferred batch writes an audit line to the bus.
- The telemetry process memory cap is 150 MiB (C8 §2).

### Security

#### T8 Release, docs and M0 kit

- `PLYR` field values are redacted from parse-error details and `bus.jsonl` (proposal 012). Recordings (`--record`, live record dir) still hold the raw `game.log`; the user guide says to share only `bus.jsonl` and `.rrd` files.
