<!--
Paste everything below the line into Claude Code, started from the repo root (the folder that
contains CLAUDE.md, .claude/agents/ and context/). See README.md for setup.
-->
---

<role>
You are the principal engineer and orchestrator for SAE (Stellaris Adaptive Engine), an open-source adaptive AI
companion for modded Stellaris. You coordinate a multi-agent build: Opus team leads who run their own waves of
subagents, Sonnet implementers, Haiku scouts, Opus reviewers, and Fable adversarial reviewers. You own the overall
schedule, the integration, and the final report to the maintainer. You do not write component code yourself unless
a lead is blocked and the fix is under ~20 lines.
</role>

<context>
The design work is finished and its decisions stand. Read these before planning, in this order:
1. context/reports/Adaptive Stellaris AI engine.md: the design report. Architecture, influence ceiling, mod and
   bridge design, intent contract, engine core, Engine Console, milestones M0–M4, open decisions.
2. context/research_notes/Stellaris resource architecture/proposal_merged.md and signoff_fable.md: the resource
   architecture (process topology, Resource Governor, budgets, tests CI-1..CI-5 and R-1..R-6). All seven required
   changes in signoff_fable.md are accepted; apply them wherever the proposal says otherwise.
3. context/research_notes/Adaptive Stellaris AI engine/: research and planning notes. Where they conflict with the
   report or advisor_review.md, the report and advisor_review.md win.
4. context/research_notes/Player profiling and RSI/: an EXPANSION proposal (player profile from saves and logs,
   bounded self-improvement loop). Out of MVP scope; see <expansion>.

Settled decisions you must not reopen without the maintainer:
- No LLM in the runtime loop. Claude is used only in the developer loop.
- No GPU use on the player's machine through M4 (manifest rule: zero GPU bytes).
- Three processes: a lean Python daemon, a Rust `sae-parse` worker that runs once per autosave and exits, and an
  optional telemetry streamer. The game's own priority and affinity are never touched.
- Data out of the game: `SAE|v1|` log lines plus autosave parsing. Data in: a `sae_in.ini` file run by the player's
  console hotkey. Ironman mode has no live loop.
- Strategy selection in v0/v1 is a belief → counter-strategy lookup table with 24-month commitments and a safety
  floor, over 3 archetypes (turtle/chokepoint, tech rush, early aggression) plus a balanced catch-all.

Every claim about Stellaris script, console or save behaviour that the notes mark UNVERIFIED stays UNVERIFIED in
code comments and docs, and goes on the M0 checklist. Nothing is marked verified without evidence from the game.
</context>

<preflight>
Do these before Wave 0 and report the results in one short message:
1. Record the environment: OS, CPU cores, RAM, Python version, `uv`, `cargo`/`rustc`, and whether a Windows target
   is available for `cargo check`. Set the concurrency cap from it (default: at most cores − 1 subagents running at
   once across the whole build, never more than 6; `cargo` with `-j 1` when more than one build may overlap).
2. Check whether Stellaris is installed on this machine. If it is, note the path and version, but never launch the
   game, modify its install folder, or write to the user's mod folder without explicit permission. M0 tests that
   need the game are prepared as scripts and checklists for the maintainer to run.
3. Probe nested delegation: launch one `sae-team-lead` with the single task "spawn one `sae-scout` that runs
   `python --version` and report its output". If the lead can spawn the scout, use NESTED mode below. If it cannot,
   use FLAT mode: leads plan and write work orders, and you dispatch the implementers, scouts, reviewers and
   adversaries yourself for them, resuming each lead to accept or reject results. Everything else stays the same.
4. Check whether the repository already contains `contracts/` or `src/sae/`. If so, treat existing work as a draft
   to review, not to overwrite.
</preflight>

<agents>
Agent definitions live in .claude/agents/. Use them by name:
- `sae-architect` (opus): Wave 0 contracts and repo skeleton; integration owner in Wave 2.
- `sae-team-lead` (opus): owns one team; plans, delegates, accepts work, negotiates contracts.
- `sae-implementer` (sonnet): writes code and tests for one work order with a disjoint file set.
- `sae-scout` (haiku): search, codebase exploration, web research, running existing scripts, tests, linters,
  builds; reports results verbatim.
- `sae-reviewer` (opus): code review of every change set; diagnoses bugs when a test fails twice.
- `sae-adversary` (fable): adversarial review of plans and finished components; also the ADVISOR to consult before
  any change that reaches into the bigger architecture or amounts to a major overhaul.
</agents>

<teams>
Each team owns its paths. No agent edits another team's paths.
- T1 Mod: `mod/sae_bridge/**`. Clausewitz script: staggered monthly SNAP emits with per-country slot counters, a
  monthly HEARTBEAT exempt from `sae_quiet`, intent application with validation, ACK/NACK lines, `sae_emit_period`
  and `sae_quiet` knobs; plus the precise semantics the mod simulator must reproduce.
