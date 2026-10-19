# SAE team plan (Wave 1 → Wave 2)

Owner: architect. Every team codes against the FROZEN contracts in `contracts/` and the models in
`src/sae/contracts/`. Contract changes go through `contracts/README.md` §3. Each team writes
`docs/plans/<TEAM>_plan.md`, `docs/status/<TEAM>.md`, `docs/components/<TEAM>.md` and
`changelog.d/<TEAM>.md`. Unit tests live in `tests/unit/<team>/` (e.g. `tests/unit/t4/`).

Quality bar for every team: `uv run pytest -x -q`, `uv run mypy --strict <your package>`,
`uv run ruff check`, in `parser/`: `cargo clippy -j 1 -- -D warnings`, `cargo test -j 1`. Docstrings on
public APIs, no TODO stubs in MVP scope, deterministic seeded tests, property tests where input is
parsed. Windows-only code behind platform checks, mocked in tests, labelled "not executed in CI on
this machine". Every game assumption keeps its A-xx id (contracts/README.md §5).

## Current state (2026-10-19)

| Team | State | Notes |
|---|---|---|
| T1 | DELIVERED (static, M0-ready) | `sae_bridge` 0.1.0, not run in the game |
| T2 | DELIVERED | C6 v1.1; proposals 009/011/012 accepted (health hook, lockstep, PLYR redaction) |
| T3 | DELIVERED (MVP) | C4 v1 |
| T4 | DELIVERED | owes the 009 wiring and the 011 lockstep rule (C6 §1.7) |
| T5 | DELIVERED (MVP) | C7 v1.1, factories in C8 §5.1 |
| T6, T7, T8 | in progress | |

Settled since Wave 0 (maintainer, 2026-10-19; `contracts/CHANGES.md`):

1. CUSUM is diagnostic only (`belief_update.change_score`, logged, never acted on). Settled for v0.
2. Fleet-composition bias is dropped from v0 (C3 v1.1, `RESERVED_KINDS`); backlog, re-entry after
   M0-7 proves a per-size knob (A-22) via a C3 v1.2 proposal. The C2 ship slots stay, always -1.
3. Multiplayer write guard: owned by T2, once, in the adapters' shared write path (C6 §1.6).
4. Presets confirmed: `balanced` default, `low_impact` = H1 floor + `emit_period 3` (C8 §4).
5. Counters: `outscale_siege` and `early_punish` both stay in v0, write identical game values and differ
   only in `sae_p_reason`; they split again with the hull mix after M0-7 (C3 §6).
6. Composition-root factory names and signatures are contract (C8 §5.1): `sae.bus.open_sink`,
   `sae.control.open_inbox` (T6); `sae.governor.make_governor`, `sae.platform.make_sensors`,
   `sae.platform.make_platform` (T5); `sae.adapters.make_adapter(..., *, health=None)` (T2). Only
   `sae.daemon.wiring` (T4) imports them.
7. Optional extras (proposal 010): `telemetry` (`rerun-sdk==0.39.0`, `sae.telemetry` only) and `console`
   (`duckdb`, `tools/console/` only). Runtime deps stay `pydantic`, `psutil`; neither extra may enter the
   daemon's import graph (CI-4, `tests/unit/t4/test_boundary.py`). Zero GPU on the player's machine
   through M4.
8. Ownership additions: `src/sae/bench/**` → T7, `src/sae/m0/**` → T8 (C8 §7).

## Dependency order

```text
contracts (frozen) ──► T3 parser ─────────────┐
                  ├──► T1 mod (C1/C2 text)     │
                  ├──► T2 adapters + simulator ◄┘ (needs `sae-parse fixture`/`parse` for synth/replay)
                  ├──► T5 governor/platform
                  ├──► T6 bus writer/readers, ctl
                  ├──► T4 core + daemon (uses T2, T5, T6 through contracts; integrates last)
                  ├──► T7 CI/bench (starts with CI-3, CI-4 on contracts + stubs it owns)
                  └──► T8 docs + M0 kit (starts immediately from contracts/README.md §5)
```

All teams can start at once: each depends on contract models, not on another team's code. T4's
daemon loop and T7's e2e harness are the last to integrate (Wave 2).

## T1 — Mod (`mod/sae_bridge/**`)

