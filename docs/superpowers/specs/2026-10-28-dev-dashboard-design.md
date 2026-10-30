# Dev dashboard and game-test pipeline — design

Date: 2026-10-28. Status: draft for maintainer approval. Visual reference (approved look and content):
https://claude.ai/artifact/UPdDTUuQ95wzrqt3iH5djk (mockup, sample data).

## 1. Intent

The maintainer acts as PM for SAE. Work happens in Claude Code sessions where an orchestrator, subagents and
scripts act in parallel, and, now that the game is available on this PC, in Stellaris itself. Today the only
view of that work is terminal scrollback, status markdown and PR pages. The maintainer wants one local page that
answers, at a glance:

1. **What is happening right now?** Which agent or script is doing what, as short live messages.
2. **Where is the project?** Every task as done, in progress, pending or blocked.
3. **Does the engine work in the real game?** Every game gate (X3, M0, X2, R) with its result and the reason.
4. **What did the game runs show?** Parse cost, the engine's belief about the player, the counters it chose,
   and the game-impact numbers.
5. **Do I need to do something?** A single prominent "Waiting on you" when a step needs the maintainer to play.

Alongside it, a **game-test pipeline** runs the game-PC validation in order, reports every step to the dashboard,
and stops cleanly at each step that needs the maintainer.

## 2. Goals

- G1. A local web dashboard, always running during a dev session on this repo, started automatically.
- G2. Four views, as in the mockup: **Overview** (running now + activity feed), **Tasks** (board),
  **Game gates** (grid + detail), **Results** (charts). Summary KPIs and the "Waiting on you" banner on every view.
- G3. Live updates: a new event shows up within about 2 seconds, with no manual refresh.
- G4. Every action is visible without relying on agent discipline. Claude Code hooks log agent launches and
  finishes and each shell command's description automatically. Agents and scripts can add richer messages.
- G5. A pipeline command that runs X3-G0..G11, M0-1..M0-14 (via `sae m0`), X2-G1..G3 and the R-gates that
  can run on one PC. It records results in the files the repo already uses, emits progress, and pauses at
  owner steps.
- G6. Results survive restarts: the dashboard rebuilds its state from files on disk.

## 3. Non-goals

- Not a runtime component. Nothing here ships to players, runs in the daemon or is imported by `src/sae`.
- Not a replacement for engine telemetry. Rerun (`sae telemetry`) and the DuckDB console (`tools/console/`)
  remain the tools for engine internals. The dashboard shows summaries and links to them.
- Not hosted or shared. Localhost only, single user, no accounts.
- Not a task tracker with editing UI. v1 is read-only apart from two owner actions: "Mark done" on an owner
  step, and the **verdict form** for gates the maintainer judges (§6.5).
- No X4-G0 (six live campaigns) in v1. It is a later milestone. The gate shows as "later".

## 4. Anti-goals (failure even if it technically works)

- **It slows the game.** The dashboard and pipeline must stay light while Stellaris runs: no busy polling of
  save folders, and no parsing while the game holds a file. Never touch the game's priority or affinity
  (settled decision).
- **It leaks god-view data.** It reads only the legal bus (`bus.jsonl`), never `bus.god.jsonl`, the same fog rule
  as `tools/console/`. Raw saves, logs and recordings are never served. Only derived numbers and messages are.
- **It drifts from the repo.** A second hand-kept task list that disagrees with `docs/` is a failure.
  Gate status comes from result files, never typed by hand.
- **It claims passes it can't show.** A gate is "Passed" only if a result file says so. Missing data shows as
  "Not run" or "No data", never as a placeholder number.
- **It needs a build step or new runtime dependencies.** Python stdlib server, one static HTML/JS/CSS bundle, no
  npm and no CDN (it must work offline).

## 5. Constraints (from the repo and the owner)

- Settled decisions in `CLAUDE.md` stand: no LLM in the runtime loop, no GPU through M4, never touch priority or
  affinity, Ironman has no live loop.
- Stellaris script, console and save behaviour stays labelled **UNVERIFIED** until M0 proves it. The dashboard
  shows the label next to any result that depends on an unproven assumption (A-xx).
