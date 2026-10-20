# C6 — GameAdapter protocol, Observation model and fog-of-war boundary

| | |
|---|---|
| Version | 1.3 (`schema_version = 1` on `Observation`/`GodObservation`) |
| Status | FROZEN v1.2 (2026-10-19; v1.1 multiplayer write guard §1.6, proposals 009 health hook §1.8, 011 replay/synth lockstep §1.7; v1.2 proposal 017: neutralise on close/`mp=1` §1.9, Ironman observe-only §1.10, `EmitReceipt.error = "ironman"`, `SaveParser.parse(..., sections=)`; v1.3 proposals 019 Part A `Observation.save_folder_key`/`game_version` §2, 022 recording hygiene §3; §3 recorded PLYR stub keeps `sid`, review 2 B1) |
| Owner | architect (contract); T2 implements the adapters |
| Producers | LiveAdapter, ReplayAdapter, SynthAdapter, release mode (T2) |
| Consumers | Engine core and daemon loop (T4); bus writer (T6) for `observation`/`observation_god` |
| Code | `src/sae/contracts/c6_adapter.py` — `GameAdapter`, `Observation`, `ManagedState`, `GodObservation`, `IntentAck`, `EmitReceipt`, `GameClock`, `SaveParser`, `GodSink` |

## 1. Protocol

```python
class GameAdapter(Protocol):
    @property
    def mode(self) -> AdapterMode: ...                       # live | replay | synth | release
    def observations(self) -> AsyncIterator[Observation]: ... # fog-legal only
    async def emit(self, batch: IntentBatch) -> EmitReceipt: ...
    def acks(self) -> AsyncIterator[IntentAck]: ...
    def clock(self) -> GameClock: ...
    async def close(self) -> None: ...
```

This is the dev-env plan's async protocol (advisor's pick). Construction takes, besides
mode-specific options: a `SaveParser` (daemon-provided; it asks the Governor and spawns `sae-parse`,
C7) and a `GodSink` (the bus writer's god file). The engine core imports only `sae.contracts`.

Semantics:

1. `observations()` yields at most one `source="log"` observation per game month (on each `HB`, C1)
   and one `source="save"` observation per successfully parsed save. Within a `session_epoch`,
   `game_date` never decreases.
2. `emit()` compiles with `compile_batch` + `render_ini` (C3/C2) and writes atomically (C2 §2).
   It never raises for I/O; it returns `EmitReceipt(ok=False, error=…)`. `ok=True` means the file is
   in place, not that the game applied it — only an ACK says that.
3. `acks()` yields every C1 `ACK` in arrival order, converted to `IntentAck`. The adapter does not
   deduplicate or time out; LOST/SUPERSEDED are engine decisions (C3 §5).
4. `close()` is idempotent; iterators end after it; `emit()` after `close()` returns
   `EmitReceipt(ok=False, error="closed")`.
5. Errors in inputs (bad log line, failed parse, missing file) never end the iterators; they become
   `health` events (C1 §5, C4 §2) and the stream continues.
6. **Multiplayer write guard** (maintainer decision 2026-10-19). The adapter keeps `mp` = the `mp`
   field of the most recent C1 `HELLO` it has read (any epoch; unknown before the first `HELLO` or
   when `mp` is absent). While `mp == 1`, `emit()` writes nothing (no `.tmp`, no `os.replace`) and
   returns `EmitReceipt(ok=False, error="multiplayer")`; a later `HELLO` with `mp=0` lifts it.
   **Owner: the adapter (T2), once, in the write path shared by every adapter that writes**
   (live; replay/synth apply it to their scratch file so the rule is testable). The daemon and engine
   do not re-check. Visibility: the receipt is logged in C5 `intent_emitted`, and the engine turns
   every `ok=False` receipt into `health ini_write_failed` (component `engine`, detail
   `seq <n>: multiplayer`). `multiplayer` refusals do not count toward the three-failure `ack_alarm`
   (C2 §2), since pressing the hotkey would not help. Unknown `mp` allows writes: A-13 is UNVERIFIED,
   and if `is_multiplayer` does not exist the mod prints `mp=0` and the guard is inert (C2 §5 note).
7. **Replay/synth lockstep** (proposal 011). Applies when `mode` is `replay` or `synth`; `live` and
   `release` are unchanged. Consumer (T4 daemon loop) obligations:
   a. It requests observation N+1 from `observations()` only after it has completely handled
      observation N: the engine step, publishing its bus events, and, if a batch resulted, the awaited
      `emit()` and the engine's handling of the receipt. (In `sae.daemon.loop`: `_feed_obs` hands each
      observation to the consumer and awaits a per-observation completion signal, or calls `_on_obs`
      inline, before its next `__anext__`.)
   b. ACKs: every `IntentAck` that `acks()` has yielded before observation N+1 is yielded is handled by
      the engine before observation N+1 (the ack feeder enqueues on receipt and awaits nothing else;
      the queue stays FIFO within a priority class).
   Adapter (T2) obligations: a pulse's ACKs are yielded from `acks()` and handed to a waiting consumer
   before that pulse's observation is yielded; `emit()` in these modes performs no wall-clock wait.
   Together these make the bus a function of (input, seed, config) at `speed=max` (§3).