- Deliverables: `descriptor.mod` (`supported_version="v4.4.*"`), `common/on_actions/sae_on_actions.txt`,
  `common/scripted_effects/`, `common/script_values/`, `common/ai_budget/sae_budgets.txt` (additive,
  flag-gated), `events/sae_bridge_events.txt` (`sae.1` emit, `sae.2` apply, `sae.10` flavour),
  `localisation/english/sae_l_english.yml` (all `sae_reason_*` keys); `sid` and `m` counters; HELLO/HB/SNAP/PLYR/ACK
  per C1; C2 §5 apply semantics; `sae_emit_period` stagger and `sae_quiet` (HB/HELLO/ACK exempt);
  `sae_managed` on AIs in contact with the player (A-32); `docs`-ready semantics note for the T2 simulator.
- Acceptance: a static check script (Python, under `mod/sae_bridge/tools/` or T7's tests) that every
  emitted `log = "…"` template, rendered with sample values, parses with `sae.contracts.c1_log.parse_log_line`;
  every `sae_p_*` variable read by `sae.2` matches C2's list; every construct carries its A-xx id in a
  comment. No game run is claimed.
- Depends on: C1, C2, C3 codes, C7 §6. Feeds: T2 simulator, T8 M0 kit.
- Backlog: immediate apply via `country_event` from the C2 line (ack in seconds, not a month);
  between-session `scripted_variables/sae_generated.txt` for release mode; StarNet profile after M0-1.

## T2 — Adapters and bridge I/O (`src/sae/adapters/**`)

- Deliverables: log tailer (offsets, truncation, partial lines, CRLF, 2 s poll); autosave watcher
  (stable 2 s and ≥ 8 s); atomic `sae_in.ini` writer with Windows retry; ACK matcher to `IntentAck`;
  LiveAdapter, ReplayAdapter (speed, from/stop-at, ack models echo/drop/delay/from_recording,
  `game.log.ts` sidecar), SynthAdapter (archetype schedule → `FixtureSpec` → `sae-parse fixture`),
  release mode; Python mod simulator implementing C2 §5 and emitting C1 lines; path auto-detection
  (C8 §3); `sae replay` / `sae synth` CLIs; recorder for fixtures; multiplayer write guard in the
  shared write path (C6 §1.6); PLYR redaction in health details (C1 §5).
- Acceptance: property tests on the tailer (arbitrary chunking reproduces the same records);
  simulator round-trip: `compile_batch → render_ini → simulator → ACK lines → IntentAck` for every C2
  §5 branch; fog test: no `Observation` built from a PLYR line or a `god` section; replay determinism
  (same bus hash twice at `speed=max`).
- Depends on: C1, C2, C3, C4 (T3 binary for synth/replay with real parsing; a recorded-JSON
  `SaveParser` stub of its own until then), C6, C8. Feeds: T4, T7.
- Backlog: keystroke injection (opt-in, after M0-10); Proton path detection; StarNet profile; fleet
  composition bias (after M0-7, A-22).

## T3 — Parser (`parser/**`)

- Deliverables: `sae-parse parse|fixture|--version` per C4 using jomini (zip + text; binary only if
  M0-11 needs it); section-selective extraction; legal-view and god-view computation per C4 §3;
  `cost` self-measurement; `--background` (Windows `PROCESS_MODE_BACKGROUND_BEGIN` + EcoQoS behind
  `cfg(windows)`); fixture renderer; `parser/README.md` key map with A-19 labels; fixtures
  `parser/fixtures/<name>/{spec.json,save.sav,expected.json}` (≥ 1 per archetype + 1 pathological at 2×).
- Acceptance: `cargo clippy -j 1 -- -D warnings`, `cargo test -j 1`; every fixture's output validates
  as `ParseOutput` (a Python test in T7 or a JSON-schema check against `contracts/schemas/ParseOutput.schema.json`);
  fuzzing of the text parser entry (cargo-fuzz or proptest) without panics; exit-code table of C4 §2
  covered by tests; `cargo check -j 1 --target x86_64-pc-windows-gnu` passes.
- Depends on: C4 only. Feeds: T2, T4/T5, T7.
- Backlog: rayon pool with explicit `num_threads` (M2–M3); binary save support; the dashboard parser
  as a cross-check.

## T4 — Engine core and daemon (`src/sae/core/**`, `src/sae/daemon/**`)

- Deliverables: feature extraction from `Observation` (shared player belief from in-contact
  `LegalView`s; 4–5 features: border fort ratio, expansion rate, fleet-power category, months to first
  war, tech category); forgetful Bayes filter (fixed-share ρ, clipped log-lik, unobserved features drop
  out); counter table (C3 §6, no fleet bias in v0) with 24-month commitment and safety floor; strategy expansion to intents
  under the desired-state rule; validator (C3 §4); ack lifecycle (LOST after 2 months, one
  re-emission, SUPERSEDED, alarm); seq continuity across restarts and save reloads; C5 decision events
  with `decision_id`; C9 command application at tick boundaries; `sae.daemon.run(config, adapter,
  run_id)`; `sae daemon` CLI; `state.json` for `--resume`; Governor and worker wiring through the
  C7 protocols.
- CUSUM (settled): only the diagnostic `belief_update.change_score`; never triggers a switch or breaks
  a commitment.
- Replay/synth: handle each observation (including its awaited `emit`) before pulling the next
  (C6 §1.7, proposal 011); pass `health=` to `make_adapter` (C8 §5.1, proposal 009).
- Acceptance: unit tests (posterior sums to 1, monotone in evidence, clipping bounds a single feature,
  `None` features have no effect); table/commitment tests (no switch inside 24 months except forced
  or safety floor); validator rule tests, one per `RejectRule`; synth runs per archetype reach the
  right counter strategy by game year 30 in ≥ 3 of 4 seeds; import test that `sae.core` never imports
  `sae.adapters`.
- Depends on: C3, C5, C6, C7, C8, C9. Feeds: Wave 2 integration.
- Backlog: bandit across campaigns (condition C), CUSUM-triggered commitment breaks, BetweenSessionEmitter
  for release mode, LLM condition D (M4, never in the runtime loop before then).

## T5 — Governor and platform (`src/sae/governor/**`, `src/sae/platform/**`)

- Deliverables: pure `Governor` (C7 §2–§4, §8); `Sensors` (psutil; PDH via ctypes on Windows;
  `/proc/vmstat` on Linux); `Platform`: daemon priority (BELOW_NORMAL + EcoQoS / nice 10), worker spawn
  (IDLE + Job with `PROCESS_MEMORY` and `KILL_ON_JOB_CLOSE` / `SCHED_IDLE` + `ionice` + `systemd-run`
  scope or `RLIMIT_AS`), timeout, outcome classification per C4 §2; daemon RSS self-limit with
  in-place re-exec.
- Acceptance: CI-3 (synthetic traces: drifting spm never above H1; 40 % step resets baseline; RAM
  ramp reaches H2 within 10 s simulated; fault spike; no oscillation within 6 months); CI-5 on Linux
  (pathological fixture under a cgroup or RLIMIT_AS: killed, `budget_breach`, daemon keeps running);
  Windows paths mocked; the game's priority/affinity is never touched (test that no call targets a
  foreign pid except read-only probes).
