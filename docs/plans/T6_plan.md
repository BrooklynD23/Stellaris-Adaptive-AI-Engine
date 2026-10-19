# T6 plan — Telemetry and dev console

Owner: T6 lead. Paths: `src/sae/bus/**`, `src/sae/telemetry/**`, `src/sae/control/**`, `tools/console/**`,
tests in `tests/unit/t6/**`. Contracts: C5 (primary), C9 (primary), C8 (§2 `telemetry`, §5 CLI, §6 run dir, §7
deps), C7 (tiers, health). Honours proposals 005 (`ControlPayload.command` may be null) and 007 (ints bounded,
C5 `seq` unbounded).

## 0. What T4 already does (do not duplicate)

- `wiring.default_deps` calls `sae.bus.open_sink(run_dir, run_id, config, resume=resume)` and
  `sae.control.open_inbox(run_dir)`. Ports (`sae.daemon.ports`): `BusSink` = `emit(EventDraft)`, `flush()`,
  `close()` (idempotent), `write_god(GodObservation)` (must not raise); `ControlInbox` = `poll() ->
  Sequence[InboxItem]` (each item at most once until `complete`), `complete(item, ControlResult)`.
- T4 builds every `health` payload with state/level already stamped, emits `control` events itself (incl.
  `command=None` for rejected files) and `not_running`. T4 writes `runs/CURRENT` (`<run_id> <pid>`).
- So T6 = file I/O + readers + `sae ctl` + the telemetry process. No engine logic.

## 1. Work units (sequential: concurrency cap 1)

### U1 Bus writer + reader + god compaction — `src/sae/bus/{__init__,writer,reader,compact}.py`, `tests/unit/t6/test_bus_*.py`

`open_sink(run_dir, run_id, config, *, resume=False, wall=None, mono=None) -> BusWriter`.
- Two files, each with its own gapless `seq` and its own `run_started` first line (C5 §1, §4 god example).
  `RunStartedPayload` from `sae.__version__`, `CONTRACT_VERSIONS`, `config.adapter`, `config.preset`,
  sha256 of `config.model_dump_json()` (same recipe as T4 `runs.config_sha256`; reuse it if importable without
  cycles, else replicate and test equality), `sys.platform`-machine, python version.
- Open: create dirs. Existing non-empty file and `resume=False` → `FileExistsError` (never clobber). `resume=True`:
  truncate a final partial line (no LF), recover `next_seq` from the last valid line (read a tail block, widen if
  needed); empty/missing file → write `run_started` anyway (a resume with no bus is a fresh file).