8. **Health hook** (proposal 009). Adapters report the health kinds of C1 §5 and C4 §2
   (`log_parse_error`, `version_skew`, `reconnect`, `ini_write_failed`, `ack_alarm`, `invalid_event`)
   through a `health: Callable[[HealthKind, str | None], None]` given at construction (C8 §5.1,
   `make_adapter(..., *, health=None)`; replay/synth constructors take the same keyword). The caller
   (T4) stamps `component="adapter"`, `state`, `level` and `game_date`. The hook must not raise into the
   adapter (the adapter wraps it). With `health=None` an adapter may only log; it must not import
   `sae.core` to build bus events. No model change: `GameAdapter` and `AdapterFactory` are unchanged.
   v1.2 adds the kinds `ini_neutralised` (§1.9) and `ironman_observe_only` (§1.10) to this hook.
9. **Neutralise on close and on `mp=1`** (maintainer decision 2026-10-19, Wave 3 N3, proposal 017).
   **Owner: the adapter (T2), in the same shared write path as §1.6** (live; replay/synth on their
   scratch file). The adapter writes the C2 §2.1 neutral file (knob line only, `period`/`quiet` from
   the latest `HB`/`HELLO`, else 1/0; no country lines, no seq):
   a. in `close()`, on its first call only, before the iterators end, while the health hook still works;
   b. on reading a `HELLO` with `mp=1` when the previous `mp` was 0 or unknown (once per transition).
   It never deletes the file and never writes in `release` mode. While §1.10 is latched, trigger b is
   skipped but trigger a still writes (maintainer, 2026-10-19: neutralise on exit even under Ironman).
   Success → `health(ini_neutralised, "close" | "multiplayer")`; failure after the C2 §2 retries →
   `health(ini_write_failed, "neutralise <close|multiplayer>: <cause>")`; `close()` still completes and
   never raises. **The daemon (T4)** calls `await adapter.close()` on every clean exit, including
   SIGTERM/SIGINT and the C7 self-restart, **before** it closes the bus sink, so the health event is
   written. A crash or kill -9 leaves the last batch on disk (accepted: the `(sid, seq)` and
   `sae_managed` gates of C2 §5 still apply).