- Depends on: C4 exit codes, C5 health, C7. Feeds: T4, T7.
- Backlog: PAUSED state after M1-P; opportunistic pause-time queue (M2, CI-2); suspend-on-unpause.

## T6 — Telemetry and dev console (`src/sae/bus/**`, `src/sae/telemetry/**`, `src/sae/control/**`, `tools/console/**`)

- Deliverables: bus writer (validate, flush per event, gapless seq, partial-line repair, god file);
  bus reader (skip/count invalid lines); `sae-telemetry` (`sae telemetry`) tailing the bus to Rerun
  (remote `rerun+http://…` or `.rrd`), tiers T0–T2, pinned Rerun version (extras `telemetry` and
  `console`, proposal 010), viewer memory limits; Rerun blueprint for the Engine Console wireframe; DuckDB SQL views in
  `tools/console/` (offline only, `v_decisions` joining on `decision_id`); `sae ctl` and the daemon-side
  inbox reader (C9).
- Acceptance: writer/reader round-trip property tests; crash-truncation test; `sae ctl` against a fake
  daemon inbox (accept, reject, timeout); `.rrd` produced from a fixture bus; telemetry process never
  imported by the daemon (whitelist test with T7).
- Depends on: C5, C8, C9. Feeds: T4, T7, Wave 2 `.rrd` output.
- Backlog: FastAPI/SPA console only when Rerun blocks; Parquet compaction only if bus size demands.

## T7 — QA, CI and bench (`tests/integration/**`, `tests/e2e/**`, `.github/workflows/**`, `bench/**`, `src/sae/bench/**`)