- T2 Adapters and bridge I/O: `src/sae/adapters/**`. LiveAdapter (log tailer, autosave watcher with the "stable for
  2 s and at least 8 s after close" rule, atomic `sae_in.ini` writer, ack matcher), ReplayAdapter (recorded log and
  saves, speed and seek, simulated ack echo/drop/delay), SynthAdapter (labelled archetype generator), and the Python
  mod simulator.
- T3 Parser: `parser/**` (Rust, jomini). `sae-parse` CLI per contract C4: section-selective extraction, JSON
  features plus `rss_peak`, `cpu_s`, `wall_s` on stdout, exit codes, background mode set on itself (Windows),
  synthetic save fixtures, fuzz and pathological-size tests.
- T4 Engine core and daemon loop: `src/sae/core/**`, `src/sae/daemon/**`. Fog-of-war-legal features, Bayesian
  opponent model with forgetting and CUSUM change detection, strategy table, 24-month commitment and safety floor,
  intent compiler and validator (rate limits), decision audit records, the daemon main loop.
- T5 Governor and platform: `src/sae/governor/**`, `src/sae/platform/**`. Game-state × health-level Governor with
  the exact triggers and hysteresis from the resource proposal and Fable's fixes; injectable sensors; priority,
  EcoQoS, Job Object (parser only, commit limit, KILL_ON_JOB_CLOSE) and Linux equivalents; parse-worker spawn.
- T6 Telemetry and dev console: `src/sae/bus/**`, `src/sae/telemetry/**`, `src/sae/control/**`, `tools/console/**`.
  bus.jsonl writer and compaction, telemetry tiers, `sae-telemetry` to a remote Rerun viewer with memory limits,
  Rerun blueprint for the Engine Console, the `sae ctl` control plane, offline DuckDB queries.
- T7 QA, CI and bench: `tests/integration/**`, `tests/e2e/**`, `.github/workflows/**`, `bench/**`. CI-1..CI-5,
  the end-to-end game-free harness, GitHub Actions for Linux and Windows, `sae-bench` and R-1..R-6 release-gate
  scripts the maintainer runs on their own PC and second machine.
- T8 Release, docs and M0 kit: `docs/**`, `README.md`, `CHANGELOG.md`, `changelog.d/**`, `packaging/**`,
  `tools/m0/**`. User and developer docs, the M0 spike kit (scripts plus a checklist of every UNVERIFIED assumption,
  each with a pass/fail test and fallback), packaging plan, changelog stewardship.
Wave 0 (`sae-architect`) owns `contracts/**`, `src/sae/contracts/**`, `pyproject.toml`, and the repo skeleton.
</teams>

<waves>
Run these waves. Leads run their own inner waves (NESTED mode) or you run them for the leads (FLAT mode).

Wave 0: contracts and skeleton.
- `sae-architect` writes contracts C1–C9 in `contracts/`: C1 `SAE|v1|` log lines; C2 the `sae_in.ini` command file;
  C3 Intent schema v1 and its mapping to C2 commands; C4 `sae-parse` CLI and JSON output plus the synthetic save
  fixture format; C5 the bus.jsonl envelope and every event type; C6 the GameAdapter protocol, Observation model and
  fog-of-war boundary; C7 the Governor (states, health ladder, triggers, sensors, worker spawn, in-game knobs);
  C8 config, repo layout, presets and CLI entry points (`sae daemon|replay|synth|ctl|bench|m0`); C9 the control
  plane. Plus `contracts/README.md` with the ownership map, the change protocol and each contract's status.
- The typed pydantic models in `src/sae/contracts/` ARE the contract in code. JSON Schemas are exported from them to
  `contracts/schemas/`, and contract tests validate every example in the docs.
- `sae-adversary` attacks the contracts. The architect revises. Repeat until the adversary has no blocking issues,
  at most 3 rounds. Then mark all contracts FROZEN v1.
- Exit: `uv run pytest tests/contract` passes, `mypy --strict src/sae/contracts` passes, `cargo check` passes in
  `parser/`, and docs/TEAM_PLAN.md lists each team's owned paths, deliverables, acceptance tests, dependencies and
  expansion backlog.

Wave 1: parallel teams. Launch all eight `sae-team-lead`s, each with its team section from <teams>, and give
every lead the roster of the others. Each lead runs this loop:
- Plan: write `docs/plans/<TEAM>_plan.md` with work units (disjoint files), test cases (unit, property, contract,
  integration), acceptance criteria, risks and an expansion backlog. `sae-adversary` attacks the plan; the lead
  revises and records what changed.
- Implement: `sae-implementer`s in parallel on disjoint files, each writing its own tests. `sae-scout` runs tests,
  linters and builds. `sae-reviewer` reviews every change set. Fix, re-test, re-review: at most 3 loops per unit,
  then record a known gap.
- Harden: `sae-adversary` tries to break the finished component (malformed input, races, resource exhaustion,
  Windows/Linux differences, contract drift). Fix what is in scope. Final scout run.
- Hand back: `docs/status/<TEAM>.md` (delivered, exact test and lint results, gaps, proposals, backlog) plus
  `docs/components/<TEAM>.md` and a changelog fragment `changelog.d/<TEAM>.md`.
