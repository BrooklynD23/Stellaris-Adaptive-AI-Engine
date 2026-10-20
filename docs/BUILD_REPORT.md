# SAE build report — MVP (game-free)

Date: 2026-10-19. Machine: WSL2 Ubuntu 24.04.3, 6 cores, 22 GiB, Python 3.12.3, uv 0.13.1, Rust 1.99.1.
Stellaris is **not installed** on this machine: nothing here has run against the real game.

## 1. Verdict

The MVP exit criteria hold **game-free, on this machine**. The engine loop runs end to end from `sae synth`,
`sae replay` and `sae daemon` (live mode against a fake game folder). Every decision in `bus.jsonl` carries
observation → belief → strategy → intent → ack, and `sae telemetry` writes a `.rrd` recording. Running in a
real game is not proven: it waits for M0 on the maintainer's PC.

## 2. What was built

| Team | Delivered | Paths |
|---|---|---|
| Wave 0 (architect) | Contracts C1–C9 (now v1.1/v1.2, no wire change), pydantic models, 17 JSON Schemas, repo skeleton | `contracts/`, `src/sae/contracts/` |
| T1 Mod | `sae_bridge` 0.1.0: monthly driver, staggered SNAP/PLYR, HB, intent apply with ACK/NACK, knobs; `SEMANTICS.md`; static checker | `mod/sae_bridge/` |
| T2 Adapters | Live (log tail, autosave watcher, atomic `sae_in.ini`, ack matcher, multiplayer guard, Ironman observe-only, neutral file on close), Replay, Synth (with `--record`), Python mod simulator | `src/sae/adapters/` |
| T3 Parser | `sae-parse` (Rust, jomini): section-selective features, cost fields, exit codes, fixtures, fuzz and stress tests | `parser/` |
| T4 Core + daemon | Fog-legal features, forgetful Bayes belief, counter table with 24-month commitment and safety floor, intent validator, audit, daemon loop, run lock, SIGTERM handling | `src/sae/core/`, `src/sae/daemon/` |
| T5 Governor + platform | Game-state × health-level Governor, injectable sensors, worker spawn with memory cap, own-priority lowering only | `src/sae/governor/`, `src/sae/platform/` |
| T6 Telemetry + console | bus writer and compaction, `sae ctl` (file inbox with command expiry), `sae telemetry` (remote Rerun or `.rrd`, 150 MiB cap), DuckDB views | `src/sae/bus/`, `src/sae/telemetry/`, `src/sae/control/`, `tools/console/` |
| T7 QA, CI, bench | CI-1..CI-5, e2e harness, GitHub Actions workflow, `sae bench`, R-1..R-6 procedures, demo script | `tests/integration/`, `tests/e2e/`, `.github/workflows/`, `bench/`, `src/sae/bench/` |
| T8 Docs + M0 kit | User and developer guides, README quickstart, `sae m0` kit and M0 checklist, packaging plan, merged `CHANGELOG.md` | `docs/`, `tools/m0/`, `src/sae/m0/`, `packaging/` |
| T9 Expansion (plan only) | Player profile and bounded RSI plan, proposals 019–023 | `docs/expansion/` |

## 3. Final test results (run by the orchestrator, 2026-10-19)

| Gate | Result |
|---|---|
| `uv run pytest -q` | **902 passed, 3 skipped** in 12 min 38 s. Skipped: `tests/integration/test_ci2.py:8` (opportunistic queue cut, C7 D-13); `tests/unit/t4/test_real_synth.py:52` ×2 (30-year matrix, runs with `T7_E2E_FULL=1`: 3 of 3 passed in 3 min 36 s, run by T4) |
| `uv run mypy --strict src/sae` | no issues in 83 source files |
| `uv run ruff check` | all checks passed |
| `mod/sae_bridge/tools/check_mod.py` | 9 files, 12 templates, 253 tags, 0 failures; mod tests 45 passed |
| `cargo fmt --check`, `cargo clippy -j 1 --all-targets -- -D warnings`, `cargo test -j 1` (parser/) | clean, clean, 82 passed |
| `cargo check --target x86_64-pc-windows-gnu` | passes (checked at Wave 0 and by T3) |