- Deliverables: CI-1 (forced H0 vs H2 replay: switches in the same window ≥ 90 %), CI-2 (only if the
  opportunistic queue exists; else documented as not applicable in MVP), CI-3 harness with T5,
  CI-4 (import graph of the daemon: whitelist only; RSS ≤ 150 MB; zero GPU packages in the lock file),
  CI-5 with T5; fog import test (C6 §4); end-to-end game-free harness (`sae synth` and `sae replay`
  produce a bus where every decision has observation → belief → strategy → intent → ack, plus `.rrd`);
  GitHub Actions (Linux; Windows leg as `cargo check` + unit tests); `sae bench` and R-1..R-6 scripts
  marked "requires the game".
- Acceptance: all of the above green on this machine; R-gates documented, not claimed.
- Depends on: every contract; integrates every team. Feeds: Wave 2 and 3.
- Backlog: M4 paired A/B harness on a second machine; Windows CI leg for Python.

## T8 — Release, docs, M0 kit (`docs/**` except this file, `README.md`, `CHANGELOG.md`, `changelog.d/**`, `packaging/**`, `tools/m0/**`, `src/sae/m0/**`)

- Deliverables: user and developer docs (honest pitch: "a strategic director that reshapes the AI's
  economy and posture", the influence ceiling; fleet doctrine only after M0-7); the M0 spike kit — `tools/m0/` scripts
  plus `docs/m0_checklist.md` with one row per A-xx in contracts/README.md §5 (A-01..A-45) and per M0 test (pass/fail criterion,
  fallback, result field), a timestamping log tailer for M0-4, a parse timer for M0-11, an
  `m0_results.md` template; `sae m0` CLI; packaging plan (mod zip, Python wheel, `sae-parse` binary);
  changelog stewardship (merge `changelog.d/` fragments at Wave 3).
- Acceptance: every A-xx id in `contracts/README.md` §5 appears in the checklist with a test and a
  fallback; `sae m0 --help` works; docs reference only contracted commands and paths.
- Depends on: C1–C9 (read-only). Feeds: maintainer's M0 weekend.
- Backlog: mod page text after M3; Workshop packaging.

## Validation backlog (Fable architecture review, 2026-10-19)

Source: `docs/status/ARCHITECTURE_alignment_review.md` (benefit 3 "Adaptive engine" AT RISK; benefit 4
"RSI readiness" AT RISK). Today's evidence that the engine adapts is circular: the likelihoods are fitted
to the synth generator's own profiles, so synth runs only show that the engine agrees with our simulator.
These tests replace that with evidence from real play. None can run on this machine (Stellaris not
installed); each needs the maintainer's saves or the game. Added on the maintainer's request.

| ID | Test | Pass criterion | Needs | Owner | Milestone |
|---|---|---|---|---|---|
| V-1 | Calibrate on real campaigns: hand-label 5 real campaigns with the player's archetype; fit likelihoods with leave-one-campaign-out | Expected calibration error (ECE) ≤ 0.10 on each held-out campaign; correct counter chosen on ≥ 4 of 5 | 5 labelled real campaigns (saves + logs), label manifest (seam S4) | T4 + T9 | M2 |
| V-2 | Knob A/B in game: does an applied econ plan change AI behaviour? | Fleet power B/A ≥ 1.3 after 15 years (already M0-8 in `docs/m0_checklist.md`) | the game, M0 weekend | maintainer, T8 kit | M0 |
| V-3 | Adaptation has signal, not noise: long runs must show real strategy switches and a reward spread | On recorded real or replayed campaigns: > 1 switch where the player changes style; rewards not all within ±0.02 (a 200-year synth run gave 1 switch, 3 batches, 99 rewards within ±0.02) | V-1 data or real recordings | T4 + T7 | M2 |
| V-4 | Turtle vs builder counters really differ | `outscale_siege` and `early_punish` produce measurably different AI behaviour once the hull mix returns | M0-7 pass (A-22) | T4 + T1 | after M0-7 |
| V-5 | Fog-perturbation test on real saves: hide one god-only field and confirm beliefs don't move | Belief change = 0 for every god-only perturbation | `ReplayAdapter(parser=...)` seam, real recordings | T9 + T7 | M2 |

The runtime still ships only validated, versioned policies approved by a human; no test here turns on
runtime self-modification.

## Wave 2 entry criteria (architect)

All teams' status files report green checks; `sae synth` runs the loop end to end with the Python mod
simulator acking; the Governor manages the parse worker; telemetry writes a `.rrd`.