- Owned paths (`docs/TEAM_PLAN.md`): the pipeline sits beside `tools/game/` (T7). The dashboard is new dev tooling
  with no owner yet (see §9, decision D1). Doc changes go through T8. The `TEAM_PLAN.md` edit goes through the
  architect.
- Owner authorization 2026-10-28: Claude may launch Stellaris and write to its install and mod folders for
  validation. The pipeline still pauses for any step that needs the maintainer to play (§7.4).
- Game location (found on this PC, detect, never hard-code): install `D:\Steam\steamapps\common\Stellaris`,
  user data `C:\Users\<user>\OneDrive\Documents\Paradox Interactive\Stellaris`. The dev session runs in WSL2 on
  the same machine (`/mnt/d/...`, `/mnt/c/...`, `cmd.exe` interop available).
- User data lives under **OneDrive**. Sync can lock or rewrite files. Copy saves before parsing, and treat a
  sharing violation (`sae-parse` exit 6) as "retry later", not a failure.
- Gates: `uv run pytest -x -q`, `uv run mypy --strict`, `uv run ruff check`, and the Opus review gate per wave.

## 6. Architecture

```
Claude Code hooks ─┐
agents (emit) ─────┼──► .sae-dev/events.jsonl ──┐
pipeline ──────────┘                            │
                                                ├──► devdash server (127.0.0.1:8765) ──SSE──► browser
result files (junit, m0_results/, bench CSVs,   │       stdlib ThreadingHTTPServer
 runs/<run>/bus.jsonl, docs/dev/tasks.json) ────┘       + static index.html/app.js/app.css
```

### 6.1 Event log

- File: `.sae-dev/events.jsonl` at the repo root, append-only, git-ignored. One JSON object per line:
  `{"v":1,"ts":"<RFC3339>","source":"<pipeline|orchestrator|<agent name>|stellaris|ci|hook>","kind":"<info|start|progress|pass|fail|skip|owner|done>","ref":"<task or gate id, optional>","msg":"<one line, ≤200 chars>","pct":<0-100, optional>,"session":"<claude session id, optional>"}`.
- Writers append with a single `write` of one line (O_APPEND), so concurrent writers do not interleave lines. The
  reader skips invalid or truncated lines and counts them, like the bus reader.
- Rotation: when the file passes 5 MiB, roll it to `events.<n>.jsonl`. The server reads only the current file
  plus whatever the last 24 hours need.
- Redaction: replace home directories with `<HOME>` before writing, the same rule as `run_deferred.py`.

### 6.2 Emitters

- **CLI:** `tools/devdash/emit` (a small stdlib script, also runnable as `uv run python3 tools/devdash/emit.py`):
  `emit [--source S] [--kind K] [--ref ID] [--pct N] "message"`. Agents call it at the start and end of each
  work order and at meaningful milestones. The instruction goes into the agent definitions in `.claude/agents/`.
- **Claude Code hooks** (project `.claude/settings.json`, committed):
  - `SessionStart` → start the server if it isn't running (§6.5), then emit `session started`.
  - `PreToolUse` on `Agent` → emit `start` with the agent's type and description.
    `SubagentStop` → emit `done`.
  - `PreToolUse` on `Bash` → emit `info` with the tool's `description` field (never the raw command, which can
    contain paths or secrets).
  - Hooks must return within 100 ms and never block the tool. A failure to write is swallowed.
- **Pipeline** emits through the same library function (§7).
- **Stellaris** events come from the pipeline's watchers: autosave written, `game.log` SAE lines seen, game
  process started or exited.

### 6.3 Data sources the server reads (read-only)

