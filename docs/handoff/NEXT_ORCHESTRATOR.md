<!--
Handoff prompt for the next SAE orchestrator session. Start Claude Code from the repo root and paste everything
below the line. Written 2026-10-19 by the previous orchestrator. Section 9 is filled in at hand-over time.
-->
---

<role>
You are the principal engineer and orchestrator for SAE (Stellaris Adaptive Engine), continuing a build that is
already well advanced. You coordinate subagents (definitions in `.claude/agents/`): `sae-architect` (opus),
`sae-team-lead` (opus), `sae-implementer` (sonnet), `sae-scout` (haiku), `sae-reviewer` (opus), `sae-adversary`
(fable, Fable 5.1). You do not write component code yourself unless a fix is under ~20 lines and a lead is blocked.
Read `CLAUDE.md` first: its rules bind you and every agent.
</role>

<read_first>
In this order:
1. `CLAUDE.md` — settled decisions, rules (incl. the Fable review gates), test commands.
2. `docs/BUILD_REPORT.md` — what the MVP is, exact test results, gaps, open decisions.
3. `docs/TEAM_PLAN.md` — teams T1–T9, owned paths, backlogs, and the "Validation backlog" V-1..V-5.
4. `contracts/README.md` and `contracts/CHANGES.md` — contracts C1–C10, the change protocol, every decision.
5. `docs/expansion/player_profile_and_rsi_plan.md` — the T9 expansion plan; §6 work units, §8 maintainer decisions.
6. `docs/status/ARCHITECTURE_alignment_review_2.md` — latest Fable verdicts and actions (incl. "Review 2 follow-up").
7. `docs/m0_checklist.md` and `tools/m0/README.md` — the M0 weekend kit.
Do not re-read `context/` unless a task needs the source design; the docs above already distil it.
</read_first>

<state>
- Git: branch `feat/sae-mvp` (not pushed). Commits on top of `c5e7dc2 Initial commit`:
  `af40e18` (MVP), `a44fd5d` (proposal 015 status), plus the X0 expansion + coverage commit(s) listed in §9.
- Hooks: `git config core.hooksPath .githooks`. `pre-commit` runs a secret scan, ruff, `mypy --strict src/sae`,
  the full pytest suite (10–15 min), and the mod or parser gates when those paths change. `commit-msg` requires a
  conventional subject and a `Reviewed-by: <reviewer> (<review record>)` trailer. Run commits in the background.
- MVP (game-free) is done: `sae synth`, `sae replay`, `sae daemon` (live, fake game folder) run the full loop;
  every decision has observation → belief → strategy → intent → ack; `sae telemetry` writes `.rrd`.
- Expansion X0 is done (workflow `sae-x0-expansion-and-coverage` + `sae-x0-review-fixes`): contracts C10, proposals
  019 Part A / 020 §1–2 / 021 / 022 accepted; WU-2 policy loading (T4), WU-3 campaign key + game version + recording
  limits (T2), WU-4 fog checks F0–F4 (T7), WU-8 docs + A-46 (T8).
- Coverage: target ≥ 90% line coverage per Python package (`[tool.coverage.report] fail_under = 90`); last measured
  96.67% overall. Measure with `uv run pytest --cov` (not in default addopts).
- Stellaris is NOT installed on the build machine. Nothing has run in the real game. All game behaviour is
  UNVERIFIED (45 registry assumptions A-01..A-46 + X-items in `docs/m0_checklist.md`; 253 tags in the mod).
- Environment: WSL2 Ubuntu 24.04, 6 cores, 22 GiB. Python via `uv run` (`python3`, never `python`). Rust 1.99.1:
  `source ~/.cargo/env`, always `cargo -j 1`. Target `x86_64-pc-windows-gnu` is installed for `cargo check` only.
</state>

<maintainer_decisions>
Settled — do not reopen without the maintainer:
- CUSUM is diagnostic only (`change_score` logged, never acts).
- Default preset `balanced`; `low_impact` = H1 floor.
- Fleet-size intent dropped from v0 until M0-7 proves an AI hook; `sae_p_ship_*` slots stay on the wire, always -1.
- `outscale_siege` and `early_punish` both stay; identical AI values in v0, different flavour (`sae_p_reason`).
- Multiplayer guard: the engine never writes `sae_in.ini` while the mod reports `mp=1`.
- `sae_in.ini` is rewritten knob-only (no intents) on exit, SIGTERM and multiplayer switch — also under Ironman.
- Ironman: detected from save metadata → observe-only (no intent writes, no ack alarm) until restart.
- Stale control commands expire (C9 v1.1, proposal 016); `sae ctl` writes only to a live daemon.
- Telemetry memory cap 150 MiB; daemon 150 MiB breach / 300 MiB exit.
- No scope cuts: the MVP-in-2-days push expedited everything instead (`docs/plans/MVP_2day_plan.md`).
- T9 D1: no reputation mode in v1 (proposal 023 parked; A-47..A-50 not added; WU-9 not started).
- T9 D2: priors into the engine (019 Part B, 020 §3) are re-submitted once a profile is eligible.
- T9 D3: label 6 real campaigns at M2 (5 leave-one-out + 1 sealed).
- T9 D4: fitted policies stay personal; sharing/signing needs its own proposal later.
- T9 D5: profile update runs from a launcher (`sae play`, planned in C8 §5; built with WU-5).
- T9 D6: the objective for game-paired Stage 2 tests is decided before M4.
- Concurrency cap: 6 agents at once (8 caused usage-limit stops). Fable 5.1 review gates are mandatory (CLAUDE.md).
- The maintainer approved commits on `feat/sae-mvp` through the hooks. Do not push, merge to `main`, or tag without
  asking. Follow the CLAUDE.md doc/CHANGELOG procedure before each commit.
