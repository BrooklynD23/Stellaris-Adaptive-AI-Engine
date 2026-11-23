# DEVDASH alignment review (Fable gate, CLAUDE.md rule)

Date: 2026-10-29. Reviewer: sae-adversary (Opus 5.5 standing in for Fable 5.1, owner authorization 2026-10-28).
Scope: `git diff main..HEAD` on `feat/devdash` (HEAD 7c69ec0): `tools/devdash/**`, `tools/game/{pipeline,gates}.py`,
`gates.json`, `.claude/settings.json`, hook changes, `tests/unit/devdash/**`, two `tests/game` property lines. No code
edited; this file is the only write.

## Verdict: ACCEPT WITH CHANGES (4 small fixes before the pipeline is used on a real campaign)

| Axis | Rating | Evidence |
|------|--------|----------|
| 1. Performance and memory | AT RISK | Server is light: measured idle 0.1 % CPU, RSS ~24 MB (budget 50 MB), 2 SSE clients 0.7 %, 8 clients 4.3 %, cap 8 (`server.py:60`). Hooks are opt-in and detached (`.claude/settings.json`, measured 1-4 ms). Watcher stats only, game check every 60 s (`pipeline.py:65-66,536`). Priority/affinity: nothing calls `os.nice`, `setpriority`, `taskkill` or touches `stellaris.exe` beyond `tasklist.exe` (`pipeline.py:11`, grep clean). The risk is the pipeline, not the server: parse-heavy X3 stages and 13-save copies run at full speed even when `game_running` is true, and under WSL2 that cost lands on the game through the VM (see actions). |
| 2. Telemetry | ON TARGET | Separate file, schema (`{"v":1,...}`), directory (`.sae-dev/`) and port (8765 vs Rerun 9876); nothing writes `runs/` or the engine bus. God bus: `_legal_bus` confines `run_dir` to repo/`.sae-dev`, rejects symlinks and requires the resolved name `bus.jsonl` (`state.py:431-445`). Single rotator (`server.py:152-171`, `events.py:161-184`). Losses are view-only: a line appended between `tail.poll()` and `os.replace` lands in `events.N.jsonl` unseen live (deferred minor); on restart right after a rotation the feed is empty (spec 6.1 "last 24 hours" not implemented); rotated files are never pruned. All data stays on disk. |
| 3. Properly adaptive engine | ON TARGET | `test_boundary.py` (AST + substring, 11 planted forms and `.pyi`) re-run: 13 passed. `grep devdash\|sae-dev src/sae` empty. Wheel ships `src/sae` only (`pyproject.toml:35`). No LLM client anywhere in `tools/devdash` or the pipeline; hooks only log Claude's own dev activity. Server, pipeline and hooks are dev-only, opt-in (`.sae-dev/enabled`), and never start `sae daemon` (R-5 is owner-run), so the three-process runtime is untouched. |
| 4. Readiness to learn from saves (bounded RSI) | AT RISK | Helps: copy-before-parse with lock retry (`pipeline.py:407-423`), real per-save `parse_costs` evidence (`tests/game/test_saves.py:68-69`), legal-bus-only Results. Hinders: (a) X3-G2 numbers come from the Linux `sae-parse` reading drvfs copies, possibly while the game runs, yet are shown unlabelled next to the Windows A-24 budget; the dashboard drops `rss_bytes` (`state.py:473-476`), the memory number the RSI parse path depends on. (b) Raw saves are kept with no retention: every run makes a new `.sae-dev/pipeline/<ts>/private/` holding up to 13 saves plus pytest basetemp copies. (c) `default_scrub` only replaces the WSL `$HOME` (`gateindex.py:58-64`); the Windows user name in `/mnt/c/Users/<name>/...` is served in the sidebar from `env.json` (confirmed present) and printed by `--dry-run`. (d) A save mid-write can be copied torn (Stellaris write behaviour UNVERIFIED); `_same` checks size+mtime only. |

## Gates re-run (bounded)

| Check | Result |
|------|--------|
| `uv run pytest -q tests/unit/devdash/test_boundary.py` | 13 passed |
| `grep -rln "devdash\|sae-dev" src/sae` | no hits |
| `.sae-dev/env.json` `user_dir` | contains an unredacted `/mnt/c/Users/<name>/` |
| Full suite | not run (instructed); last recorded clean run in progress.md, Task 10 |