10. **Ironman: detect and observe only** (maintainer decision 2026-10-19, Wave 3 N6, proposal 017;
   implements the settled "Ironman has no live loop"). `live` mode only.
   a. Detection: the LiveAdapter also watches `save_dir/*/ironman.sav` with the §3 eligibility rule,
      counting only a file created or modified after the adapter started (an old Ironman campaign on
      disk never triggers). It calls `SaveParser.parse(path, sections=("meta",))` (C4 §1, existing
      `--sections`) and uses only `meta.ironman`. The result is never yielded as an observation and its
      `god` is `None` (meta-only), so nothing goes to the `GodSink`. `ironman` is `True` → latch.
      `False` or `None` (A-20: undetectable) → no change; `parse` returning `None` → retry on the next
      eligible write.
   b. When latched (for the adapter's lifetime; leaving needs a daemon restart): exactly one
      `health(ironman_observe_only, "<save path>: console disabled in Ironman; observe only")`;
      every later `emit()` writes nothing and returns `EmitReceipt(ok=False, error="ironman")`; no
      `mp=1` neutral write either, but the `close()` neutral write (§1.9 a) still happens. Observations from the log, if it works in Ironman (A-20), continue.
   c. Engine (T4): a batch whose receipt has `error="ironman"` awaits no ACK: it is never marked
      `LOST`, never re-emitted, counts toward no `ack_alarm` (neither the C2 §2 write-failure count nor
      the C3 §5 LOST rule), and does not produce the per-batch `health ini_write_failed` of §1.6 (the
      one-shot `ironman_observe_only` explains it). `intent_emitted` still logs the receipt.
   d. No `Observation` field: the adapter enforces the rule, as with `mp`; the engine sees only the receipt.

## 2. Observation assembly (all adapters)

| Field | Source | Rule |
|---|---|---|
| `game_date` | latest HB or save date | |
| `m` | latest HB `m` | `None` before the first HB |
| `session_epoch` | adapter | +1 on `HELLO` or on a date regression (save reload), at most +1 per observation; events `hello` / `date_regressed`. On a new epoch the adapter discards cached SNAPs. Starts at 0; a first `HB` with no `HELLO` opens the session without a bump (C1 §5, proposal 006). `ManagedState.applied_seq` may go **down** across epochs (reloaded save). |
| `player_sid` | save `countries[is_player]`; else PLYR `sid` | `None` in observer mode |
| `managed` | one `ManagedState` per country with the `sae_managed` flag | values from the latest SNAP if `snap_date` ≥ save date, else from the save's `self_state`; `applied_seq`/`applied_econ`/`applied_mil` from SNAP, else from the save's `self_state`; `pending_seq` from the save only (SNAP has no pending) |
| `player_legal` | latest parsed save's `legal` | carried forward unchanged until the next parse |
| `legal_as_of` | that save's date | lets T4 age features |
| `events` | adapter | `hello`, `date_regressed`, `save_parsed` |
| `save_folder_key` (v1.3) | adapter | `live`/`release`: `"f-" + hmac.new(salt, <folder name UTF-8>, sha256).hexdigest()[:16]` of the latest parsed save's parent folder under `save_dir` (A-46); `replay`/`synth`: `"f-synth-" + sha256(run_id UTF-8).hexdigest()[:8]` (unique per run, so these runs never merge). `None` before the first parsed save; carried forward like `player_legal`. The folder name itself never reaches any SAE file |
| `game_version` (v1.3) | adapter | latest parsed save's `meta.game_version` (C4), ≤ 64 chars; `None` before the first parse; carried forward |

**Install salt** (v1.3): 32 bytes from `secrets.token_bytes(32)` in `<sae_dir>/salt` (`c8_config.sae_dir()`).
The adapter that first needs it creates it with `os.open(path, O_CREAT|O_EXCL|O_WRONLY)` (parent dirs created);
a loser of the race (`FileExistsError`) re-reads the winner's file. A salt file that is not exactly 32 bytes →
`health invalid_event` once and no folder key for the run (`None`), never a rewrite. Deleting the salt makes all
older folder keys unlinkable (privacy reset, documented by T8).

Integer bound (proposal 007): `sid`, `m`, every seq field (`applied_seq`, `pending_seq`, `IntentAck.seq`/`applied`,
`EmitReceipt.seq`) and every `LegalView` integer are at most `CLAUSEWITZ_INT_MAX` = 1,000,000 (A-17).
An adapter that reads a larger value drops that record and reports `health invalid_event`; it never
constructs an out-of-range model.

Which AIs are managed is decided **by the mod**: it sets `sae_managed` on AI countries in contact
with the player (report: "only AI empires in contact with the player", A-32), capped by the mod's
own constant `@sae_max_managed = 16` (first 16 by `sid`). The flag is sticky: once set it is never
removed (proposal 006 R4); a managed country later played by a human answers `REJECTED/NOT_MANAGED`. `engine.max_managed` (≤ 16) can only lower
the number the engine steers. The engine never manages a country the mod did not flag (validator `not_managed`).

## 3. Adapter modes

| Mode | Observations | `emit` | `acks` |
|---|---|---|---|
| `live` | tail `game.log` (C1) every 2 s; watch `save_dir/*/` for new or modified (size/mtime) `autosave_*.sav` (and `ironman.sav`, overwritten in place: parsed meta-only for detection, §1.10; A-34); a save is eligible when size and mtime are unchanged for 2 s **and** ≥ 8 s have passed since its last modification; then `SaveParser.parse` | atomic `sae_in.ini` write | from `ACK` lines |
| `replay` | a recorded session dir `{game.log, game.log.ts, saves/, bus.jsonl?}`; options `speed` (`realtime`, `<N>x`, `max`), `from`, `stop_at` (game dates) | writes the ini into the session's scratch dir; never to the real game folder | per `ack_model`: `echo` (the Python mod simulator applies C2 §5 to each written file and emits C1 ACKs), `drop:<p>`, `delay:<months>`, `from_recording` |
| `synth` | archetype-labelled generator (`archetype` schedule, `seed`, `years`) producing FixtureSpecs → `sae-parse fixture` → `SaveParser.parse`, plus synthetic HB/SNAP lines; optional `record_dir` writes the replay session format (`game.log`, `game.log.ts` with `i*1e9` ns, `saves/<offset:012d>-autosave_<date>.sav`; proposal 015) | as replay | as replay (default `echo`) |
| `release` | saves (and the log if it works in Ironman, A-20); no live input | `EmitReceipt(ok=False, error="release_mode")` | none |

**Recording hygiene** (v1.3, proposal 022; C8 `record`): `live --record` and `synth --record` honour
`record.saves` (`all`; `none` = log-only strict recording; `every_n` = every `record.save_every`-th eligible save,
so `saves/` stays a valid replay session) and `record.max_bytes` (per recording dir: when the next save copy would
exceed it, save copies stop, the log keeps recording, one `health record_capped`). Nothing is ever deleted. With
`record.redact_plyr = true` (default) every PLYR line in the recorded `game.log` is redacted **but keeps its
identity** (review 2 B1): a line that parses under C1 is recorded as exactly `SAE|v1|PLYR|<date>|sid=<n>` (stats
and `name` removed, `sid` and the date kept as parsed); replay parses that stub as a stat-less `PlayerRecord`
(C1 §3), so the echo simulator registers the human's sid and acks match the live run, and no `log_parse_error` is
emitted for it. A PLYR line that fails C1 parsing is recorded in the bus-redaction form `SAE|v1|PLYR[|<date>]`
(`sae.adapters.logtail._redact`, proposal 012) and replays as the same `log_parse_error` the live run saw. The `sid`
is a country index, not god data; `name` and every stat are. `false` (dev preset only) also writes an empty marker file
`GOD_VIEW_INSIDE` in the recording dir. A recording may carry `label.json` (C10 §1 `CampaignLabel`); `synth
--record` writes it from its schedule.

`game.log.ts` (replay sidecar) is a text file of `<byte_offset> <monotonic_ns>` lines written while
recording; it lets replay reproduce wall-clock pacing. Replay and synth are deterministic for a given
seed and `speed=max`.

## 4. Fog-of-war boundary

The engine may only use what a player in the AI's seat could plausibly see (Alien: Isolation rule).

1. **Whitelist by type.** `Observation` contains only: each managed AI's *own* state (an AI knows
   itself), and per-observer `LegalView`s about the player (C4 §3, the diplomacy-screen-legal
   approximation). All models forbid extra fields, so god-view values cannot ride along.
