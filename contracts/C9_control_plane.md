# C9 — Control plane (`sae ctl`)

| | |
|---|---|
| Version | 1 (`v = 1` in commands and results) |
| Status | FROZEN v1.1 (2026-10-19; proposal 016-T6: command expiry) |
| Owner | architect (contract); T6 implements `sae ctl` and the daemon-side inbox reader |
| Producers | operator via `sae ctl` (T6); tests |
| Consumers | daemon loop (T4) applies commands at the next tick boundary; bus `control` events (C5) |
| Code | `src/sae/contracts/c9_control.py` — `ControlCommand`, `Pause`, `Resume`, `ForceStrategy`, `Status`, `ControlResult`, `EngineStatus`, `command_expired`, `COMMAND_MAX_AGE_S`, `COMMAND_MAX_FUTURE_S` |

## 1. Scope (advisor cut)

v0 commands: `pause`, `resume`, `force_strategy` (with clear), `status`. Nothing else: no intent
override, no learning freeze, no LLM switch, no snapshot/restore. None of them edits game state
directly; all game effects still go through intents (C3).

| Command | Semantics |
|---|---|
| `pause` | Level S (C7): no decisions, no emits; observation, bus logging and Governor polling continue. The mod keeps the last applied knobs (inside the safety floor). Idempotent. |
| `resume` | Leave S; the Governor re-evaluates its level on the next poll. Idempotent. |
| `force_strategy {strategy, months}` | Bypass the counter table for `months` game months (default 24) from the next tick; strategy_selected `reason=forced`, `reason_key=operator_forced`; outcomes of forced windows carry `forced=true`. `strategy=null` clears an active override (CLI `clear-force`). |
| `status` | Read-only; returns `EngineStatus`. Logged as a `control` event like the others. |

## 2. Transport: a file inbox (no network)

No port is opened (no network protocol before M5; nothing to authenticate). Per run:

```text
runs/<run_id>/control/inbox/<command_id>.json      written by sae ctl (atomic: .tmp + rename)
runs/<run_id>/control/done/<command_id>.json       the command, moved here by the daemon after handling
runs/<run_id>/control/done/<command_id>.result.json  ControlResult (atomic write)
```

1. `sae ctl` resolves the run: `--run <run_id>`, else the run named in `runs/CURRENT` (C8; first token). It
   writes nothing and exits 1 with `no running daemon` unless `runs/CURRENT` exists, its run_id equals the
   target run and its pid is alive (C8 §6 liveness test). `--run` naming any other run is refused the same way.
2. It writes a `ControlCommand` with `command_id = c-<UTC yyyymmddThhmmssZ>-<6 hex>`, `operator` =
   the OS user name, `reason` from `--reason`.
3. The daemon polls `inbox/` every 2 s (C7: nothing polls faster), handles files in `command_id`
   order (which is time order), at the next tick boundary for state-changing commands and immediately
   for `status`.
4. It writes the result, moves the command to `done/`, and appends one `control` bus event (C5) with
   both. Then `sae ctl` prints the result and exits 0 if `accepted`, else 1.
5. `sae ctl` waits up to `RESULT_TIMEOUT_S` (10 s) for the result; on timeout it exits 1 with
   `no result; is the daemon running?`. The command stays in the inbox; if a daemon of this session picks
   it up within `COMMAND_MAX_AGE_S` it is applied, otherwise it is rejected as `expired` (§3.1).

## 3. Validation and errors

| Case | Result |
|---|---|
| File larger than 64 KiB | `accepted=false, error="too_large"` (command_id `unknown` if unreadable) |
| Invalid JSON or schema | `error="invalid"`, `detail` = first validation error |
| Unknown `cmd` | `error="unknown_cmd"` |
| Daemon shutting down | `error="not_running"` |
| Command too old, from before this daemon session, or dated in the future (§3.1) | `error="expired"`, `detail` from `command_expired`; `command_id` echoed |
| `force_strategy` while suspended, or `resume` racing a `pause` in the same poll | applied in `command_id` order; a `force_strategy` while suspended is accepted and takes effect on resume |
| any command in release mode | accepted and logged; release mode emits no live intents (C3 `mode` rule), so `force_strategy` only changes `strategy_selected` on the bus |