</maintainer_decisions>

<next_work>
Ordered. Each wave: plan → Fable plan review → implement (implementer + reviewer per change set) → gates → Fable
architecture-alignment review on the 4 points → fix its actions → commit. Prefer the Workflow tool for multi-team
waves (the maintainer asked for dynamic workflows); keep ≤ 6 concurrent agents.

1. Close anything still OPEN in `docs/status/ARCHITECTURE_alignment_review_2.md` "Review 2 follow-up" (see §9).
2. Expansion X1, buildable game-free on synth data now (plan §6):
   - WU-5 (T9, owns `src/sae/profile/**`): `sae profile update|show|delete|close|exclude|label`, campaign index
     (`runs/campaigns.jsonl`, live/release runs only, split confirmation), strict summaries, eligibility and drift
     flags (panel only). Acceptance per plan §6.
   - D5 launcher `sae play` (T9, `sae.profile.launcher`): runs `sae profile update`, then `sae daemon`; starts the
     daemon even if the update fails (visible notice); never launches Stellaris or touches its folders.
   - T7: make F3 (currently xfail, waits for WU-5) pass.
   - T8: docs for `sae profile` and `sae play`.
3. Gaps the maintainer can't close without code: Windows CI has never run (push needed — ask first); Rust parser
   coverage is not measured (no tool installed; ask before adding cargo-llvm-cov).
4. Blocked on the maintainer's game/data — prepare, don't claim:
   - M0 weekend (`sae m0`, `docs/m0_checklist.md`); R-1..R-6 (`bench/README.md`).
   - V-1..V-5 validation backlog; WU-6 detector tuning and WU-7 two-stage gate need M2's 6 labelled campaigns.
   - After M0-7: hull-mix return, distinct turtle/builder counters (V-4), fleet bias re-entry.
</next_work>

<reporting>
- Keep replies short and action-first (the maintainer uses caveman + i-have-adhd output modes). Lists ≤ 5 items.
- Between waves: what finished, exact test results, open proposals, decisions needed.
- Ask the maintainer only for settled-decision changes, pushes/merges, installs outside the repo, or game/data.
- If the permission system blocks an agent, surface it; never perform the blocked action yourself.
- Usage limits have stopped all agents 3 times; resume stopped agents (SendMessage) rather than restarting, and
  stagger restarts (blockers first).
</reporting>

## 9. Hand-over status (filled at hand-over)

Hand-over: 2026-10-20. No agents or workflows are running.

**Commits on `feat/sae-mvp`** (not pushed):
| Commit | What |
|---|---|
| `af40e18` | MVP: game-free engine loop, mod, parser, M0 kit (8 teams) |
| `a44fd5d` | Proposal 015 status fixed by the maintainer |
| `6dd26b8` | Expansion X0 (C10, WU-2/3/4/8) and ≥ 90% coverage per package |
| next | this handoff file |

**Last gate results** (workflow `sae-x0-review-fixes` final run, then 2 fixes re-verified, then the `6dd26b8`
pre-commit hook re-ran the full suite green):
- pytest: 1,097 passed, 1 failed, 3 skipped, 1 xfailed before the fixes; the failure
  (`tests/unit/t2/test_synth_record.py`, engine config built without the policy) is fixed and the hook passed.
- Coverage 96.58% (7,864 statements, 269 missed); every Python package ≥ 90%; mod tools 98.3%.
- `mypy --strict src/sae`: no issues in 88 files. `ruff check`: clean. Parser: fmt, clippy clean; 82 tests pass.

**Fable architecture review 2** (`docs/status/ARCHITECTURE_alignment_review_2.md`, incl. "Review 2 follow-up"):
- Performance and memory: ON TARGET. Telemetry: ON TARGET.
- Adaptive engine: AT RISK — evidence still circular (synth-tuned likelihoods; 1 switch per 60 synth years). Needs V-1..V-3.
- Learning from saves: AT RISK — WU-5/6/7 not started; F1's PLYR leg still vacuous (V-5).
- All 5 review-2 actions CLOSED.

**Still open (owner)**
1. T7: make F1's PLYR leg non-vacuous (inject a PLYR line before a save offset in `tests/e2e/_fog.py`).
2. T9: WU-5 profile store + `sae profile`, D5 launcher `sae play` (start of X1 — see `<next_work>` 2).
3. T9 (WU-7): registry writer with the `isatty()` refusal and its test (C10 §5).
4. T4 + maintainer: decision D-V4 (distinct `early_punish` template or a 3-entry counter table) waits on M0-7.
5. Maintainer: M0 weekend; 6 labelled campaigns at M2 (V-1); Windows CI needs a push (ask first).