2. **God data has its own type and file.** PLYR lines (C1) and the parser's `god` section (C4) are
   written as `GodObservation` to `GodSink` → `bus.god.jsonl`, and are **never** yielded by
   `observations()`.
3. **Unobserved is `None`, never imputed.** A `LegalView` with `in_contact=false` has every feature
   `None` (enforced by the model). T4 aggregates the per-observer views into one shared belief about
   the player using only views with `in_contact=true`.
4. **Code boundary.** `sae.core` and `sae.daemon` must not import `sae.adapters`, must not
   reference `bus.god.jsonl`, and must not read `ParseResult.god`, `GodView`, `GodObservation` or
   `PlayerRecord` (the daemon's `SaveParser` hands the `ParseResult` to the adapter untouched; only T2
   splits `god` off to the `GodSink`). T7 adds a CI test: import graph plus a grep of `sae/core` and
   `sae/daemon` for `GodView|GodObservation|PlayerRecord|\.god\b|bus\.god`. Per the advisor this test is a **convention, not a
   guarantee**: a determined change can bypass it. Reviews treat any new god-path reference as blocking.
5. The strict per-(observer, target) intel mask is deferred (report decision 3).

## 5. Examples

```json model=Observation
{"game_date": "2231.04.01", "m": 375, "session_epoch": 1, "source": "log", "player_sid": 1,
 "managed": [
   {"sid": 3, "name": "Tzynn Empire", "applied_seq": 17, "pending_seq": 17, "applied_econ": 2, "applied_mil": 3,
    "fleet_power": 8450, "naval_cap_used": 110, "naval_cap": 140, "snap_date": "2231.04.01"},
   {"sid": 4, "name": "Kel-Azaan Hegemony", "applied_seq": 0}],
 "player_legal": [
   {"observer_sid": 3, "in_contact": true, "systems_owned": 41, "frontier_systems": 6,
    "fortified_border_starbases": 4, "fleet_power_cat": 1},
   {"observer_sid": 4, "in_contact": false}],
 "legal_as_of": "2231.03.01", "events": []}
```

