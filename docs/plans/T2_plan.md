# T2 plan — Adapters and bridge I/O

Owner: T2 lead. Paths: `src/sae/adapters/**`, `tests/unit/t2/**`. Status: v2 (after adversary review; see §8).

Inputs: C1, C2, C3, C4, C5 (health kinds), C6 (primary), C8 (§3 paths, §5 CLI); `mod/sae_bridge/SEMANTICS.md`
§§1–9 (R1–R10, proposal 006 PROPOSED — built to as normative); accepted proposals 002, 004, 007; T4 wiring
(`sae.adapters.make_adapter(config, parser, god_sink)`), T4 `GovernedSaveParser` (already caps exit-6 retries at 3
per (path, size, mtime) and reports `parse_failed`/`save_skipped` itself).

## 1. Principles

1. Pure cores, thin shells. Line splitting, record → observation assembly, the mod simulator and the
   ini writer's retry policy are pure/synchronous and take injected clocks, file ops and RNGs. Only
   `live.py`/`replay.py`/`synth.py` touch asyncio.
2. No real sleeps in tests: `sleep: Callable[[float], Awaitable[None]]` and `monotonic: Callable[[], float]`
   are constructor arguments everywhere a wait exists.
3. Fog boundary by construction: PLYR records and `ParseResult.god` are routed in exactly one function
   (`assembler._route_god`) to `GodSink.write_god`; `Observation` is built only from HB/SNAP/HELLO and
   the save's `countries`/`self_state`/`legal`.
4. Untrusted input never crashes an iterator: `LogLineError`, `ValidationError`, `OSError`,
   `UnicodeError` → drop + `health` event; iterators continue (C6 §1.5).
5. Every Stellaris assumption carries its A-id and the word UNVERIFIED in a comment.

## 2. Health channel (gap in C6)

C6 says input errors "become health events" but `GameAdapter` has no health method, and `make_adapter`
receives only `(config, parser, god_sink)`. In production `god_sink` is T4's `BusSink`, which also has
`emit(EventDraft)`. Plan:

- `health.py`: `HealthFn = Callable[[HealthKind, str | None], None]` plus plain-dict rate limits
  (`log_parse_error` once per game month per code; `version_skew` once per session; `reconnect` once
  per cause until recovered). Adapters take `health: HealthFn`.
- `make_adapter(config, parser, god_sink, health=None)`: T4 may pass its own `HealthFn` (proposal 009 option C).
  When `None`, it builds one from `god_sink` if it has `emit`: `HealthPayload(kind, component=
  "adapter", state=PLAYING, level=H0, detail=…)` wrapped in T4's `EventDraft` (lazy import of
  `sae.core.state.EventDraft`; the bus writer re-stamps state/level per C5 §5). Otherwise log only.
- Proposal **009-T2** (PROPOSED): add `HealthSink`/`emit_health` to C6 or move `EventDraft` to
  `sae.contracts` so adapters do not import `sae.core`. Build against the duck-typed path meanwhile.

## 3. Work units (sequential, one implementer at a time, disjoint files)

| Unit | Files (`src/sae/adapters/`) | Tests (`tests/unit/t2/`) |
|---|---|---|
| U1 bridge I/O | `health.py`, `logtail.py`, `ini_writer.py`, `acks.py`, `paths.py` | `test_logtail.py` (+hypothesis), `test_ini_writer.py`, `test_acks.py`, `test_paths.py` |
| U2 assembly | `assembler.py`, `savewatch.py` | `test_assembler.py`, `test_savewatch.py`, `test_fog.py` |
| U3 simulator | `simulator.py` | `test_simulator.py` (SEMANTICS conformance), `test_roundtrip.py` (+hypothesis on ini) |
| U4 live | `live.py`, `__init__.py` (`make_adapter`) | `test_live.py`, `test_make_adapter.py` |
| U5 replay/synth/CLI | `replay.py`, `synth.py`, `cli.py` | `test_replay.py`, `test_synth.py`, `test_cli.py` |