### 3.1 Expiry (proposal 016-T6)

A command is valid for one daemon session and a short time. When the daemon reads a file from `inbox/` that
validates as a `ControlCommand`, it calls `command_expired(issued_at, now, session_started_at)`:

1. `issued_at < session_started_at` → expired. `session_started_at` is the wall-clock UTC time the daemon
   process opened the inbox (start or `--resume`). So every file left in `inbox/` by an earlier session is
   rejected at startup, including on `--resume` of the same run.
2. `now - issued_at > COMMAND_MAX_AGE_S` (60 s) → expired.
3. `issued_at - now > COMMAND_MAX_FUTURE_S` (5 s) → expired (a future date would dodge rule 2).

The check runs once, at pickup. A command picked up in time and waiting for the tick boundary is not
re-checked. An expired command is completed like any rejection: result written, file moved to `done/`,
one `control` event whose `command` is the parsed command (not `null`) and whose result has
`accepted=false, error="expired"`. Expiry is checked after size, JSON, schema and file-name checks, so
those errors win.

Every rejected command is still logged as a `control` event; when the file could not be read as a
`ControlCommand` (`invalid`, `too_large`, `unknown_cmd`) the event's `command` is `null` (C5 §3, proposal 005-T4). Inbox files are trusted only as far as
the local user account is: the inbox lives under the user's own `runs/` directory.

## 4. Examples

```json model=ControlCommand
{"v": 1, "command_id": "c-20261019T031800Z-a1b2c3", "issued_at": "2026-10-19T03:18:00Z",
 "operator": "danny", "reason": "check the siege doctrine", "command": {"cmd": "force_strategy",
 "strategy": "outscale_siege", "months": 24}}
```

```json model=ControlCommand
{"v": 1, "command_id": "c-20261019T031900Z-00ff00", "issued_at": "2026-10-19T03:19:00Z",
 "operator": "danny", "command": {"cmd": "force_strategy", "strategy": null}}
```

```json model=ControlResult
{"v": 1, "command_id": "c-20261019T032000Z-d4e5f6", "accepted": true, "applied_at": null,
 "status": {"run_id": "20261019-031500-live-0a1b", "game_date": "2231.05.01", "suspended": false,
            "state": "PLAYING", "level": "H1", "strategy": "outscale_siege", "forced_until": null,
            "commit_until": "2233.04.01", "top_archetype": "turtle", "in_flight_batches": 1}}
```

```json model=ControlResult
{"v": 1, "command_id": "unknown", "accepted": false, "error": "invalid",
 "detail": "Expecting value: line 1 column 1 (char 0)", "applied_at": null, "status": null}
```

```json model=ControlResult
{"v": 1, "command_id": "c-20261019T031800Z-a1b2c3", "accepted": false, "error": "expired",
 "detail": "issued before this daemon session started", "applied_at": null, "status": null}
```

Invalid: command outside the v0 set.

```json model=ControlCommand invalid
{"v": 1, "command_id": "c-20261019T031800Z-a1b2c3", "issued_at": "2026-10-19T03:18:00Z",
 "operator": "danny", "command": {"cmd": "override_intent", "knob": "econ", "value": 2}}
```

Invalid: naive timestamp.

```json model=ControlCommand invalid
{"v": 1, "command_id": "c-20261019T031800Z-a1b2c3", "issued_at": "2026-10-19T03:18:00",
 "operator": "danny", "command": {"cmd": "pause"}}
```

## 5. Versioning

v1.1 (proposal 016): `error="expired"` and the §3.1 rule. Additive; the command file format is unchanged.

Adding a command is a contract change (the advisor's cut means it needs a reason in the proposal).
Changing transport or file layout is `v = 2`.

## 6. UNVERIFIED assumptions

None about the game.
