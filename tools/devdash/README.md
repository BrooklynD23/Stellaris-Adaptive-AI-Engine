# Dev dashboard

A local, dev-only dashboard for the game-test work: an event log, a task board, gate status derived from result
files, and a page that shows them. It is never imported by `src/sae` (enforced by
`tests/unit/devdash/test_boundary.py`). Design: `docs/superpowers/specs/2026-10-28-dev-dashboard-design.md`.
Everything about Stellaris behaviour that the gates exercise is UNVERIFIED until M0 proves it (A-xx ids where a gate has one, in
`tools/game/gates.json`).

## Enable

```bash
python3 tools/devdash/dd.py enable      # creates .sae-dev/enabled (git-ignored)
python3 tools/devdash/dd.py disable     # removes it
```

Without `.sae-dev/enabled`, hooks do nothing. The committed `.claude/settings.json` registers opt-in Claude Code
hooks (`SessionStart`, `PreToolUse` for `Agent|Task|Bash`, `SubagentStop`). Each one first checks for the flag file,
runs `python3 tools/devdash/dd.py hook <event>`, and always exits 0. `SessionStart` runs synchronously (5 s timeout) because it starts the server and returns the session context; `PreToolUse` and `SubagentStop` run detached. The hook code is stdlib-only,
so bare `python3` is enough.

## Run

```bash
python3 tools/devdash/dd.py ensure      # start the server if needed, print its URL
python3 tools/devdash/dd.py serve       # or run it in the foreground
python3 tools/devdash/dd.py stop
```

URL: `http://127.0.0.1:8765/` (127.0.0.1 only). Override the port with `SAE_DEVDASH_PORT` or `--port`. The server
checks `Host` and `Origin` and exits after 12 h idle. Endpoints: `/api/health`, `/api/state`, `/api/events`,
`/api/stream` (SSE), and `POST /api/owner/<gate>/{done,verdict}` (needs the page's token). Processes the server starts: `sae m0 record` (the verdict form), and cached, 10 s-bounded `gh pr list` and `gh run list` calls for the PR and CI panels.

## Commands (`tools/devdash/dd.py`)

| Command | Purpose |
|---|---|
| `emit [--source S] [--kind K] [--ref ID] [--pct N] MSG` | append one event; kinds: info, start, progress, pass, fail, skip, owner, done |
| `task ID [--status S --title T --note N --tag G --link L --blocked-on B]` | create or update a task in `docs/dev/tasks.json` (`--status` and `--title` are needed only when creating) |
| `enable` / `disable` | switch the event log on or off |
| `serve` / `ensure` / `stop` | run, start-if-needed, or stop the server |
| `hook {session-start,pre-tool,subagent-stop}` | Claude Code hook handler (JSON on stdin; always exits 0) |

Events go to `.sae-dev/events.jsonl` (WSL writer) and `.sae-dev/events.win.jsonl` (Windows-side runs); the server
merges them by time and rolls a file over at 5 MiB.

## Game-test pipeline

`tools/game/pipeline.py` runs the game-PC gates (X3, X4, M0, X2, R) in order and reports each step here:

```bash
uv run python tools/game/pipeline.py --dry-run          # plan and preflight, runs nothing: do this first
uv run python tools/game/pipeline.py --stages x3,x4     # --stages x3,x4,m0,x2,r
uv run python tools/game/pipeline.py --from X3-G4       # resume at a gate (newest run)
```

Other flags: `--out DIR` (default `.sae-dev/pipeline/<timestamp>`), `--rerun`, `--launch`, `--max-saves N`
(default 13), `--while-game`, `--keep-runs N` (default 3). Gates that only mean something on Windows report "setup needed: Windows uv" and are never run under
WSL (once Windows `uv` exists they skip with "setup needed: Windows-side runner (not built)"); the pipeline installs nothing on Windows. A gate shows passed only when a result file says so; missing data
shows "No data". Gate registry: `tools/game/gates.json`, runbook: `docs/handoff/GAME_PC_AGENT.md`.

The pipeline does not compete with the game. Before the X3/X4 parse stages (and before copying saves for other
non-owner-play gates) it checks `tasklist.exe`; if Stellaris is running it posts an owner step "close Stellaris to run
<stage>, or rerun with --while-game" and waits (Mark done, or Enter without the dashboard; with neither a dashboard
nor a terminal the gates are skipped). `--while-game` skips the wait. It only reads the process list and never touches
the game's process, priority or affinity. A save is copied only once its mtime is about 15 s old (UNVERIFIED: how
Stellaris writes a save; M0 would watch one autosave grow).

Parse costs on the Results view come from the Linux `sae-parse` reading a WSL copy of the save, not from the Windows
budget machine. Each row carries `platform` (`sys.platform`, plus `+wsl`) and `source` (`wsl-copy`), and the chart
labels them "UNVERIFIED A-24 (measured on <platform>)" with the peak RSS. A WSL pass is never an A-24 pass.

## Privacy

Logged: event source, kind, ref, a message of at most 200 characters with the home directory replaced by `<HOME>`
control characters flattened, and the Claude `session_id`. For Bash tool calls the hook logs only `tool_input.description`; for Agent/Task
calls the description and `subagent_type`. Never logged: shell commands (`tool_input.command`), prompts, tool
output, file contents. The server reads the legal bus only (never `bus.god.jsonl`) and never serves raw saves, logs
or recordings. `.sae-dev*/` is git-ignored.

Windows profile paths (`/mnt/<drive>/Users/<name>`, `C:\Users\<name>`, `C:/Users/<name>`, any drive and case) are
replaced by `<WINHOME>` in event messages, the sidebar (`env.json` rows), the Results view and `--dry-run` output.
`env.json` itself still holds the real paths on disk.

Retention: every pipeline run copies saves and pytest basetemp under `.sae-dev/pipeline/<timestamp>/private/` (and
`<stage>/private/`). At the start of a run the pipeline deletes those `private/` folders for all but the newest N runs
(`--keep-runs N`, default 3; the current run always stays). It removes only `private/` folders inside timestamp-named
run folders directly under `.sae-dev/pipeline`, never follows a symlink, and keeps `state.json` and `results.json`.