### U1 bridge I/O
- `LogTailer(path, opener)`: keeps `(offset, head)`; `poll() -> list[bytes-lines]`. Reads from
  offset to EOF, splits on `b"\n"` only, keeps the trailing partial line buffered (bounded at 8 KiB;
  overflow → drop the buffer, health `log_parse_error` code `too_long`). Strips one `\r`. Decode
  UTF-8 `errors="replace"` (A-16). Identity = the first ≤ 512 bytes seen (compared over the stored length;
  the game prefixes timestamps, A-04), platform-neutral (inode/ctime are unstable on drvfs/9p). Size < offset or
  head change → offset 0, partial buffer cleared, `reconnect` (A-15). Missing → `reconnect` detail `missing` once.
  Opened for read with shared access (Python default on Windows is share-read/write; delete-share needs
  `msvcrt`, out of scope — A-15 note).
- `parse_lines(lines) -> list[LogRecord | LogLineError]` wrapper over `parse_log_line` that catches
  `LogLineError` and `ValidationError`.
- `IniWriter(path, replace=os.replace, fsync=os.fsync, sleep)`: `sae_in.ini.tmp` → flush → fsync →
  replace, run in `asyncio.to_thread`; retries 0.1/0.2/0.4 s on `OSError`; returns `EmitReceipt` (sha256,
  bytes, path); tmp removed on failure; each failure → `ini_write_failed`. No `ack_alarm` here (the engine,
  `engine_tick.py`, owns it).
- `emit_batch(batch, writer)`: `compile_batch` + `render_ini`; a `ValidationError` from compile →
  `EmitReceipt(ok=False, error="write_failed", detail="compile: …")` (never raises).
- `acks.py`: `AckRecord → IntentAck` (C6 model; `ValidationError` → drop + `invalid_event`).
- `paths.py`: C8 §3 detection. Windows Documents via `ctypes` `SHGetKnownFolderPath` (gated, mocked);
  Linux `~/.local/share/Paradox Interactive/Stellaris`; parser exe on PATH else
  `parser/target/release/sae-parse[.exe]`. Returns a frozen `ResolvedPaths`.

### U2 assembly
- `Assembler` (sync, pure aside from god sink + health): `on_record(rec) -> list[Observation | IntentAck]`,
  `on_parse(result) -> Observation | None`, `clock() -> GameClock`.
  - Session epoch starts at 0. `+1` on HELLO and on an HB whose date < previous HB date
    (`date_regressed`); at most +1 per observation. The first HB with no HELLO seen opens the session
    at the current epoch: no bump, no event (C1 §5, C6 §2; T4 resets engine state on every epoch change).
    New epoch → clear SNAP cache.
  - HB → one `source="log"` Observation, held pending and flushed when a record with a later date arrives,
    at the end of a live poll, or at end of input — so the same pulse's SNAPs (printed after HB,
    SEMANTICS §4) are in it. Acks of a pulse are always delivered before that pulse's observation; SNAP → cache by sid (only valid SNAPs; staleness by `snap_date`);
    PLYR → god sink only (also remembers `sid` for `player_sid` fallback, C6 §2); ACK → `IntentAck`.
  - Save parse → split `god` to god sink first, then build `source="save"` Observation; `player_legal`,
    `legal_as_of` carried forward; `managed` = union of save `countries[managed]` and cached SNAP sids,
    merged per C6 §2 (SNAP wins iff `snap_date ≥ save date`; `pending_seq` from save only).
    Observation `game_date` = max(clock date, save date) within the epoch (monotonic rule C6 §1.1).
  - HELLO `schema ≠ 1` → `version_skew`, `emit_blocked=True` (emit returns `write_failed`/`schema_skew`).
  - Any `ValidationError` while building → drop + `invalid_event` (proposal 007).
- `SaveWatcher(save_dir, glob, stat, monotonic)`: tracks `(size, mtime_ns)` per file. Eligibility =
  unchanged for ≥ 2 s **and** ≥ 8 s of monotonic time since this adapter first saw this exact version.
  Uses no wall clock, so clock skew and future mtimes cannot stall or rush it (stricter than C6, never
  looser). At start, existing files are a baseline; only the newest is parsed once. When several are
  eligible, only the newest by mtime is returned; older ones are marked done. One parse attempt per
  version (the governed parser already caps exit-6 retries); a new size/mtime re-arms. Keyed on the
  path the watcher found, never `ParseResult.save.path`. `ironman.sav` only in release mode (A-34).