Release checks (T7): CI-1 1.000 over 20 strategy switches on recorded-then-replayed synth games, H0 vs H2; CI-2
not applicable (feature cut); CI-3 named step on T5's traces; CI-4 pass (daemon peak RSS 41–46 MiB against
150 MiB, runtime closure pinned to 6 distributions, zero GPU packages); CI-5 pass on Linux (capped parser
reports `budget_breach`, loop continues).

Strategy check (orchestrator, 30-year `sae synth`, seeds 1/3/5/7): turtle → `outscale_siege`, tech_rush →
`early_punish`, early_aggression → `hold_counterpunch`, balanced → `baseline`; turtle-then-aggression switches to
`hold_counterpunch` at 2214. 20 of 20 correct.

## 4. Reviews

- Wave 3 adversarial review (Fable 5.1): 0 blockers, 6 findings, all fixed (`docs/status/WAVE3_review.md`).
- Architecture alignment review (Fable 5.1, `docs/status/ARCHITECTURE_alignment_review.md`):
  1. Performance and memory: **ON TARGET** (200 synth years: 55 MiB peak RSS; bus about 4.5 KB per game month).
  2. Telemetry: **AT RISK** at review time; both gaps found (spm/parse-cost metrics, deferred-batch audit) are now fixed.
  3. Adaptive engine: **AT RISK**. The mechanics are correct, but the evidence is circular: likelihoods are fitted
     to the synth generator's profiles. Validation backlog V-1..V-5 in `docs/TEAM_PLAN.md` replaces it with real-play evidence.
  4. RSI readiness: **AT RISK**. Five missing seams (S1–S5) are planned in the T9 expansion plan.

## 5. Known gaps

1. Nothing has run in the real game; all Stellaris behaviour is UNVERIFIED (section 6).
2. Windows-only code (Job Objects, EcoQoS, priority classes, Windows file semantics) is tested with mocks only;
   the GitHub Actions workflow passes `actionlint` locally but has never run.
3. Likelihoods are synth-tuned guesses (HYP) until M2 calibration on real saves (V-1).
4. `outscale_siege` and `early_punish` write identical AI values in v0 (fleet bias dropped until M0-7).
5. The disk-size warning reuses the `budget_breach` health kind (C5 has no disk kind).
6. Adapter edge cases: a relaunched log repeating the old file's head and tail looks like an append (A-15); a save
   rewritten with the same size and mtime is missed on coarse-mtime filesystems.
7. A remote Rerun viewer that connects and then stalls is untested.

## 6. UNVERIFIED assumptions awaiting M0

`docs/m0_checklist.md`: 45 registry assumptions (A-01..A-45), 11 extra items (X-04..X-14; X-12..X-14 are bench
R-gate items), 17 M0 tests (9 core, 4 stretch, 4 later). The mod carries 253 UNVERIFIED tags, each with its id.
R-1..R-6 require the game and have not run.

## 7. Maintainer decisions

Settled during the build (recorded in `contracts/CHANGES.md` and the plan files): CUSUM diagnostic only;
`balanced` default preset with `low_impact` = H1 floor; fleet bias dropped until M0-7; multiplayer guard;
`outscale_siege`/`early_punish` flavour-only split in v0; no scope cuts for the 2-day push; neutral `sae_in.ini`
on close (also under Ironman); Ironman detected and observe-only; T9 decisions D1–D6 (plan §8).

Open:
1. Proposals 019–022 (T9) are PROPOSED; 023 is parked by decision D1.
2. D6: the objective for game-paired tests is decided before M4.

(Proposal 015's status line was fixed by the maintainer on 2026-10-19: ACCEPTED-WITH-CHANGES.)

## 7a. Update 2026-10-20: expansion X0 and coverage

Expansion X0 (contracts C10; WU-2 policy loading, WU-3 campaign key / game version / recording limits, WU-4 fog
checks, WU-8 docs) is done. Coverage: 96.58% overall, every Python package ≥ 90% (`fail_under = 90`). Fable
architecture review 2 (`docs/status/ARCHITECTURE_alignment_review_2.md`): performance/memory ON TARGET,
telemetry ON TARGET, adaptive engine AT RISK and learning from saves AT RISK (both need real-game data or WU-5..7);
all 5 review actions closed. Exact gate results: `docs/handoff/NEXT_ORCHESTRATOR.md` §9.

## 8. Recommended next step

Run the M0 weekend on the maintainer's PC with `sae m0` (`tools/m0/README.md`, `docs/m0_checklist.md`), then
label 6 real campaigns for V-1 at M2.