v1.3: after the first parsed save (live), and a synth run's key:

```jsonl model=Observation
{"game_date": "2231.05.01", "m": 376, "session_epoch": 1, "source": "save", "player_sid": 1, "managed": [], "player_legal": [], "legal_as_of": "2231.05.01", "events": ["save_parsed"], "save_folder_key": "f-9c1185a5c5e9fc54", "game_version": "Cepheus v4.4.4"}
{"game_date": "2201.02.01", "m": 1, "session_epoch": 0, "source": "save", "player_sid": 1, "managed": [], "player_legal": [], "legal_as_of": "2201.02.01", "events": ["save_parsed"], "save_folder_key": "f-synth-0a1b2c3d", "game_version": "Cepheus v4.4.4"}
```

Invalid: a raw folder name in the key field.

```json model=Observation invalid
{"game_date": "2231.05.01", "m": 376, "session_epoch": 1, "source": "save", "player_sid": 1, "managed": [], "player_legal": [], "legal_as_of": "2231.05.01", "save_folder_key": "united_nations_of_earth"}
```

Invalid: a god-view field smuggled into the observation.

```json model=Observation invalid
{"game_date": "2231.04.01", "m": 375, "session_epoch": 1, "source": "log", "player_sid": 1,
 "managed": [], "player_legal": [], "legal_as_of": null, "player_fleet_power": 12010}
```

```json model=GodObservation
{"game_date": "2231.04.01", "source": "log", "plyr": {"fleet": 12010, "al_inc": 40, "wars": 0}}
```

```json model=IntentAck
{"seq": 17, "country": 3, "status": "APPLIED", "code": "OK", "game_date": "2231.05.01", "reason": 1}
```

```json model=EmitReceipt
{"seq": 18, "ok": false, "error": "write_failed", "detail": "PermissionError after 3 retries"}
```

Invalid: unknown status. (`LOST`/`SUPERSEDED` validate as `IntentAck` values, but adapters never
produce them; only the engine does, C3 §5.)

```json model=IntentAck invalid
{"seq": 17, "country": 3, "status": "MAYBE", "code": "OK", "game_date": "2231.05.01"}
```

## 6. Versioning

Adding a protocol method or an `Observation` field is a contract change (proposal; T2 and T4 update
together). `schema_version` becomes 2 when a field is removed or its meaning changes.
v1.3 adds two optional `Observation` fields (`save_folder_key`, `game_version`, default `None`) and the recording
rules of §3; `schema_version` stays 1 because both fields are optional and every reader validates with this model.
v1.2 adds `EmitReceipt.error = "ironman"`, the keyword `SaveParser.parse(..., *, sections=None)`
(implementations must accept it) and rules §1.9/§1.10; no `Observation` field, `schema_version` stays 1.
v1.1 adds one `EmitReceipt.error` value and rule §1.6; no `Observation` field changes (the engine
does not need `mp`: the adapter enforces the guard), so `schema_version` stays 1.

## 7. UNVERIFIED assumptions (M0 checklist)

| Id | Assumption | M0 test | Fallback |
|---|---|---|---|
| A-20 | The `log` effect still writes in Ironman; `ironman.sav`'s zip `meta` has `ironman=yes` (§1.10) | M0-14 | Release mode observes saves only; `ironman = null` → no detection, `ack_alarm` documented as expected in Ironman |
| A-13 | `HELLO` `mp=1` really marks a multiplayer session (`is_multiplayer` exists) | M0-6 | Guard inert; live mode documented as single-player only |
| A-24 | Autosave can be monthly in dev | M0-11 | Log channel carries monthly signal |
| A-34 | Autosave file names `autosave_*.sav` / `ironman.sav` | M0-11 | Config glob |
| A-46 | Each campaign's autosaves live in one folder under `save games/`; a folder name is reused only by a new game of the same empire name | M0-11 | C10 §1 split rule + `sae profile exclude` |
| A-32 | The mod can detect "in contact with the player" in script to set `sae_managed` | M0-6 | Engine-chosen list staged via a C2 v2 line |