- `emit(draft)`: stamp `v, run_id, seq, t_wall (UTC Z), t_mono_ns, game_date, decision_id, fow` (fow from a
  fixed type→fow table taken from the models' `Literal` defaults), validate with `BUS_EVENT_ADAPTER`, serialize,
  reject lines > 1 MiB. Invalid/oversized: raise `BusValidationError` if `config.preset == "dev"` or the writer
  was built with `strict=True` (tests); else drop and write `health invalid_event component=bus` with the type
  and first error in `detail` (state/level from the last `health` draft seen; PLAYING/H0 before any).
  `observation_god` via `emit` is refused (god goes only to `write_god`).
- Write = one `write()` of the full line + `flush()`. `fsync` when the draft's game month differs from the last
  fsynced month, and on `close()`. `seq` advances only after a successful write.
- I/O failure (disk full, EIO): truncate the file back to the last good offset, log once per minute (stdlib
  `logging`), count `dropped_io`, never raise into the daemon. Next emit retries normally.
- `write_god(obs)`: same path into `bus.god.jsonl` with `GOD_BUS_EVENT_ADAPTER`; never raises.
- `close()`: flush, fsync, close; idempotent; emit after close raises `RuntimeError` in strict mode, else drops.

Reader (`reader.py`): `read_events(path, *, god=False) -> ReadResult(events, invalid, seq_issues, partial_tail)`
streaming line by line (`iter_events`), skips and counts invalid lines and unknown `v`, reports gaps/repeats
without reordering, ignores a final line without LF. `BusTail(path, god=False)`: incremental `poll() ->
list[event]` from a byte offset, only LF-complete lines, detects truncation/replacement (size < offset or
inode/ctime change) and restarts from 0 with a flag. `final_acks(events)` = last `intent_ack` per (country, seq).

Compaction (`compact.py`): `compact_god(run_dir, *, keep_every_months: int, runs_dir_current: Path | None)` —
keeps `run_started` and the first `observation_god` of every Nth game month, renumbers `seq` gaplessly, writes
`bus.god.jsonl.tmp`, fsync, `os.replace` (3 retries on `PermissionError` for Windows readers, then error).
Refuses a run named live in `runs/CURRENT` (pid alive). Legal `bus.jsonl` is never compacted (T0 is always
kept; C5 §1 no rotation). Entry: `tools/console/compact_god.py`.

Tests: hypothesis round-trip (random valid drafts of every type → write → read → identical payloads, gapless
seq); crash-truncation at every byte offset of a two-line file → reopen with resume → valid, gapless; resume
continues seq and writes no second `run_started`; non-resume refuses existing file; dev raises / balanced drops
+ `invalid_event`; oversize line; fsync once per month (fake fsync counter); injected `OSError` on write →
file intact, next emit ok; reader counts invalid lines and gaps; `BusTail` with chunked appends and truncation;
compaction invariants (hypothesis: output valid, gapless, subset in order, first line `run_started`, idempotent,
refuses live run); `BusWriter` structurally satisfies `BusSink` (mypy assignment in test).

### U2 Control plane — `src/sae/control/{__init__,inbox,cli}.py`, `tests/unit/t6/test_control_*.py`

`open_inbox(run_dir) -> FileInbox` (creates `control/inbox`, `control/done`).
- `poll()`: `os.scandir` of `inbox/` only (no path input from outside), names ending `.json`, sorted by name;
  `lstat`: non-regular or symlink → `invalid`. Read with a 64 KiB + 1 cap (`too_large` if over, command_id
  `unknown`). JSON error / schema error → `invalid` (detail = first error, ≤ 2000 chars); parsed object with
  `command.cmd` a string outside the v0 set → `unknown_cmd`; `command_id` ≠ file stem → `invalid`. Valid →
  `InboxItem(command, None, handle=stem)`. Each handle returned once until `complete`. Imports `InboxItem`
  from `sae.daemon.ports` (the port's own dataclass; no copy).
- `complete(item, result)`: write `done/<stem>.result.json` atomically (tmp + `os.replace`), then move the
  command file to `done/<stem>.json` (retry 3× on `PermissionError`; missing file ignored). Never raises
  `OSError` into the daemon (log); forgets the handle.

`sae ctl` (`cli.py`, `main(command, argv)`): common flags (`--config --preset --set --runs-dir`) to resolve
`runs_dir` (`paths.runs_dir` else T4's `default_runs_dir`), `--run`, `--reason`, subcommands
`pause | resume | force-strategy <strategy> [--months N] | clear-force | status`. Run = `--run` or first token
of `runs/CURRENT`; none or run dir missing → exit 1 `no running daemon`. `command_id = c-<UTC>-<6 hex>`
(`secrets.token_hex(3)`), `operator = getpass.getuser()` (fallback `unknown`, truncated to 64), write to
`inbox/.<id>.json.tmp` then `os.replace` to `<id>.json`. Wait for `done/<id>.result.json` up to
`RESULT_TIMEOUT_S`, polling every 0.5 s (injected clock and sleep); print the result (status as a short table),
exit 0 if accepted else 1; timeout → exit 1 `no result; is the daemon running?`. Usage errors exit 2, bad config 3.
`--run` validated against the run_id pattern (no path traversal through `--run ../x`).

Tests: accept/reject/timeout against a fake daemon thread that uses `FileInbox` itself (end-to-end over the
filesystem); invalid JSON, too large, unknown cmd, symlink, name mismatch, `.tmp` ignored, ordering, at-most-once
until complete, complete with vanished file; `--run ../../etc` rejected; CURRENT missing → exit 1.

### U3 Telemetry process — `src/sae/telemetry/{__init__,cli,mapping,stream,blueprint}.py`, `tests/unit/t6/test_telemetry_*.py`

- `mapping.py` (pure, no rerun import): event → list of `Record(entity, kind, value, timelines)` where kind ∈
  scalar / text / bar. Timelines: `seq` (sequence), `game_month` (sequence, months since 2200.01.01) and
  `t_wall`. Tier filter: T0 → nothing streamed (bus only); T1 → scalars (posterior per archetype, entropy,
  confidence, `change_score` labelled `belief/change_score (diagnostic only, never acts)`, intent pipeline
  counters emitted/applied/rejected/lost, ack lag, every numeric health metric); T2 → T1 + text logs (strategy,
  decision audit line per `decision_id`, control, health kinds incl. any new kind such as the multiplayer guard
  rendered generically from `kind`/`detail`) + god overlay from `bus.god.jsonl` under `god/…` (red, "debug only").
  Live follow: effective tier = min(config tier, level cap) with H0 → config, H1 → T1, H2/H3/S → T0 (from the
  latest `health` event's `level`).
- `stream.py`: the only module importing `rerun` (lazily, inside functions). `Sink` protocol with `RerunSink`
  (real SDK) and `ListSink` (tests). `run_stream(run_dir, sink, tier, follow, clock, sleep)`: `BusTail`, 2 s poll,
  batch ≤ 1000 events per poll so memory stays bounded; follow while `runs/CURRENT` names the run, else one pass
  to EOF and exit (offline conversion, no level throttling).
- `cli.py`: `sae telemetry --run <id> (--rerun-url URL | --rrd PATH)` plus common flags. Neither given →
  `config.telemetry.rerun_url` / `rrd_path`, else `runs/<id>/telemetry.rrd`. `--rerun-url` → `connect_grpc`; no
  local viewer is ever spawned. Missing `rerun` → exit 1 with `install the telemetry extra: uv sync --extra
  telemetry`. `--rrd` writes a file without any viewer (Wave 2 need).
- `blueprint.py`: Engine Console layout (header text, belief time series, counter table text, committed strategy,
  intent pipeline, decision audit log, economy/military legal, health) via `rerun.blueprint`, sent once.
- Memory: the process reads incrementally (never the whole bus); viewer memory limits are viewer-side flags,
  documented with the exact command for the pinned version (UNVERIFIED until checked against 0.39 CLI help).

Tests: mapping (pure) per event type and tier, level throttling, `change_score` label, unknown health kind
rendered; stream with `ListSink` over a fixture bus and a growing file; `--rrd` produces a non-empty `.rrd`
from a fixture bus (`pytest.importorskip("rerun")`); CLI missing-extra path via monkeypatched import.

### U4 Offline console + import guard — `tools/console/{views.sql,query.py,compact_god.py,README.md}`, `tests/unit/t6/test_console.py`, `tests/unit/t6/test_import_guard.py`

- DuckDB views over `read_json(..., format='newline_delimited', ignore_errors=true)`: `v_events`, `v_decisions`
  (one row per `decision_id`: observation date, top archetype + confidence, strategy + reason, intents emitted /
  rejected, final ack status), `v_intents`, `v_health`, `v_control`. `query.py <run_dir> [SQL]` prints rows.
  Tests `importorskip("duckdb")`.
- Import guard: subprocess `-c "import sae.bus, sae.control, sae.daemon.wiring, sae.daemon.loop; …"` then assert
  no `rerun`, `duckdb`, `pyarrow`, `polars`, `torch`, `sae.telemetry` in `sys.modules`; and `sae.bus`/
  `sae.control` sources contain no `import rerun|duckdb`.

## 2. Dependencies (proposal 010-T6)

Optional extras only: `telemetry = ["rerun-sdk==0.39.0"]`, `console = ["duckdb>=1.1,<2"]`. Runtime deps
unchanged. Applied by T6 at U3 if the architect has not objected; recorded in status.

## 3. Acceptance

`uv run pytest -x -q`, `uv run mypy --strict src/sae/bus src/sae/telemetry src/sae/control`, `uv run ruff check`
green; `sae daemon` reaches past `open_sink`/`open_inbox` (no T6 "not available yet"); `sae ctl status` against a
fake inbox; `.rrd` from a fixture bus.

## 4. Risks

| Risk | Mitigation |
|---|---|
| Rerun API drift (0.39) | Pin; all rerun calls in `stream.py`/`blueprint.py`; pure mapping tested without it |
| Windows `os.replace` over an open file | retries; readers open/close per poll; documented |
| Writer exception kills daemon | I/O errors swallowed + logged; only validation raises, and only in dev/strict |
| SDK buffers unboundedly if remote viewer stalls | bounded batches; document; adversary check |
| Resume with corrupt tail | truncate partial line; scan back to last valid line |

## 5. Expansion backlog

Parquet compaction if bus > 100 MB; FastAPI/SPA console only if Rerun blocks; bus size telemetry in monthly
health; `sae ctl` watch mode.

## 6. Adversary round 1 (2026-10-19) — accepted changes (override §1 where they conflict)

| # | Tag | Change |
|---|---|---|
| 1 | BLOCKING | Writer uses `os.open(O_WRONLY\|O_APPEND\|O_CREAT\|O_BINARY)` + one `os.write` per line; `flush()` no-op. Truncate back only when `os.write` fails or is short; fsync failure is logged, never truncated. |
| 2 | BLOCKING | `poll()` skips any inbox file whose `done/<stem>.result.json` exists and just retries the move (no re-apply after crash/failed move). |
| 3 | BLOCKING | Resume: `next_seq = json.loads(last LF line)["seq"] + 1` (raw, not validated); refuse resume if the first line's `run_id` differs. One forward scan (no tail-block widening). |
| 4 | SHOULD | DuckDB: read `payload` as `JSON`, extract with `->>`. |
| 5 | SHOULD | Telemetry follow stops when CURRENT is gone, names another run, or its pid is dead. |
| 6 | SHOULD | Batch cap 8 MiB per poll; psutil RSS self-check in telemetry, exit 1 with a log line above 150 MiB (was 300 MB; architect decision, C8 §2). |
| 7 | SHOULD | `sae ctl` regenerates `command_id` (after 1 s) if `inbox/` or `done/` already holds the same `c-<ts>` prefix. |
| 8 | SHOULD | `invalid_event` stamping uses only validated health drafts; a failing `invalid_event` is logged and dropped (no recursion). |
| 9 | SHOULD | CURRENT's first token validated by the same run_id regex as `--run`. |
| 10 | CUT → kept small | Compaction is in the orchestrator's brief, so `compact_god` stays, offline-only (refuses a live run), no Windows retry loop beyond one `os.replace`; hypothesis invariants kept (cheap). |
| 11 | CUT → kept small | Blueprint is in the orchestrator's brief: one function building the Engine Console layout, no per-panel logic. |
| 12 | CUT | `BusTail` restarts only on `size < offset`. |
| 13 | CUT | (merged into #3) |
| 14 | CUT | No `strict` flag: raise iff `config.preset == "dev"`. |
| 15 | CUT | Replicate the config sha256 (one line) and test equality with T4's; only T4 import is `sae.daemon.ports.InboxItem` (+ `sae.daemon.runs_dir.default_runs_dir` in `sae ctl`/telemetry CLI, a pure function). |

Fairness: DuckDB views and `sae ctl` read `bus.jsonl` only; god overlay goes to the viewer only.