| View | Source |
|---|---|
| Running now, activity | `events.jsonl`: an item is "running" from its `start` until a matching `done`/`pass`/`fail` with the same `ref`/`source`, or until 30 min of silence (then "stale") |
| Tasks | `docs/dev/tasks.json` (new, tracked, §6.4) + open PRs via `gh pr list --json` when `gh` is available (cached 60 s) |
| Game gates | Gate registry (§7.2) + latest results: pipeline junit (`<out>/private/junit.xml`, `user_properties` for evidence), `m0_results/*.json` via `sae.m0.results` readers, bench CSVs under the pipeline out dir |
| Results | junit evidence (X3-G2 wall/RSS), `sae bench` outputs, and for belief/commitments the latest `runs/<run>/bus.jsonl` from a real-game replay (legal bus only, read like `tools/console/views.sql`'s `v_belief` and `v_decisions`) |
| KPIs | derived from the above; CI status from `gh run list` (cached 60 s) |

The server may import `sae.m0.results` and `sae.contracts` for typed reads. It must never be imported by
`src/sae` (add it to the CI-4 boundary check).

### 6.4 Task board file

- `docs/dev/tasks.json`, tracked: `[{"id","title","note","status":"done|in_progress|pending|blocked","tag","link","blocked_on","updated"}]`.
- Edited by `tools/devdash/emit task <id> --status S [--note …]`, which rewrites the file atomically and emits an
  event. The orchestrator updates it when work starts, merges or blocks, as part of the existing doc-update
  rule before git actions.
- Seeded once from the current state: the handoff list, `TEAM_PLAN.md` validation backlog V-1..V-5, open PRs and
  the follow-ups list. After that it is the single place task status lives. Status files in `docs/status/` stay the
  narrative record and link to task ids.
- Gate-shaped items (X3-Gn, M0-n, R-n) never go in this file. They come from results (anti-goal: drift).

### 6.5 Server

- `tools/devdash/server.py`: stdlib `http.server.ThreadingHTTPServer`, bound to `127.0.0.1` only. Default port
  8765, overridable with `SAE_DEVDASH_PORT`.
- Endpoints: `GET /` static bundle; `GET /api/state` full snapshot; `GET /api/events?since=<offset>`;
  `GET /api/stream` Server-Sent Events (tails `events.jsonl` with a 1 s stat loop, no inotify dependency);
  `POST /api/owner/<ref>/done` (appends a `done` event: "I finished playing"); and
  `POST /api/owner/<ref>/verdict` (decided with the maintainer, 2026-10-28: owner judgements are given on the
  dashboard). Both require the per-run token from `.sae-dev/token`, which is embedded in the page, against
  cross-site requests from other local pages.
- **Verdict form.** Gates whose result is a human judgement (the M0 checks, R-3 and R-5, anything
  `sae m0 record` accepts) show **Pass / Fail** buttons, one input per evidence value the checklist names for that
  gate (for example M0-5 "fleet value in UI"), and an optional note in the gate detail panel and the owner
  banner. Submitting runs `uv run sae m0 record <id> <pass|fail> --value K=V … --note …` as a fixed argv list
  (no shell). The id must be in the registry and in `sae m0 record`'s choices, the verdict must be pass or fail,
  keys come from the registry (not free text), values ≤ 200 printable chars, the note ≤ 500. The gate turns
  Passed/Failed only once `m0_results/<id>.json` exists (the result file stays the source of truth). The form shows
  `record`'s stderr on failure. A verdict can be re-submitted, which overwrites the result the same way the CLI
  does, and the activity feed records each submission.
- The registry (§7.2) lists, per judged gate, the evidence fields and the question shown to the maintainer, taken
  from `docs/m0_checklist.md`.
- Single instance: pid and port in `.sae-dev/server.json`. `tools/devdash/ensure_running` checks that the pid is
  alive and `/api/state` answers, else starts it detached (`nohup … &`, logs to `.sae-dev/server.log`). Hook-safe:
  returns at once.
- Stays running across sessions until `tools/devdash/stop`, a reboot, or 12 h with no events and no browser
  connected (then it exits).
- Budget: ≤ 50 MB RSS, ~0 % CPU when idle, and snapshot recompute only when an input file's mtime changes.
- The URL is printed by the `SessionStart` hook so it appears in the session.

### 6.6 Front end

- `tools/devdash/static/{index.html,app.js,app.css}`: vanilla JS, no framework, no build, no CDN. Fonts: system
  stack falling back from Inter/JetBrains Mono (bundled woff2 optional).
- Matches the mockup: sidebar (views + environment status), top bar (branch / view, Live pill, port), owner
  banner, KPI strip, and the four views. Dark theme first, light theme via `prefers-color-scheme`.
- Works at laptop and phone widths (the sidebar stacks).
- "Run pipeline" in the mockup becomes **copy command** in v1: the server never starts processes on a browser
  request.
- Accessibility: real buttons, keyboard-navigable gate grid, AA contrast, status shown by text as well as colour.

## 7. Game-test pipeline

### 7.1 Command

`uv run python3 tools/game/pipeline.py [--stages x3,m0,x2,r] [--from <gate id>] [--out <dir>] [--launch] [--dry-run]`

- Owned by T7, next to `run_deferred.py`. It **reuses** `run_deferred.py`, `sae m0` and `sae bench`, and never
  reimplements their checks.
- `--out` defaults to `.sae-dev/pipeline/<timestamp>/`, with the same `summary.md` / `inputs.json` / `private/`
  layout and privacy rules as `run_deferred.py`.
- State file `<out>/state.json` records each gate's status, so `--from` and re-runs resume. Re-running a passed
  gate requires `--rerun`.
- `--dry-run` prints the plan and preflight results without running anything.

### 7.2 Gate registry

`tools/game/gates.py` holds the ordered list of gates: id, stage, title, how it runs (pytest selector / `sae m0`
step / `sae bench` subcommand), the inputs it needs (`SAE_GAME_*` vars, files), whether it needs owner play, and
the A-xx assumptions it depends on. The registry is the single list that both the pipeline and the dashboard use.
A test (T7) checks that every `@pytest.mark.game(id=…)` and every `docs/m0_checklist.md` row maps to a registry
entry and vice versa.

### 7.3 Stages

1. **Preflight** (always): detect the install and user-data folders (Steam `libraryfolders.vdf`, then known
   paths), the `sae-parse` binary (Windows build for Windows-side paths, or a Linux build reading copies), the
   OneDrive location, game running or not (`tasklist.exe`), mod staged or not. Emit an environment event, which
   feeds the sidebar.
2. **X3** (X3-G0..G11) via `run_deferred.py` with `-k` per gate, emitting `start`/`pass`/`fail`/`skip` per gate from
   junit. Gates needing inputs the PC doesn't have yet are `skip` with the reason.
3. **M0** (M0-1..M0-14) via `sae m0 steps/stage/check/record`, in the runbook's order: critical first, stretch
   last. Each "play N months" step is an owner step.
4. **X2** (X2-G1..G3) once PR #4 is merged. Before that, the stage reports "waiting on PR #4" and skips.
5. **R** (R-gates) only those runnable on one PC: R-3, R-5 and the parts of R-1 that `sae bench spy` covers.
   R-1 (second machine) and R-6 (4-core laptop) show as "needs other hardware".

### 7.4 Owner steps and the game

- An owner step emits `kind: owner` with a one-line instruction and what will resume it (for example "the next
  autosave in `<campaign>`" or "Mark done"). It sets the banner and waits. The wait is a 10 s stat loop on the
  expected file, or the Mark-done event, with no timeout. Ctrl-C leaves state resumable.
- With `--launch`, the pipeline may start Stellaris (`cmd.exe /c start steam://rungameid/281990`) and stage
  `sae_bridge` into the user mod folder (owner-authorized 2026-10-28), but it still waits for the maintainer to
  play. Without `--launch` it only tells the maintainer what to start.
- Saves are parsed only after the game has released them (copy first; exit 6 → retry in 30 s, up to 10 times).
- The pipeline never sets the game's process priority or affinity, and never kills the game.

### 7.5 Results for the Results view

The pipeline writes `<out>/results.json` with the numbers the charts need: per-save parse wall/RSS (from junit
evidence), bench outputs, and the path of any replay run directory built from real recordings (X3-G9/G11). The
dashboard reads the newest `results.json` and the legal bus of that run.

## 8. Delivery and checks

- Tests: unit tests for the event writer/reader (concurrent append, truncated line), task file round-trip,
  snapshot derivation from fixture files, the registry ↔ markers ↔ checklist mapping, and server endpoints
  (snapshot, SSE first message, token check on POST, verdict validation: unknown id, bad verdict, unknown key,
  oversize or control-char value all rejected before any process starts; a valid verdict calls a fake `record`).
  Because the verdict endpoint starts a process, the Opus review covers it as security-sensitive. The pipeline gets `--dry-run` tests and stage tests with fake
  `run_deferred`/`sae m0` runners. No test touches the game.
- The CI-4 boundary check gains "`tools/devdash` is never imported by `src/sae`".
- Docs: `tools/devdash/README.md`, a `docs/developer_guide.md` section, a `GAME_PC_AGENT.md` pointer to the
  pipeline, a changelog fragment, and a `.gitignore` entry for `.sae-dev/`.
- Opus plan review before implementation and Opus wave review after, per the repo rule.

## 9. Decisions and what's left to the builder

Decided with the maintainer: the four views and their content as in the mockup; the SaaS-style dark look; always
on locally during dev sessions; a pipeline that pauses for the maintainer's play; owner judgements given on the
dashboard by clicking Pass or Fail and typing the observed value.

Orchestrator's calls (the maintainer can override):
- D1. Ownership: `tools/devdash/` goes to **T7** (QA/CI/bench already owns `tools/game/` and `tests/game/`), and
  `docs/dev/tasks.json` goes to T8. Recorded in `TEAM_PLAN.md` by the architect.
- D2. The dashboard lives outside `src/sae` and is not a `sae` subcommand, so it never ships and never enters the
  daemon's import graph.
- D3. Stdlib server + SSE + vanilla JS. No FastAPI, no npm, no CDN.
- D4. Task status lives in one tracked JSON file. Gate status is always derived from results.
- D5. Hooks log shell commands by their description only, never the command text.
- D6. Apart from the verdict form's fixed `sae m0 record` call, the browser can't start processes in v1.
  "Run pipeline" copies the command.
- D7. Port 8765, localhost only, a 12 h idle exit.
- D8. **Where it runs.** The pipeline, server and hooks run under WSL Python (the repo's `uv` venv). Saves and logs
  are copied from `/mnt/c/...` into `<out>/private/` and parsed with the Linux `sae-parse`. Gates that only mean
  something on Windows (X3-G4 clean Windows failures, R-5 Windows Job cap, real sharing-violation behaviour) run
  through a Windows-side runner (`cmd.exe /c uv run …` in a Windows checkout or venv). Today Windows has no `uv`
  (checked 2026-10-28), so those gates report **"setup needed: Windows uv"** with the install command, and the
  pipeline never installs software on Windows by itself. Writers never share a file across the WSL/Windows
  boundary: each writer kind appends to its own file (`events.jsonl` for WSL, `events.win.jsonl` for Windows-side
  runs) and the server merges them by `ts`.
- D9. **Gate status across runs.** A gate's status is the **newest result for that gate from any run**, including
  manual `run_deferred.py` and `sae m0` runs done per the runbook. The pipeline writes M0 results to the shared
  repo-root `m0_results/` (the `sae m0` default), not a per-run folder. X3 results come from each run's junit,
  indexed into `.sae-dev/gates.json` (gate id → newest status, evidence, run folder, time). `run_deferred.py` gains
  a small hook to update that index so manual runs count too. `--from` without `--out` resumes the newest
  run folder.
- D10. **X2 before PR #4 merges.** The registry carries X2-G1..G3 as placeholder entries marked `pending_source:
  "PR #4"`, exempt from the registry ↔ markers mapping test until their markers exist. The X2 stage checks for the
  markers in the repo (not `gh`), and its result format follows whatever PR #4 defines.
- D11. **R gates.** The dashboard lists R-1..R-6. The pipeline runs only what one PC can do: R-3 and R-5 (owner
  play for mod-on/off runs; R-5 also needs Windows uv) and the `sae bench spy` part of R-1. R-1 needs a second
  machine and R-6 a 4-core laptop, so both show "needs other hardware". An R gate's result is
  `sae m0 record R-x` (what `docs/m0_checklist.md` already says), with bench stdout kept in `<out>/private/` as
  evidence. The pipeline may start `sae daemon` only for R-5's condition (c), and stops it afterwards.
- D12. **Hooks are opt-in.** `.claude/settings.json` is committed (the repo is public), but every hook first
  checks for `.sae-dev/enabled` (git-ignored) and exits at once if it's absent. `tools/devdash/enable` creates it.
  Other contributors get no server and no logging unless they opt in. The `emit` lines added to
  `.claude/agents/*.md` are no-ops when not enabled. `.claude/**` gets an owner via the architect in
  `TEAM_PLAN.md` (proposed: T7).

Left to the builder: module layout inside `tools/devdash/`, chart drawing (inline SVG or CSS), exact SSE
reconnect policy, how stale items look, and the seed content of `tasks.json` (from the handoff and
`TEAM_PLAN.md`).