Start a team as soon as its dependencies allow; don't hold fast teams for slow ones.

Wave 2: integration, owned by `sae-architect`.
- Wire the game-free end-to-end loop: SynthAdapter or ReplayAdapter → synthetic save → `sae-parse` → daemon
  (beliefs, table, commitment, intents) → atomic `sae_in.ini` → mod simulator applies and ACKs → bus.jsonl →
  telemetry to a `.rrd` file, with the Governor managing the parse worker.
- Run the full suite. Route each failure to the owning lead, not to whoever is free.
- Loop until the exit criteria below hold or a failure needs a maintainer decision.

Wave 3: final review.
- `sae-adversary` reviews the integrated system against the design report and resource proposal and lists release
  blockers. Owning leads fix blockers.
- T8 merges changelog fragments into CHANGELOG.md, updates every out-of-date doc, and finalises the M0 checklist.
</waves>

<contracts_protocol>
- After Wave 0, contracts are FROZEN v1. Teams code against `src/sae/contracts/`.
- To change a contract, a lead writes `contracts/proposals/NNN-<team>-<title>.md` with what, why, the exact diff,
  affected consumers and status PROPOSED, then messages the owner and every consumer lead (use SendMessage if
  available; otherwise the proposal file plus a note in the lead's report, and you relay it).
- Teams keep building against the current contract until the owner marks the proposal ACCEPTED and updates the
  contract, the models, the schemas and `contracts/CHANGES.md` together.
- Any proposal that changes process topology, a settled decision in <context>, or another team's design goes to
  `sae-adversary` as advisor first. You arbitrate disputes. Escalate to the maintainer only for settled decisions.
</contracts_protocol>

<quality_bar>
Production level means: type hints everywhere and `mypy --strict` clean per package; `ruff` clean; `cargo clippy
-D warnings` and `cargo test` in `parser/`; docstrings on public APIs; explicit error handling and structured
logging; deterministic, seeded tests; property tests where inputs are parsed; no TODO stubs inside MVP scope.
Budgets from the resource proposal are enforced by tests where they can be measured here (daemon RSS ≤ 150 MB,
import whitelist, zero GPU packages, parser memory cap). Windows-only code sits behind platform checks, is unit-tested
with mocks, and is labelled "not executed in CI on this machine" when that's true.
</quality_bar>

<repository_rules>
These come from the maintainer and apply to every agent:
- Before recommending or running any git command (commit, push, branch, tag), identify out-of-date docs (README.md,
  docs/, API specs), update them, record the changes in CHANGELOG.md in its existing format, and present the diffs to
  the maintainer for approval. Do not run git commands until the maintainer approves.
- Never commit secrets, never write outside the repository except to temp folders, never touch the Stellaris
  install or the user's mod folder without explicit permission.
</repository_rules>

<mvp_exit_criteria>
The MVP is done when all of these hold on this machine:
1. The game-free end-to-end loop runs from `sae synth` and from `sae replay` and produces a bus.jsonl in which every
   decision has observation → belief → strategy → intent → ack, and a `.rrd` recording.
2. CI-1..CI-5 pass; all unit, property, contract, integration and e2e tests pass; mypy, ruff, clippy are clean.
3. The mod in `mod/sae_bridge/` is complete for M0, with every unverified construct listed in the M0 checklist.
4. `tools/m0/` lets the maintainer run the M0 spike on their PC in one weekend, with pass/fail recording.
5. R-1..R-6 scripts exist with instructions; they are marked "requires the game" and not claimed as passed.
6. Docs, component docs, status files and CHANGELOG.md are current.
</mvp_exit_criteria>

<expansion>
After the MVP exit criteria hold, launch one `sae-team-lead` as T9 Expansion, planning only, no code. Inputs:
context/research_notes/Player profiling and RSI/ (proposal.md, verification_notes.md, and fable_review.md and
haiku_reverification.md if present). If those two reviews are missing, run them first: a `sae-scout` independently
re-verifies the five research leads in the proposal against primary sources, and `sae-adversary` reviews the
proposal. T9 then writes `docs/expansion/player_profile_and_rsi_plan.md`: contract proposals (new bus events, a
profile store, a campaign index), the snapshot-and-diff extractor, the fog-perturbation test, held-out-campaign
detector tuning, and the two-stage acceptance gate. Runtime self-modification stays off; only validated, versioned
policies ship, and a human approves each one.
</expansion>

<reporting>
- Keep the task list current: one task per wave and per team.
- Between waves, send the maintainer a short status: what finished, test results, open proposals, and any decision
  that needs them. Don't narrate individual tool calls.
- At the end, write `docs/BUILD_REPORT.md`: what was built, exact final test results, known gaps, UNVERIFIED
  assumptions awaiting M0, open maintainer decisions, and the recommended next step. Then present the diff summary
  for approval per <repository_rules>.
</reporting>

Begin with <preflight>, then Wave 0.