### U3 simulator (`simulator.py`)
Reproduces SEMANTICS §§1–8 exactly, R1–R10 included: anchor state, sid assignment by ascending country id,
sticky `sae_managed` (contact with a human), HB never suppressed, §6 apply rules 1–11 in order, stagger
`(m + sid) mod period`, quiet gates only SNAP/PLYR, `sae.10` at most once per human per pulse, knob reading
from the human (ignored if out of range / non-integer), `is_multiplayer`. API:
`ModSimulator(countries, rng)`; `run_ini(text)` (console `run`: knob line → player vars; country lines stage
only on countries with `sae_managed` and matching `sae_sid`); `pulse(date) -> list[str]` (C1 lines via
`format_log_line`); `hello(date)`; `snapshot()`/`restore(snap)` (save reload: `m`, `applied_seq` go down);
`flavour_events` log for tests. Not-C2 text in `run_ini` is ignored per line (the console would error).

### U4 live (`live.py`, `__init__.py`)
`LiveAdapter(paths, parser, god_sink, health, *, release=False, sleep, monotonic, poll_s=2.0, record_dir=None)`:
one pump task (started on first iterator use) polls the tailer then the watcher every 2 s and feeds an ack queue
and an observation queue (all acks of a poll are put before its observation). It never awaits `parser.parse`
inline: an eligible save starts one parse task (skipped while one runs) and tailing continues; `close()` cancels the pump, puts sentinels, idempotent; `emit` after close →
`closed`; release mode: always tails the log (HBs simply never arrive if A-20 fails), `emit` → `release_mode`,
`acks()` ends immediately. `make_adapter(config, parser, god_sink)` resolves paths (`paths.py`) and returns
a `LiveAdapter` for `live`/`release`; other modes → `ValueError` (CLI builds those).

### U5 replay / synth / CLI
- Replay and synth have no pump and no queues: `observations()` is a pull-driven generator, and `acks()`
  drains a list the generator fills (acks of a pulse are produced before its observation is yielded). The
  simulator runs a staged ini at the first pulse produced after `emit` returned — deterministic by construction.
- Session dir `{game.log, game.log.ts?, saves/<offset:012d>-<name>.sav, bus.jsonl?}`; save files are parsed
  when the replayed byte offset passes their prefix. `ReplayAdapter(session, parser, god_sink, speed,
  from_, stop_at, ack_model, seed, scratch_dir, sleep, monotonic)`. `speed=max` no sleeps; `realtime`/`Nx`
  pace by `game.log.ts` (`<offset> <mono_ns>`), missing sidecar → health detail `no_ts` and run as `max`.
  `from_`: records before it update state but yield nothing; `stop_at`: end iterators after the first HB with
  date > stop_at. Ack models: `echo` (simulator mirrors managed sids from recorded SNAP/HB and runs every written
  ini at the next recorded HB; recorded ACK lines ignored), `drop:<p>` (seeded `random.Random(seed)`), `delay:<k>`
  (run k months later), `from_recording` (recorded ACK lines only, no simulator). `emit` writes
  `scratch/sae_in.ini` only.
- `SynthAdapter(archetype_schedule, seed, years, parser, god_sink, parser_exe, scratch_dir, ack_model, …)`:
  seeded world (player + 3–6 AIs) driven month by month through `ModSimulator.pulse`; every 3 months a
  `FixtureSpec` (strict JSON types, proposal 002) built from the archetype's player trajectory →
  `sae-parse fixture --spec … --out …` (subprocess, injected runner) → `parser.parse(path)`. Archetype
  trajectories target T4's 5 features (expansion rate, border fort ratio, fleet-power category, months
  to first war, tech category) so T4's acceptance can use synth.
- Recording is folded into `LiveAdapter(record_dir=…)`: appends raw log bytes, `game.log.ts`, and copies
  eligible saves offset-prefixed.
- `cli.py`: `main(command, argv)`; common flags (`--config --preset --set --runs-dir`), replay/synth args per
  C8 §5; builds a factory `lambda parser, god: …Adapter(...)` and calls `asyncio.run(sae.daemon.run(cfg,
  factory, run_id))`. Exit 2 usage, 3 config.

## 4. Tests and acceptance

