# Dev Dashboard and Game-Test Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local, always-on dev dashboard (Overview / Tasks / Game gates / Results) fed by an event log, plus a
game-test pipeline that runs the game-PC gates in order and pauses for the maintainer.

**Architecture:** Writers (Claude Code hooks, an `emit` CLI, the pipeline) append JSON lines to
`.sae-dev/events*.jsonl`. A stdlib `ThreadingHTTPServer` on 127.0.0.1 derives one JSON snapshot from the events,
`docs/dev/tasks.json`, a gate registry and result files (junit, `m0_results/`, pipeline `results.json`, legal bus),
and pushes it over SSE to a vanilla-JS page. The pipeline wraps `run_deferred.py`, `sae m0` and `sae bench`.

**Tech Stack:** Python 3.12 stdlib only (http.server, json, subprocess, xml.etree), vanilla HTML/CSS/JS, pytest,
mypy --strict, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-28-dev-dashboard-design.md` (read it first; §n references below point there).
Visual reference: https://claude.ai/artifact/UPdDTUuQ95wzrqt3iH5djk

## Global Constraints

- Nothing under `tools/devdash/` or `tools/game/` is imported by `src/sae` (spec D2). Code that hooks run
  (`tools/devdash/dd.py hook …` and the modules it imports) is **stdlib-only** so it runs under bare `python3`.
- Bind `127.0.0.1` only. Default port 8765, override `SAE_DEVDASH_PORT` (D7).
- Read the legal bus `bus.jsonl` only, never `bus.god.jsonl`; never serve raw saves, logs or recordings (§4).
- Never set or read the game's process priority or affinity; never kill the game (settled decision).
- A gate is shown passed only when a result file says so; missing data renders as "No data", never a number (§4).
- Event `msg` ≤ 200 chars, home directory replaced by `<HOME>` (§6.1). Event log rolls over at 20 MB.
- Hooks are no-ops unless `.sae-dev/enabled` exists, return within 100 ms, always exit 0 (D12, §6.2).
- Hooks log `tool_input.description` for Bash, never `tool_input.command` (D5).
- Windows-only gates report `setup` ("setup needed: Windows uv") while `cmd.exe /c where uv` finds nothing; the
  pipeline never installs software on Windows (D8).
- Stellaris behaviour stays labelled UNVERIFIED (A-xx ids carried per gate) until M0 proves it.
- Every Python file passes `uv run ruff check` (line length 120) and `uv run mypy --strict <file>`.
- Owned paths: `tools/devdash/**`, `tools/game/**`, `tests/unit/devdash/**`, `.claude/**`, `.githooks/**` → T7;
  `docs/dev/**` and other docs → T8. The orchestrator commits after each task's review; implementers do not
  run git.

## Review Focus

1. **A torn or invalid line in an events file while the server tails it.** Expect: the line is skipped and
   counted (`invalid_lines`), the stream continues, and the next valid line still arrives. Test in Task 1 + Task 6.
2. **Two sessions start the server at once.** Expect: exactly one server; the second `ensure` finds it healthy and
   returns its URL. A bind failure on a port held by a healthy devdash is success, not an error. Test in Task 6.
3. **A save locked by OneDrive sync or the running game.** Expect: the input copy retries every 30 s up to 10
   times, then the file is left out and the gate skips "save locked", never `fail`. Test in Task 9.
4. **A verdict value that looks like a flag, has a newline, or names an unknown key.** Expect: rejected with 400
   before any process starts; valid values reach `sae m0 record` as one `--value K=V` argv item. Test in Task 6.
5. **A request from another local page or a rebound DNS name.** Expect: POST without the token → 403; any request
   whose `Host` is not `127.0.0.1:<port>` or `localhost:<port>` → 403. Test in Task 6.

---

## File map

| File | Responsibility | Task |
|---|---|---|
| `.gitattributes`, `.githooks/*` | LF endings so repo hooks run | 0 |
| `.gitignore` | add `.sae-dev/` | 1 |
| `tools/devdash/dd.py` | CLI entry: puts its dir on `sys.path`, calls `devdash.cli.main` | 1 |
| `tools/devdash/devdash/__init__.py` | package marker, `REPO` and `dev_dir()` | 1 |
| `tools/devdash/devdash/events.py` | event build/append/read/redact/rotate, enabled flag | 1 |
| `tools/devdash/devdash/cli.py` | subcommands `emit task enable disable serve ensure stop hook` | 1 (grown in 2, 6, 8) |
| `tools/devdash/devdash/tasks.py` + `docs/dev/tasks.json` | task file load/set, seed | 2 |
| `tools/game/gates.json` + `tools/game/gates.py` | gate registry data + typed loader | 3 |
| `tools/devdash/devdash/gateindex.py` | junit / m0 results → `.sae-dev/gates.json` | 4 |
| `tools/game/run_deferred.py` | after a run, update the gate index (best effort) | 4 |
| `tools/devdash/devdash/state.py` | snapshot derivation (pure) | 5 |
| `tools/devdash/devdash/server.py`, `ensure.py` | HTTP + SSE + verdict, single instance | 6 |
| `tools/devdash/static/{index.html,app.js,app.css}` | front end | 7 |
| `.claude/settings.json`, `tools/devdash/devdash/hooks.py`, `.claude/agents/*.md` | hooks, agent emit lines | 8 |
| `tools/game/pipeline.py`, `tests/game/test_saves.py` | pipeline; parse-cost property | 9 |
| docs, changelog, boundary test, TEAM_PLAN | delivery | 10 |

Tests live in `tests/unit/devdash/` (`conftest.py` inserts `tools/devdash` and `tools/game` into `sys.path`;
existing tests load `tools/game/*.py` with `importlib.util.spec_from_file_location`, keep that style for
`run_deferred`). mypy for tools: `MYPYPATH=tools/devdash:tools/game uv run mypy --strict tools/devdash tools/game/gates.py tools/game/pipeline.py`.

---

### Task 0: Make the repo hooks runnable (LF endings)

**Files:** Modify `.gitattributes` (exists: `* text=auto`); Modify `.githooks/pre-commit`, `.githooks/commit-msg` (line endings only).

- [ ] **Step 1:** Append to `.gitattributes`: `.githooks/* text eol=lf` and `*.sh text eol=lf`.
- [ ] **Step 2:** Convert both hooks to LF (`sed -i 's/\r$//'`), keep the executable bit.
- [ ] **Step 3:** Verify: `file .githooks/*` shows no "CRLF"; `bash -n` on both exits 0;
  `git -c core.hooksPath=.githooks hook run commit-msg -- <(printf 'fix: x\n')` exits 1 with "missing 'Reviewed-by" (proves the hook executes; `commit --dry-run` skips hooks).
- [ ] **Step 4:** Orchestrator commits: `fix(hooks): store .githooks with LF endings`.

### Task 1: Event log, enabled flag, `emit` CLI

**Files:** Create `tools/devdash/dd.py`, `tools/devdash/devdash/{__init__,events,cli}.py`,
`tests/unit/devdash/{__init__,conftest,test_events,test_cli_emit}.py`; Modify `.gitignore`.

**Interfaces — Produces:**
- `devdash.REPO: Path`; `devdash.dev_dir(repo: Path | None = None) -> Path` → `<repo>/.sae-dev` (honours env `SAE_DEVDASH_DIR` for tests).
- `events.KINDS = ("info","start","progress","pass","fail","skip","owner","done")`; `events.MSG_MAX = 200`; `events.ROTATE_BYTES = 5 * 1024 * 1024` (amended from the spec's 20 MB after plan review: memory budget).
- `events.make_event(source: str, kind: str, msg: str, *, ref: str | None = None, pct: int | None = None, session: str | None = None, now: datetime | None = None) -> dict[str, object]` — `{"v":1,"ts":<UTC RFC3339 with ms>,"source","kind","msg", +ref/pct/session when given}`; raises `ValueError` on unknown kind, pct outside 0..100, empty source; msg is redacted, newlines → spaces, cut to 200 chars.
- `events.redact(text: str, home: str | None = None) -> str` — home dir → `<HOME>`.
- `events.events_path(dev: Path, writer: str = "wsl") -> Path` — `events.jsonl` for `"wsl"`, `events.win.jsonl` for `"win"`.
- `events.append(event: Mapping[str, object], path: Path) -> None` — one `os.write` of one line with `O_APPEND|O_CREAT`. Writers never rotate.
- `events.rotate_if_needed(path: Path) -> Path | None` — called **only by the server** (single rotator): if size ≥ `ROTATE_BYTES`, rename to `events.<n>.jsonl` (`n` = next free integer) and return the new name.
- `events.read_events(paths: Sequence[Path], since_ts: str | None = None) -> tuple[list[dict[str, Any]], int]` — full read for tests/CLI: merged, sorted by `ts`, (events, invalid line count); missing files are fine.
- `events.Tail(path: Path, maxlen: int = 2000)` — incremental reader: `poll() -> tuple[list[dict[str, Any]], int]` returns only lines appended since the last poll (tracks inode + byte offset, holds back a trailing partial line until its newline arrives, restarts at 0 when the inode changes or the file shrinks); keeps the last `maxlen` events in a `deque` at `.events`. The server uses this, never `read_events`.
- `events.is_enabled(dev: Path) -> bool`; `events.emit(source, kind, msg, **kw) -> bool` — False (no write) when not enabled; never raises (catches `OSError`, `ValueError`).
- CLI (`uv run python3 tools/devdash/dd.py …` or `python3 tools/devdash/dd.py …`): `emit [--source S=orchestrator] [--kind K=info] [--ref ID] [--pct N] MSG`, `enable`, `disable`. `cli.main(argv: Sequence[str] | None = None) -> int`.

- [ ] **Step 1: Write failing tests** (`tmp_path` dev dir via `SAE_DEVDASH_DIR`):
  - `test_make_event_shape`: keys `v,ts,source,kind,msg`; `ref` absent when not given; `make_event("x","bogus","m")` raises.
  - `test_msg_redacted_and_cut`: `make_event("x","info", str(Path.home())+"/a\nb"+"z"*300)["msg"]` starts with `"<HOME>/a b"` and has length 200.
  - `test_append_read_roundtrip`: 3 appends → `read_events` returns 3 in ts order, invalid 0.
  - `test_torn_line_skipped`: write a valid line, then `'{"v":1,"ts"'` without newline, then (after newline) a valid line → 2 events, invalid 1.
  - `test_concurrent_append`: 8 processes × 200 appends via `multiprocessing` → 1600 valid lines, 0 invalid. Run it twice: in `tmp_path` and in a dir under the repo (`/mnt/e` drvfs, removed in teardown).
  - `test_rotation`: with `ROTATE_BYTES` monkeypatched to 200, `rotate_if_needed` past it creates `events.1.jsonl`; `append` alone never rotates.
  - `test_tail_incremental`: `Tail.poll()` returns 2 events, then after 1 more append returns only that 1; a partial line is held back until completed; after rotation (new inode) it reads the new file from 0; `len(tail.events) ≤ maxlen`.
  - `test_merge_wsl_and_win`: events in both files come back interleaved by `ts`.
  - `test_emit_noop_when_disabled`: `emit(...)` returns False and creates no file; after `cli.main(["enable"])` returns True.
  - `test_cli_emit`: `main(["emit","--kind","start","--ref","X3-G5","hello"])` → 0 and the line has `ref == "X3-G5"`; `--pct 101` → exit 2.
- [ ] **Step 2:** Run `uv run pytest tests/unit/devdash -q` → FAIL (module missing).
- [ ] **Step 3:** Implement the interfaces above; add `.sae-dev/` to `.gitignore`.
- [ ] **Step 4:** Run tests → PASS; `uv run ruff check tools/devdash tests/unit/devdash`; `MYPYPATH=tools/devdash uv run mypy --strict tools/devdash` → clean.

### Task 2: Task board file

**Files:** Create `tools/devdash/devdash/tasks.py`, `docs/dev/tasks.json`, `tests/unit/devdash/test_tasks.py`; Modify `cli.py`.

**Interfaces — Produces:**
- `tasks.STATUSES = ("done","in_progress","pending","blocked")`; `tasks.tasks_file(repo: Path) -> Path` → `docs/dev/tasks.json`.
- `tasks.load_tasks(path: Path) -> list[dict[str, Any]]` — `[]` if missing; `ValueError` naming the file on bad JSON or bad status.
- `tasks.set_task(path: Path, task_id: str, *, status: str | None = None, title: str | None = None, note: str | None = None, tag: str | None = None, link: str | None = None, blocked_on: str | None = None, now: datetime | None = None) -> dict[str, Any]` — create (title and status required) or update; sets `updated`; atomic write (tmp + replace), sorted by id, 2-space indent, trailing newline.
- CLI: `task ID [--status S] [--title T] [--note N] [--tag G] [--link L] [--blocked-on B]` → writes the file and emits `info` `"task ID → S"` with `ref=ID`.

- [ ] **Step 1: Failing tests:** `test_create_requires_title_and_status`; `test_update_keeps_other_fields`; `test_bad_status_rejected`; `test_file_is_stable` (writing the same content twice is byte-identical apart from `updated`); `test_seed_file_valid` (`load_tasks` on the committed `docs/dev/tasks.json` succeeds, ids unique, no id matches `^(X\d-G\d+|M0-\d+|R-\d)$` — gates never live here, §6.4).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement; seed `docs/dev/tasks.json` with: done — `sae-mvp` (PR #1), `x4-scaffold` (PR #2), `x3-harness` (PR #3, merged d011f78); in_progress — `devdash` (this plan); pending — `x2-field-kit` (PR #4), `arch-review-x2`, `rust-coverage-ci`, `game-marker-stderr`, `ledger-is-live`; blocked — `v1-calibration` (blocked_on "5 labelled real campaigns"), `x4-g0-live-campaigns` (blocked_on "later milestone"), plus V-2..V-5 from `docs/TEAM_PLAN.md` "Validation backlog" as pending/blocked per their "Needs" column.
- [ ] **Step 4:** Tests PASS, ruff, mypy clean.

### Task 3: Gate registry

**Files:** Create `tools/game/gates.json`, `tools/game/gates.py`, `tests/unit/devdash/test_gates.py`.

**Interfaces — Produces:**
- `gates.json`: list of objects, in pipeline order. Fields: `id`, `stage` (`x3|x4|m0|x2|r`), `title`,
  `runner` (`{"kind":"collect"}` | `{"kind":"pytest","select":"<-k expr>"}` | `{"kind":"m0","auto":bool}` | `{"kind":"bench","args":[...]}` | `{"kind":"none"}`),
  `inputs` (SAE_GAME_* names), `owner_play` (bool), `owner_instruction` (str, one line), `judged` (bool),
  `question` (str), `evidence` (`[{"key","label"}]`), `assumptions` (`["A-06",…]`),
  `hardware` (`one_pc|second_machine|laptop_4core`), `windows_only` (bool), `pending_source` (str|null),
  `priority` (`critical|core|stretch|later|backlog`, pipeline ordering only, never a skip reason except `backlog`),
  `milestone_later` (bool, true only for X4-G0: "later milestone" skip),
  `deferred_id` (str|null: the `id=` on the game marker; pytest gates are split from one junit by this, never selected with `-k`).
- `m0` runner fields: `{"kind":"m0","auto":bool,"pre":[argv templates],"args":[argv templates]}`. Templates may use
  `{user_dir}`, `{save}` (newest copied autosave), `{starnet_dir}`, `{parser_exe}`. Pinned from `src/sae/m0/cli.py`:
  M0-1 `args ["--starnet-dir","{starnet_dir}"]`; M0-2 `pre [["stage","M0-2"]]`; M0-4 `auto:false` (judged via
  `sae m0 tail`: `pre [["tail","--seconds","300"]]`); M0-6 `pre [["stage","M0-6","--sid","{sid}","--m","{m}"]]` with
  `{sid}`/`{m}` asked in the owner instruction as evidence (it is owner-judged staging; the pipeline only runs it after the
  verdict form supplies them — mark this step `owner_play` with evidence keys `sid`, `m`); M0-11 and M0-14 `args ["--save","{save}"]`
  (M0-14: the Ironman save the owner names in evidence `ironman_save`). Every `sae m0` call is prefixed
  `["uv","run","sae","m0","--user-dir","{user_dir}"]` (+ `"--parser-exe","{parser_exe}"` when known) so WSL never
  falls back to `~/.local/share` (`src/sae/adapters/paths.py:65`).
- `gates.Gate` frozen dataclass with those fields; `gates.load_gates(path: Path | None = None) -> list[Gate]`
  (validates every field, unique ids, `ValueError` naming the id); `gates.by_id(gs: Sequence[Gate]) -> dict[str, Gate]`.
- Content: X3-G0 (`collect`), X3-G1..G11 (`pytest`, select `"X3-G<n>"`-matching test names — use the test
  functions carrying `id="X3-Gn"`), X4-G1..G3 (`pytest`), X4-G0 (`none`, `priority:"later"`),
  M0-1..M0-14 (`m0`; `auto` true for the ids `sae m0 check` accepts — `CHECKS` with `.auto` in `src/sae/m0/checks.py` —
  except M0-4; `judged` true for every M0 id `sae m0 record` accepts; `evidence`, `question`, `owner_instruction`,
  `assumptions` from the row in `docs/m0_checklist.md`; `priority` from the runbook (`docs/handoff/GAME_PC_AGENT.md` §5:
  M0-2,3,5,6,11 critical; other core = core; stretch = stretch; checks.py tier `later` = later; M0-10 backlog)),
  X2-G1..G3 (`pending_source:"PR #4"`, runner `none`), R-1..R-6 (from `bench/README.md`; R-1 and R-6 non-`one_pc`;
  R-2 and R-4 runner `none` — R-4 uses `balloon.py` and is never automatic; R-3 and R-5 `judged` with `sae m0 record`
  evidence and runner `none` + owner instruction; R-5 `windows_only`; X3-G4 `windows_only`).

- [ ] **Step 1: Failing tests:**
  - `test_registry_loads`: ≥ 45 gates, ids unique.
  - `test_markers_in_registry`: every `id="…"` in a `pytest.mark.game(` call under `tests/` (AST scan) is a registry id with `runner.kind == "pytest"`.
  - `test_registry_pytest_gates_have_markers`: every registry `pytest` gate without `pending_source` has a marker.
  - `test_m0_rows_in_registry`: every `M0-\d+` id in a `docs/m0_checklist.md` table row is a registry id with `stage == "m0"`.
  - `test_judged_ids_recordable`: every `judged` id is in the `choices` of `sae m0 record` (build the parser from `sae.m0.cli` and read the choices, or import `BY_ID`).
  - `test_bad_field_rejected`: a temp registry with `hardware:"mars"` raises `ValueError` mentioning the id.
  - `test_m0_args_match_cli`: for every `m0` gate, `["m0","--user-dir","/x", *pre_or_check_argv]` with templates filled by dummy values parses with the real `sae m0` argparse parser (build it from `sae.m0.cli`) without `SystemExit`.
  - `test_r_scope`: R-3 and R-5 have `priority != "backlog"` and `milestone_later` false; only X4-G0 has `milestone_later` true; R-2, R-4 runner `none`.
- [ ] **Step 2:** FAIL. **Step 3:** Write `gates.json` and `gates.py`. **Step 4:** PASS, ruff, mypy.

### Task 4: Gate index from results

**Files:** Create `tools/devdash/devdash/gateindex.py`, `tests/unit/devdash/test_gateindex.py`; Modify `tools/game/run_deferred.py` (end of `main`).

**Interfaces — Consumes:** `sae.m0.results.load`, `status_of`. **Produces:**
- `GateResult = TypedDict("GateResult", {"status": str, "evidence": dict[str, str], "source": str, "run": str, "at": str})`; status ∈ `pass|fail|skip|pending`.
- `gateindex.from_junit(junit: Path, run: str, at: str) -> dict[str, GateResult]` — testcases keyed by the `deferred_id` property; a gate with several testcases: any fail → fail, else any pass → pass, else skip; evidence = other `<property>`s plus the skip/failure message (≤ 500 chars).
- `gateindex.from_m0(results: Path) -> dict[str, GateResult]` — every `<ID>.json` except `env.json` and `*.stage.json`; `at` = the newer of `manual.at`/`auto.at`; evidence = manual values + note, else auto evidence (stringified).
- `gateindex.merge_newest(old: Mapping[str, GateResult], new: Mapping[str, GateResult]) -> dict[str, GateResult]` — keep the entry with the larger `at`, **except** a `skip` never replaces a `pass` or `fail` (a run without inputs must not wipe a real result).
- `m0_results/` is NOT stored in the index: `state.snapshot` reads it directly via `from_m0` (mtime-cached), so manual `sae m0 record` runs always show (D9).
- `gateindex.update_index(dev: Path, new: Mapping[str, GateResult]) -> dict[str, GateResult]` — reads/writes `<dev>/gates.json` atomically.
- `run_deferred.main` end: if `tools/devdash` imports, `update_index(dev_dir(), from_junit(junit, str(out), now))`; any exception prints one warning to stderr and does not change the exit code.

- [ ] **Step 1: Failing tests** with fixture junit XML (three testcases: X3-G1 pass with property `A-20`, X3-G2 two cases one failed, X3-G4 skipped with message) and a fixture `m0_results/` (M0-5 manual pass with values, M0-3 auto fail, `env.json`, `M0-6.stage.json`):
  `test_junit_statuses` (G1 pass, G2 fail, G4 skip with reason in evidence); `test_m0_statuses` (manual wins; env/stage files ignored);
  `test_merge_newest_keeps_newer`; `test_newer_skip_keeps_older_pass`; `test_update_index_persists`; `test_run_deferred_updates_index` (call `run_deferred.main` with a fake runner that writes the fixture junit; `SAE_DEVDASH_DIR=tmp`; index contains X3-G1); `test_run_deferred_index_failure_harmless` (index dir unwritable → same exit code, warning on stderr).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS; existing `tests/unit/t7/test_run_deferred.py` still passes.

### Task 5: Snapshot derivation

**Files:** Create `tools/devdash/devdash/state.py`, `tests/unit/devdash/test_state.py`, fixtures under `tests/unit/devdash/fixtures/`.

**Interfaces — Consumes:** Tasks 1–4. **Produces** `state.snapshot(repo: Path, dev: Path, *, now: datetime, gh: Callable[[list[str]], str | None] | None = None) -> dict[str, Any]` returning exactly:

```
{"generated": ts, "branch": str, "enabled": bool, "invalid_lines": int,
 "env": [{"k","v","tone"}],                       # from <dev>/env.json (pipeline preflight); [] if absent
 "owner": null | {"ref","msg","judged","question","evidence":[{"key","label"}],"since"},
 "kpis": [{"label","value","sub","chip","tone"}],  # Done, In progress, Pending, Blocked, Gates passed, CI
 "running": [{"key","title","source","step","pct","started","last","stale"}],
 "feed": [{"ts","source","kind","ref","msg"}],     # newest first, ≤ 200
 "tasks": {"done":[…],"in_progress":[…],"pending":[…],"blocked":[…]},
 "prs": [{"number","title","isDraft","checks"}],   # [] when gh is None or fails
 "gates": [{"stage","name","passed","total","items":[{"id","title","status","detail","cmd","when","judged","question","evidence","unverified"}]}],
 "results": {"source": str|null, "budget_s": 3.0, "parse": [{"label","wall_s"}], "belief": [{"date","p":{archetype: float}}],
             "commitments": [{"date","strategy","reason"}], "impact": [{"label","value","pct"}]},
 "ci": null | {"status","url"}}
```

Rules: `tone` ∈ `ok|info|warn|bad|muted`. Gate `status` ∈ `pass|fail|run|owner|skip|todo|setup|hardware|later|waiting`, decided in this order: open `owner` event with `ref == id` → `owner`; open, non-stale `start` event with `ref == id` → `run`; result (gate index for junit gates, `from_m0(repo/"m0_results")` for M0/R gates; `pending` → `todo`) → its status; `hardware != one_pc` → `hardware`; `milestone_later` → `later`; `pending_source` → `waiting`; `windows_only` and env `windows_uv == false` → `setup`; else `todo`. Running items: key = `ref` or else `source`; open from `start` until `done|pass|fail|skip` with the same key; `stale` after 30 min with no event for the key (gate `run` too); `title` = the start msg, `step` = latest msg. Agents: hooks key both start and stop as `ref = "agent:<subagent_type>"`; a `done` with that ref closes the **oldest** open item with that key. Owner: newest `owner` event with no later `done|pass|fail` for its ref. Results: newest `<dev>/pipeline/*/results.json`; belief and commitments from its `run_dir`'s `bus.jsonl` (`type == "belief_update"` and `"strategy_selected"`, same fields as `tools/console/views.sql` `v_belief`/`v_decisions`). Missing inputs give empty lists, never invented numbers.

- [ ] **Step 1: Failing tests:** `test_empty_repo_snapshot` (no dev dir → all lists empty, `owner` null, KPIs show `"0"`/`"—"`); `test_running_and_stale`; `test_gate_status_precedence` (one case per rule above, including `start` then `owner` for one ref → `owner`); `test_agent_start_closed_by_stop` (two `start` with `agent:sae-scout`, one `done` → one still running); `test_manual_m0_record_shows` (write `m0_results/M0-5.json` manual pass with no index → gate `pass`); `test_owner_banner_clears_on_done`; `test_feed_newest_first_capped`; `test_results_from_pipeline_run` (fixture results.json + bus.jsonl); `test_never_opens_god_bus` (put an unreadable `bus.god.jsonl` beside `bus.jsonl` and monkeypatch `Path.open`/`builtins.open` to fail on any path ending `bus.god.jsonl`); `test_gh_failure_is_empty` (gh returns None → `prs == []`, `ci is None`).
- [ ] **Step 2:** FAIL. **Step 3:** Implement as pure functions; recompute only inputs whose mtime changed (cache keyed by path → mtime). **Step 4:** PASS, ruff, mypy.

### Task 6: Server, SSE, verdict endpoint, single instance

**Files:** Create `tools/devdash/devdash/{server,ensure}.py`, `tests/unit/devdash/{test_server,test_ensure}.py`; Modify `cli.py` (`serve`, `ensure`, `stop`). Security-sensitive: Opus review.

**Interfaces — Consumes:** `state.snapshot`, `events.*`, `gates.load_gates`. **Produces:**
- `server.make_server(repo: Path, dev: Path, port: int, *, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run, now: Callable[[], datetime] = …) -> ThreadingHTTPServer`; `server.serve_forever_with_idle(srv, idle_hours: float = 12.0) -> None`.
- Token: on start write 32 hex chars (`secrets.token_hex(16)`) to `<dev>/token`; `index.html` placeholder `__SAE_TOKEN__` replaced when served. `<dev>/server.json` = `{"pid","port","started"}`.
- The server runs under the repo venv interpreter (it imports `sae.m0.results`); `ensure` spawns `<repo>/.venv/bin/python` (fallback `uv run python3`), never the hook's bare `python3`. Each poll it calls `events.rotate_if_needed` (sole rotator) and `Tail.poll()`; snapshot inputs are mtime-cached.
- Endpoints: `GET /` and `/static/*` (from `tools/devdash/static/`, path traversal rejected); `GET /api/health` → `{"ok":true,"pid"}`; `GET /api/state` → snapshot; `GET /api/events?since=<ts>` → events after `ts` from the tail (≤ 2000); `GET /api/stream` → SSE, first message `event: state` with the snapshot, then `event: event` per new event line and a fresh `state` at most every 2 s when inputs change; `: ping` every 15 s. `POST /api/owner/<ref>/done` → `ref` must be a registry id (else 400); appends `{"source":"owner","kind":"done","ref":ref,"msg":"marked done"}`. `POST /api/owner/<ref>/verdict` body `{"verdict":"pass|fail","values":{key:value},"note":str}`.
- Every request: `Host` must equal `127.0.0.1:<port>` or `localhost:<port>` else 403. Every POST: header `X-SAE-Token` must equal the token else 403; body ≤ 16 KB, JSON object.
- Verdict validation (all before any process starts, else 400 with a reason): `ref` is a registry gate with `judged`; verdict in `pass|fail`; keys ⊆ the gate's `evidence` keys; each value str, 1–200 chars, `str.isprintable()`; note ≤ 500, `isprintable()`. Then `runner(["uv","run","sae","m0","--user-dir",<env user_dir>,"record",ref,verdict, "--value=k1=v1", "--value=k2=v2", …, "--note=<note>"], cwd=repo, capture_output=True, text=True, timeout=60)` (each value and the note is one `--opt=…` argv item, so a leading `-` can't be read as a flag); verdict submissions are serialized with a `threading.Lock`; exit 0 → 200 `{"ok":true}` and emit `pass`/`fail` (kind = the verdict) with `ref` so the owner banner clears; else 502 `{"ok":false,"stderr":<last 2000 chars>}`.
- `ensure.ensure_running(repo: Path, dev: Path, port: int) -> str` — URL; if `server.json` pid alive and `/api/health` answers within 0.5 s → return; else spawn `[sys.executable, "tools/devdash/dd.py", "serve", "--port", port]` with `start_new_session=True`, stdout/stderr → `<dev>/server.log`, wait ≤ 3 s for health. A port in use by something that is not a healthy devdash → raise `RuntimeError("port <n> in use")`. `ensure.stop(dev: Path) -> bool`.

- [ ] **Step 1: Failing tests** (server on port 0 in a thread, `http.client`):
  `test_health_and_state`; `test_static_traversal_403_or_404` (`/static/../../pyproject.toml` not served);
  `test_bad_host_403`; `test_post_without_token_403`; `test_mark_done_appends_event`;
  `test_verdict_rejects` (parametrized: unknown ref, non-judged ref, verdict "skip", unknown key, value `"a\nb"`, value of 201 chars, note with `\x00`) — each 400 and the fake runner never called;
  `test_verdict_flaglike_value_is_one_argv_item` (value `"--note=x"` → argv contains `"--value=fleet=--note=x"` exactly; note `"-x"` → `"--note=-x"`; and that argv parses with the real `sae m0` parser to `values == {"fleet": "--note=x"}`);
  `test_verdict_emits_closing_event` (success → a `pass` event with the ref);
  `test_events_since`;
  (every test that spawns a server kills it in teardown);
  `test_verdict_runner_failure_502` (fake returns code 2, stderr "boom" → 502 with "boom");
  `test_sse_first_message_is_state` and `test_sse_survives_torn_line` (append a torn line then a valid one → client receives the valid event);
  `test_ensure_single_instance` (two `ensure_running` calls → same pid; `server.json` pid unchanged);
  `test_ensure_foreign_port_raises` (bind a plain socket on the port first).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, ruff, mypy.

### Task 7: Front end (Opus implementer — frontend routing rule)

**Files:** Create `tools/devdash/static/{index.html,app.js,app.css}`; Test `tests/unit/devdash/test_static.py`.

**Interfaces — Consumes:** the snapshot shape in Task 5 and endpoints in Task 6.

- [ ] **Step 1: Failing tests:** `test_no_innerhtml_with_data` (app.js contains no `innerHTML`, `outerHTML`, `insertAdjacentHTML` or `document.write`: all data is rendered with `textContent`/`createElement`, since messages carry agent text and game-log names); `test_static_no_external_urls` (no `http://`/`https://` in the three files, so no CDN/fonts fetched);
  `test_token_placeholder_present` (`__SAE_TOKEN__` once in index.html); `test_every_snapshot_key_used` (each top-level snapshot key appears in app.js).
- [ ] **Step 2:** FAIL. **Step 3:** Port the mockup's look and layout (sidebar views + environment, top bar with branch/view/Live/port and **Copy pipeline command**, owner banner, KPI strip, Overview/Tasks/Game gates/Results) to vanilla JS rendering from `/api/state`, live via `EventSource('/api/stream')` with reconnect and a "Disconnected" pill. Gate detail and owner banner show the **verdict form** for `judged` gates: Pass / Fail buttons, one labelled input per `evidence` item, optional note, inline error from the 400/502 body, `X-SAE-Token` header from the embedded token. Empty states say "No data yet" / "Not run". Statuses carry text, not only colour. System font stack (`Inter, ui-sans-serif, system-ui…`; mono `JetBrains Mono, ui-monospace…`), dark default plus `prefers-color-scheme: light`. Sidebar stacks below 720 px. Remember the last view in `localStorage` inside try/catch.
- [ ] **Step 4:** PASS. Then the orchestrator runs the server against the repo and checks each view in a browser (screenshot) before the task review.

### Task 8: Claude Code hooks and agent emit lines

**Files:** Create `.claude/settings.json`, `tools/devdash/devdash/hooks.py`, `tests/unit/devdash/test_hooks.py`; Modify `cli.py` (`hook` subcommand), `.claude/agents/*.md` (one paragraph each).

**Interfaces — Produces:** `hooks.handle(event: str, payload: Mapping[str, Any], dev: Path, repo: Path) -> str` — returns text for stdout. Events: `session-start` → `ensure_running` then emit `info` "session started" and return `"SAE dashboard: http://127.0.0.1:<port>"`; `pre-tool` → for `tool_name in ("Agent","Task")` emit `start` (`source` = `tool_input.subagent_type` or "agent", `ref` = `agent:<subagent_type or "agent">`, msg = description); for `"Bash"` emit `info` with `tool_input.description` (skip when absent); `subagent-stop` → emit `done` with `ref = agent:<payload agent_type or "agent">` (field name UNVERIFIED: read `agent_type`, fall back to `subagent_type`, else "agent"), msg "agent finished".
The hook commands test the flag in the shell first so Python never starts when disabled:
`[ -f "$CLAUDE_PROJECT_DIR/.sae-dev/enabled" ] && python3 "$CLAUDE_PROJECT_DIR/tools/devdash/dd.py" hook <event> || true`.
`hooks.py` and everything it imports are stdlib-only; `session-start` reaches `ensure` without importing `server`/`gateindex`. `cli hook <event>` reads JSON from stdin, calls `handle`, prints, always exits 0, total time ≤ 100 ms except `session-start` (≤ 3 s, spawn is detached).

`.claude/settings.json`:
```json
{"hooks": {
  "SessionStart": [{"hooks": [{"type": "command", "command": "[ -f \"$CLAUDE_PROJECT_DIR/.sae-dev/enabled\" ] && python3 \"$CLAUDE_PROJECT_DIR/tools/devdash/dd.py\" hook session-start || true", "timeout": 5}]}],
  "PreToolUse": [{"matcher": "Agent|Task|Bash", "hooks": [{"type": "command", "command": "[ -f \"$CLAUDE_PROJECT_DIR/.sae-dev/enabled\" ] && python3 \"$CLAUDE_PROJECT_DIR/tools/devdash/dd.py\" hook pre-tool || true", "timeout": 2}]}],
  "SubagentStop": [{"hooks": [{"type": "command", "command": "[ -f \"$CLAUDE_PROJECT_DIR/.sae-dev/enabled\" ] && python3 \"$CLAUDE_PROJECT_DIR/tools/devdash/dd.py\" hook subagent-stop || true", "timeout": 2}]}]}}
```
Agent definitions: add "When `.sae-dev/enabled` exists, report progress with `python3 tools/devdash/dd.py emit --source <your agent name> --kind start|progress|done --ref <work order id> "<one line>"` at start, at each milestone and at the end."

- [ ] **Step 1: Failing tests:** `test_disabled_is_silent` (no `enabled` → no file written, empty output, exit 0); `test_bash_logs_description_not_command` (payload command contains `SECRET=abc` → event msg is the description, `"SECRET"` absent from the file); `test_agent_start_and_stop`; `test_bad_stdin_exit_0`; `test_hook_imports_stdlib_only` (run `python3 -I -X importtime tools/devdash/dd.py hook pre-tool` and `… hook subagent-stop` with an **enabled** dev dir and a Bash payload; no `pydantic`/`sae` in the import list); `test_settings_json_valid` (parses; three hook events; commands reference `dd.py hook`).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS; `time python3 tools/devdash/dd.py hook pre-tool < payload.json` real < 0.1 s.

### Task 9: Game-test pipeline

**Files:** Create `tools/game/pipeline.py`, `tests/unit/devdash/test_pipeline.py`; Modify `tests/game/test_saves.py`
(`test_parse_cost_budget` calls `record_property("parse_costs", json.dumps([{"save": name, "wall_s": …, "rss_bytes": …}, …]))`
**before** its asserts, so the evidence exists exactly when the budget fails), `tests/game/test_recording.py` (record
`record_property("run_dir", <replay run dir>)` where the replay is built, so Results can find the legal bus).

**Interfaces — Consumes:** `gates.load_gates`, `events.emit`, `gateindex.*`. **Produces:**
- `pipeline.main(argv: Sequence[str] | None = None, *, runner=subprocess.run, sleep=time.sleep, now=…) -> int`.
  Args: `--stages x3,x4,m0,x2,r` (default all), `--from ID`, `--out DIR` (default `.sae-dev/pipeline/<YYYYmmdd-HHMMSS>`; with `--from` and no `--out`, the newest existing run dir), `--rerun`, `--launch`, `--dry-run`, `--max-saves N` (default 13).
- `pipeline.preflight(runner, *, roots: Sequence[Path] = (Path("/mnt/c"), Path("/mnt/d"), Path("/mnt/e"))) -> dict[str, Any]` — keys `install_dir`, `user_dir`, `onedrive` (bool), `parser` (path or None: `SAE_PARSER_EXE`, else `parser/target/release/sae-parse`), `starnet_dir` (Workshop content folder if found, else None), `game_running`, `windows_uv`, `mod_staged`, `mod_stale` (staged copy's file hashes differ from `mod/sae_bridge/`).
  - Install dir: Steam path from `reg.exe query "HKCU\Software\Valve\Steam" /v SteamPath` (convert `X:/…` → `/mnt/x/…`), else known roots `<root>/Steam`, `<root>/Program Files (x86)/Steam`, `<root>/SteamLibrary`; read `steamapps/libraryfolders.vdf` there, convert each library's Windows path to `/mnt/x/…`, pick `<lib>/steamapps/common/Stellaris`. No recursive globbing.
  - User dir: `/mnt/c/Users/*/OneDrive/Documents/Paradox Interactive/Stellaris`, then `/mnt/c/Users/*/Documents/…`.
  - `game_running`: `tasklist.exe /FI "IMAGENAME eq stellaris.exe" /NH` stdout contains `stellaris.exe` (case-insensitive) — exit code is 0 either way.
  - `windows_uv`: `cmd.exe /c where uv` exit 0.
  - Writes `<dev>/env.json` (the `env` list of Task 5 plus the raw keys) and emits `info` "preflight: …".
- `pipeline.copy_inputs(pf: dict, out: Path, max_saves: int, *, sleep) -> dict[str, str]` — copies the newest `max_saves` autosaves of the newest campaign folder under `<user_dir>/save games/` and `<user_dir>/logs/game.log` into `<out>/private/inputs/` with `shutil.copy2`; on `PermissionError`/`OSError(EBUSY)` retry every 30 s up to 10 times (this is the "save locked" handling: OneDrive or the game holding the file); returns the env vars to set: `SAE_GAME_SAVES_DIR`, `SAE_GAME_LOG`, `SAE_GAME_MAX_SAVES`, `SAE_PARSER_EXE` (when known). A file still locked after 10 tries is left out and reported in an `info` event; if no save could be copied, the stage's gates skip "save locked".
- `pipeline.plan(gates: Sequence[Gate], stages: set[str], start: str | None) -> list[Gate]` — registry order; M0 sorted `critical → core → stretch → later`; `backlog` dropped.
- **X3/X4 stage (one pytest run per stage, never per gate):** after `copy_inputs`, `uv run python3 tools/game/run_deferred.py --out <out>/<stage>` with the copied-input env vars; one `start` event per gate first; then split the junit with `gateindex.from_junit` by `deferred_id` and emit one `pass|fail|skip` per gate (missing from junit → skip "not collected"). X3-G0 (`collect`): pass when the Task 3 AST scan finds a marker for every X3-G1..G11 and `run_deferred.py --list` exits 0.
- **Per M0 / R gate, in order**, emitting `start` then exactly one of `pass|fail|skip` (`owner` in between when waiting), `ref` = gate id:
  - skips first: `hardware != one_pc` → "needs other hardware"; `milestone_later` → "later milestone"; `pending_source` and no marker → "waiting on <pending_source>"; `windows_only` and not `windows_uv` → "setup needed: Windows uv (install: winget install astral-sh.uv)"; runner `none` and not `judged` → "manual procedure: see bench/README.md".
  - order for an M0 gate: (1) if `owner_play`: emit `owner` with `owner_instruction` and wait for a `done` event for the ref (Mark done); (2) run each `pre` argv; (3) if `auto`: `sae m0 … check <id> <args>` (templates filled from preflight and `copy_inputs`; re-copy inputs first if the owner just played); (4) if `judged`: emit `owner` "record your verdict on the dashboard" and wait for a `manual` entry in `m0_results/<id>.json` with `at` newer than the step start; final status = `results.status_of`.
  - every `sae m0` argv starts `["uv","run","sae","m0","--user-dir",<user_dir>]` (+ `--parser-exe` when known).
  - waits are a `sleep(10)` loop with no timeout; while waiting, emit `stellaris` events when the newest autosave's mtime changes ("autosave written: <name>") or `game_running` flips ("game started"/"game exited"; check every 60 s). These are the spec §6.2 watchers.
- `--launch` (owner-authorized 2026-10-28), before the first `owner_play` step when the game is not running: run `sae m0 --user-dir <user_dir> stage M0-2` (reuses the repo's staging, relative `path="mod/sae_bridge"`), copy the staged mod folder and `.mod` file it reports into `<user_dir>/mod/` (report `mod_stale` first; overwrite only with `--launch`), then `cmd.exe /c start "" steam://rungameid/281990`. Nothing else touches the game process: no priority, affinity or kill.
- `<out>/state.json` `{"gates": {id: status}, "started", "updated"}` after every step; passed gates are skipped on resume unless `--rerun`. Ctrl-C → emit `skip` "interrupted" for the current gate (closes its `run`/`owner` state), save state, exit 130.
- `<out>/results.json` `{"parse":[{"label","wall_s","rss_bytes"}],"budget_s":3.0,"impact":[…],"run_dir":str|null}` from the `parse_costs` and `run_dir` junit properties and bench outputs.

- [ ] **Step 1: Failing tests** (fake `runner` records argv and returns scripted results; fake `sleep`; tmp dev/out dirs; a fake `/mnt/*` tree under tmp roots; a temp registry):
  `test_dry_run_prints_plan_runs_nothing`; `test_plan_order_and_backlog_dropped`; `test_skip_reasons` (hardware, milestone_later, waiting, setup, manual procedure);
  `test_x3_stage_single_pytest_run` (exactly one `run_deferred.py` call per stage; gates split from one fixture junit; env carries `SAE_GAME_SAVES_DIR` pointing inside `<out>/private/inputs`);
  `test_copy_inputs_retries_locked` (copy raises `PermissionError` 3 times then succeeds → 3 sleeps of 30; 10 failures → file left out, info event);
  `test_m0_order_owner_pre_check_verdict` (M0-11: owner wait → check argv has `--user-dir` and `--save <copied save>` → verdict wait; final status from `m0_results`);
  `test_owner_wait_resumes_on_done_event`; `test_resume_skips_passed`; `test_ctrl_c_emits_interrupted`;
  `test_launch_uses_m0_stage_and_steam` (argv includes `stage M0-2`, the `steam://rungameid/281990` start, and no `taskkill`/`wmic`/`start /low|/high|/affinity`);
  `test_preflight_finds_install` (fake `reg.exe` output `D:/Steam` → `install_dir` under `<root d>/Steam/steamapps/common/Stellaris`; fallback to known roots when `reg.exe` fails);
  `test_tasklist_no_match_is_not_running` (exit 0, "INFO: No tasks are running…" → False);
  `test_results_json_from_parse_costs`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, ruff, mypy; `uv run python3 tools/game/pipeline.py --dry-run` on this PC prints a plan and a preflight with `install_dir` under `/mnt/d/Steam`.

### Task 10: Boundary, docs, ownership

**Files:** Modify `tests/unit/t4/test_boundary.py` (or add `tests/unit/devdash/test_boundary.py`), `docs/developer_guide.md`, `docs/handoff/GAME_PC_AGENT.md`, `changelog.d/T7.md`, `docs/TEAM_PLAN.md` (architect: T7 owns `tools/devdash/**`, `.claude/**`, `.githooks/**`; T8 owns `docs/dev/**`); Create `tools/devdash/README.md`.

- [ ] **Step 1: Failing test** `test_src_never_imports_devdash`: no file under `src/sae` contains `import devdash`, `from devdash`, `tools.devdash` or `tools/devdash`.
- [ ] **Step 2:** FAIL only if violated (expected PASS: it guards the future); keep it.
- [ ] **Step 3:** Docs: README (enable, URL, commands, privacy), developer guide section, runbook pointer to `pipeline.py` (and the WSL note), changelog fragment in the file's existing format.
- [ ] **Step 4:** Full gates: `uv run pytest -x -q`, `uv run mypy --strict src/sae`, `MYPYPATH=tools/devdash:tools/game uv run mypy --strict tools/devdash tools/game/gates.py tools/game/pipeline.py`, `uv run ruff check` — all clean.
- [ ] **Step 5:** Opus wave review (four ON TARGET / AT RISK / OFF TARGET axes), address findings, then PR.

## Execution notes for the orchestrator

- Branch `feat/devdash` from `main`. Tasks 1→2→3→4→5→6 are sequential (interfaces). 7, 8 and 9 can run in
  parallel after 6 (disjoint files). 0 and 10 bracket the wave.
- Implementers: Haiku xhigh for 0–6, 8, 9, 10; Opus for 7 (frontend) and for review of 6 and 8 (security).
- Each task: implementer → Opus task review → orchestrator commits with `Reviewed-by: sae-reviewer/Opus 5.5 (<task>)`.
- Plan review (Opus, 2026-10-28): accept with changes; all 5 blockers, 10 majors and the minors are folded into
  the tasks above (agent keying, owner-before-run precedence, m0 argv templates + `--user-dir`, input copying,
  one pytest run per stage, `sae m0 stage M0-2` reuse, venv server, incremental tail + single rotator, skip-never-
  overwrites-pass, direct `m0_results/` read, R scope, parse_costs/run_dir properties, textContent-only UI).
- Deviation from spec, recorded: the CLI is `tools/devdash/dd.py emit …` rather than a bare `tools/devdash/emit`
  script (one entry point, works without `uv`). X4-G1..G3 are in the registry (they carry game markers). The
  daemon start for R-5 (D11) is deferred until Windows `uv` exists, since R-5 is `setup` until then.