## Must fix (axes 1 and 4)

1. **Do not compete with the game.** In `pipeline.py`, before `run_pytest_stage`/`run_deferred` and before
   `copy_inputs`, call `game_running(runner)`; if true, emit `owner` "close Stellaris to run parse gates (or pass
   `--while-game`)" and wait. `nice` inside WSL does not help: the WSL2 VM's vCPUs compete with the game at normal
   priority on the Windows side. This also keeps X3-G2 timings uncontaminated.
2. **Label and keep parse cost honestly.** In `test_saves.py` add `"platform": sys.platform, "via": "wsl-copy"` (or
   the parser target) to each `parse_costs` row; carry it and `rss_bytes` through `collect_results` and
   `state._results`, and show "Linux build, WSL copy, UNVERIFIED A-24" on the Results chart. A WSL pass must never be
   quoted as A-24 met.
3. **Retention for raw saves.** After a run finishes (or at the start of the next one), delete
   `private/inputs/saves` and `private/tmp` of every older pipeline run except the newest N (default 1), and
   document it in `tools/devdash/README.md` Privacy and `GAME_PC_AGENT.md` §8.
4. **Scrub the Windows profile path.** Extend `default_scrub` (and `events.redact`) to replace
   `/mnt/<d>/Users/<name>` and `C:\Users\<name>` with `<WINHOME>`; apply it to `env.json` rows and `--dry-run` output.
   Add a test with a planted Windows path.

## Should fix (not blocking)

5. Skip a save whose mtime is under 15 s old, or require two equal `stat` results 2 s apart before copying
   (`copy_inputs`), so a torn save is not parsed and failed as a parser bug.
6. Gate `hub.input_sig()` in the SSE loop on `STATE_INTERVAL_S` (deferred minor, `server.py:490`); it does 5 drvfs
   stats per client every 0.1 s.
7. Rotation: after `os.replace`, read the renamed file from the old offset to its end before switching inodes; on
   start, seed the tail from the newest `events.N.jsonl` when the current file is small; prune to the newest 5
   rotated files. Rotation also never happens while the server is down (writers never rotate); acceptable.
8. Add `tools.game`/`tools/game` to the boundary `FORBIDDEN` list: `pipeline.py` imports `devdash`, so a
   `src/sae` import of the pipeline would pull devdash in transitively.
9. `idle_seconds` is 0 whenever an SSE client is connected, so a forgotten tab keeps the server alive past 12 h.
   Count only events for the idle exit, or cap at 24 h.

## Bottom line

The dashboard stays out of the runtime, never reads the god bus and costs ~24 MB and ~0 % CPU, so the engine and
telemetry axes hold. The game-test pipeline needs to stand down while Stellaris runs, label WSL parse costs as
unverified, keep RSS, and stop accumulating unredacted raw saves before its numbers can feed the save-learning path.

## Follow-up, 2026-11-23 (fixes verified, first real-save run)

Opus re-review of the staged fixes: ACCEPT WITH CHANGES; must-fix 1-4 and should-fix 5, 8 DONE. Its three findings
(spaced-name `<WINHOME>` leak, `stand_down` ignoring a game close, `_settled` trusting old-looking files) are fixed
with tests. Full gates: pytest, `mypy --strict src tools`, ruff, clippy, cargo test pass.

`pipeline.py --stages x3` on the dev PC's real saves (Linux `sae-parse` on WSL copies, UNVERIFIED A-24), 5 autosaves
2326.07-2328.07 of one campaign:

| Gate | Result | Note |
|---|---|---|
| X3-G0, G1, G2, G3, G8, G10 | pass | |
| X3-G4, G6 | skip | Windows uv not installed; no ironman save |
| X3-G5 | fail | autosaves 6 months apart (gaps [6,6,6,6]); the game's autosave setting, not a parser bug. A-24 still open: M0 must set Monthly |
| X3-G7, G9, G11 | fail | `game.log` has no `SAE|` lines: `sae_bridge` never loaded. Needs the M0 session |

First run picked a folder holding one manual repro save; `copy_inputs` now prefers folders with autosaves.