| Area | Test |
|---|---|
| Tailer | hypothesis: arbitrary chunking of a byte stream (incl. CRLF, U+2028 in names, invalid UTF-8) yields the same records as one-shot; truncation, identity change, missing file, partial line at EOF; 8 KiB partial cap |
| Log/ini parsers | hypothesis: `format_log_line`↔`parse_log_line` round trip for generated records; `render_ini`↔`parse_ini`↔simulator staging round trip |
| Ini writer | retry schedule 0.1/0.2/0.4 with fake sleep; tmp cleanup; three failures → `ack_alarm`; sha256/bytes |
| Simulator | one conformance test per SEMANTICS §6 rule 1–11 and §4 steps (knob timing R8, stagger, quiet, sticky managed, R10 popup cap, R2 silent reset, R1 self-target RANGE, reload lowers applied_seq) |
| Round trip | `IntentBatch → compile_batch → render_ini → simulator.run_ini → pulse → parse_log_line → IntentAck` for every C2 §5 branch |
| Assembler | epoch rules (HELLO, first-HB, regression, +1 cap), SNAP vs save merge, carry-forward, `invalid_event` on out-of-range (>1,000,000) values, monotonic game_date |
| Fog | a PLYR line and a `ParseResult.god` reach only the god sink; no field of any yielded `Observation` equals a god-only sentinel value; `sae.adapters` Observations contain no PLYR stats |
| Watcher | 2 s / 8 s rule with fake monotonic; save written in pieces; future mtime (clock skew); newest-only; re-arm on change |
| Live | ack-before-observation order within a poll; tailing continues while a parse is in flight; fake fs + fake parser: HB → observation; ACK → ack; emit writes atomically; close idempotence, emit after close, release mode |
| Replay | determinism: two runs at `speed=max` give identical observation/ack sequences (hash); from/stop_at; each ack model |
| Synth | per archetype, fixture spec validates strictly; with the real `parser/target/debug/sae-parse` (skip if absent) a 2-year run parses and yields save observations |
| CLI | `sae replay`/`sae synth` argument errors → 2; `sae daemon` no longer reports adapters "not available yet" |

Gate: `uv run pytest -x -q`, `uv run mypy --strict src/sae/adapters`, `uv run ruff check`.

## 5. Risks

| Risk | Mitigation |
|---|---|
| A-15 truncation vs Windows share modes (game may hold the log with no FILE_SHARE_DELETE) | read-only open, reopen each poll; identity check; mocked |
| Windows `os.replace` fails while the console reads the ini | retry policy; three-strike alarm |
| Health channel duck-typed on BusSink | proposal 009-T2 |
| Synth features may not drive T4 to the expected counter strategy | synth only shapes the player fixture; T4/T7 own the acceptance; tune after first run |
| A-24 autosave cadence unknown | log channel carries monthly signal; watcher is cadence-agnostic |

## 6. UNVERIFIED (to T8 M0 checklist)
A-03 log path, A-04 prefix, A-08 run path, A-11 line length/LF, A-15 truncation on launch, A-16 UTF-8, A-20
log in Ironman, A-24 autosave cadence, A-33 Documents redirection, A-34 save names, A-35 HELLO on reload
(not relied on: first-HB rule), A-36..A-42 via simulator (proposal 006).

## 7. Expansion backlog
Keystroke injection (opt-in, after M0-10); Proton path detection; StarNet profile; release-mode
between-session `scripted_variables` generator; Windows `CreateFileW` share-delete reader; inotify/
ReadDirectoryChangesW instead of polling.

## 8. Change log
- v1: initial.
- v2 (adversary): first HB without HELLO no longer bumps the epoch (blocker 1); pump never awaits a parse
  (blocker 2); replay/synth pull-driven, no pump (blocker 3); HB observation flushed after its pulse's SNAPs
  (blocker 4); tailer identity by file head, not inode/ctime (5); ack-before-observation rule + test (6);
  `make_adapter` takes optional `health` (009 option C) (7); writer drops `ack_alarm` (8) and runs in a
  thread (9); release always tails (10). Cut: HealthGate class, recorder unit. Kept (C8 §5 contracted
  arguments): `realtime`/`Nx` pacing and `drop`/`delay` ack models, kept minimal. Not taken: baseline-parse
  skip on resume (12; the adapter does not know about `state.json`, cost is one parse per re-exec).
  Added to M0 list: OneDrive lock on `sae_in.ini.tmp` (A-33).
