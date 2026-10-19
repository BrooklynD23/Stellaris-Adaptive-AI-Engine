# C2 — `sae_in.ini` command file

| | |
|---|---|
| Version | 1.2 (`sae_p_schema = 1`; wire template unchanged) |
| Status | FROZEN v1.2 (2026-10-19; v1.1 ship slots reserved, multiplayer write guard; v1.2 neutralise on close and on `mp=1`, §2.1, proposal 017) |
| Owner | architect (contract) |
| Producer | Engine compiler (T4) via `render_ini`; written atomically by the LiveAdapter (T2) |
| Consumers | Stellaris console `run sae_in.ini`, pressed by the player (hotkey-only in v0); `sae_bridge` monthly `sae.0` (T1); Python mod simulator (T2) |
| Code | `src/sae/contracts/c2_ini.py` — `CountryCommand`, `KnobCommand`, `IniFile`, `render_ini`, `parse_ini` |

## 1. Purpose

The only live data-in channel. The engine writes console commands that **stage** knob values into
country variables. Nothing changes in game until the mod's monthly `sae.0` event finds
`sae_seq_pending > sae_seq_applied`, validates, applies and prints an `ACK` (C1). No planner ever
writes this text: only `render_ini` does, from a validated `IntentBatch` (C3).

## 2. Location and write protocol

- Path: `<user_dir>/sae_in.ini`, with `user_dir` detected per C8 §3 (Windows Documents known folder,
  Linux `~/.local/share/Paradox Interactive/Stellaris`); config `paths.ini_path` overrides.
  The `.ini` extension is required (advisor, wiki checked); the directory `run` reads from is A-08.
- Atomic write: write `sae_in.ini.tmp` in the same directory, flush, `fsync`, then `os.replace` onto
  `sae_in.ini`. On Windows `os.replace` can fail while the game holds the file: retry 3 times at
  100 ms, 200 ms, 400 ms. Still failing: `EmitReceipt(ok=false, error="write_failed")` and `health`
  `ini_write_failed`. Three consecutive failed batches raise `health ack_alarm` (detail
  `ini_write_failed`); the engine keeps deciding and retries the write with the next batch. Level S is
  entered only by the operator (C7 §3).
- The file always holds exactly **one batch** (the latest). A newer batch overwrites an unrun one;
  the overwritten batch's intents become `SUPERSEDED` (C3 §5). Because every country line restates
  the full desired knob set (C3 §3), nothing is lost by overwriting.
- The engine never deletes the file. Running a stale file again is harmless (`DUPLICATE` or `STALE` acks).

### 2.1 Neutral file (maintainer decision 2026-10-19, Wave 3 N3, proposal 017)

A file left behind by an exited daemon could steer an unrelated AI in a later campaign (sids restart
at 1) or a multiplayer game. So the adapter **neutralises** it: it overwrites `sae_in.ini` with a
**knob-only** file through the same atomic write path (§2: `.tmp`, `fsync`, `os.replace`, the same
3 retries). It never deletes the file.

- **Content:** exactly one line, the knob line with both knobs, `period` then `quiet`:
  `effect set_variable = { which = sae_emit_period value = <P> } set_variable = { which = sae_quiet value = <Q> }`
  i.e. `render_ini(IniFile(knobs=KnobCommand(emit_period=P, quiet=Q)))`. `P`/`Q` are the `period`/`quiet`
  of the most recent C1 `HB` or `HELLO` the adapter read (any epoch), so running the file in the same
  game changes nothing; before any such line, `P = 1`, `Q = 0` (the mod defaults, `BridgeConfig`).
- **No country lines, no seq.** A knob-only file has no `sae_seq_pending` (§3 rule 6): running it
  produces no ACK and cannot touch any `sae_managed` country. The engine's next seq is unaffected
  (seq floor rule, C3 §5); a later batch overwrites the neutral file as usual.
- **When** (C6 §1.9): on adapter `close()` (clean daemon exit, including SIGTERM), and once each time
  the adapter reads a `HELLO` with `mp=1` while its previous `mp` was not 1. Never in `release`
  mode. Ironman observe-only (C6 §1.10) blocks the `mp=1` write but not the `close()` write (maintainer,
  2026-10-19).
- **Visibility:** C5 `health ini_neutralised` (component `adapter`, detail `close` or `multiplayer`)
  on success; on failure after the retries, `health ini_write_failed` with detail
  `neutralise <close|multiplayer>: <cause>`. Neither is an `EmitReceipt` or `intent_emitted`, and
  neither counts toward the three-failure `ack_alarm`.
- Residual risk: the knob line is still a console `effect`, so running even the neutral file in
  multiplayer desyncs the issuing client (§5). Neutralising removes the AI-steering payload, not that.

## 3. Grammar

```text
file          = [ knob-line LF ] *( country-line LF )      ; >= 1 line, <= 64 lines, ASCII only
knob-line     = "effect " setvar *( " " setvar )            ; sets sae_emit_period then sae_quiet, either optional
country-line  = "effect every_country = { limit = { has_country_flag = sae_managed "
                "check_variable = { which = sae_sid value = " sid " } } "
                setvar-list " }"
setvar        = "set_variable = { which = " var " value = " int " }"
setvar-list   = 14 setvars, exactly in this order, single spaces:
                sae_p_schema sae_p_expires sae_p_reason sae_p_econ sae_p_mil sae_p_mil_target
                sae_p_dip sae_p_dip_target sae_p_ship_corvette sae_p_ship_frigate
                sae_p_ship_destroyer sae_p_ship_cruiser sae_p_ship_battleship sae_seq_pending
int           = ["-"] 1*DIGIT                               ; integers only, |x| <= 1,000,000 (A-17)
```

Rules:

1. Lines end with LF; the file ends with LF; no comments, no blank lines (comment support in `run`
   files is unknown, so none are written). `parse_ini` also accepts CRLF.
2. Country lines are sorted by ascending `sid`, unique, and all carry the same `sae_seq_pending`.
3. Each line ≤ 2048 characters (A-11). A full country line is about 900 characters.
4. `sae_seq_pending` is the **last** effect on a country line: it is the commit marker.
5. `-1` means **keep** the currently applied value of that knob.
6. A knob-only file (no country lines) has no seq and no ACK; the next `HB` line (C1) shows the
   applied `period`/`quiet`.

Variable meanings and ranges (the mod re-validates every one; out of range → `REJECTED/RANGE`):

| Variable | Range | Meaning |
|---|---|---|
| `sae_p_schema` | 1 | Intent schema version; mismatch → `SCHEMA_MISMATCH` |
| `sae_p_expires` | 0..1,000,000 | Last mod month `m` at which this batch may still apply; later → `STALE/EXPIRED` |
| `sae_p_reason` | 0..6 | ReasonKey code (C3); 0 = no flavour event |
| `sae_p_econ` | -1, 1..4 | EconPlan code |
| `sae_p_mil`, `sae_p_mil_target` | -1 or 1..3 with target sid ≥ 1 | Posture code and target; both -1 or both set |
| `sae_p_dip`, `sae_p_dip_target` | -1 or 1..4 with target sid ≥ 1 | Stance code and target; both -1 or both set |
| `sae_p_ship_<size>` | -1 (reserved; the mod still accepts 0..100) | Hull-size bias slot. The engine always writes -1 and `parse_ini` rejects any other value (C3 v1.1: fleet bias dropped until M0-7, A-22) |
| `sae_seq_pending` | 1..1,000,000 | Batch seq |
| `sae_emit_period` | 1..12 | C7 in-game knob, set on the console-scope country |
| `sae_quiet` | 0, 1 | C7 in-game knob, set on the console-scope country |

A target must be another country (not 0, not the line's own `sid`); the mod answers a self-target
with `REJECTED/RANGE` (006 R1). The mod additionally rejects a target sid that matches no living
country (`BAD_TARGET`) and any application when multiplayer (`MULTIPLAYER`, A-13).

## 4. Examples

A batch with an econ and posture change for sid 3 and a heartbeat for sid 4 (this is the
compiled form of the C3 example `batch-17`; the test checks the mapping byte for byte):

```sae-ini compiled-from=batch-17
effect every_country = { limit = { has_country_flag = sae_managed check_variable = { which = sae_sid value = 3 } } set_variable = { which = sae_p_schema value = 1 } set_variable = { which = sae_p_expires value = 378 } set_variable = { which = sae_p_reason value = 1 } set_variable = { which = sae_p_econ value = 2 } set_variable = { which = sae_p_mil value = 3 } set_variable = { which = sae_p_mil_target value = 1 } set_variable = { which = sae_p_dip value = -1 } set_variable = { which = sae_p_dip_target value = -1 } set_variable = { which = sae_p_ship_corvette value = -1 } set_variable = { which = sae_p_ship_frigate value = -1 } set_variable = { which = sae_p_ship_destroyer value = -1 } set_variable = { which = sae_p_ship_cruiser value = -1 } set_variable = { which = sae_p_ship_battleship value = -1 } set_variable = { which = sae_seq_pending value = 17 } }
effect every_country = { limit = { has_country_flag = sae_managed check_variable = { which = sae_sid value = 4 } } set_variable = { which = sae_p_schema value = 1 } set_variable = { which = sae_p_expires value = 377 } set_variable = { which = sae_p_reason value = 0 } set_variable = { which = sae_p_econ value = -1 } set_variable = { which = sae_p_mil value = -1 } set_variable = { which = sae_p_mil_target value = -1 } set_variable = { which = sae_p_dip value = -1 } set_variable = { which = sae_p_dip_target value = -1 } set_variable = { which = sae_p_ship_corvette value = -1 } set_variable = { which = sae_p_ship_frigate value = -1 } set_variable = { which = sae_p_ship_destroyer value = -1 } set_variable = { which = sae_p_ship_cruiser value = -1 } set_variable = { which = sae_p_ship_battleship value = -1 } set_variable = { which = sae_seq_pending value = 17 } }
```

A knob line followed by one country line (Governor H3 request plus a diplomatic stance):

```sae-ini
effect set_variable = { which = sae_emit_period value = 12 } set_variable = { which = sae_quiet value = 0 }
effect every_country = { limit = { has_country_flag = sae_managed check_variable = { which = sae_sid value = 5 } } set_variable = { which = sae_p_schema value = 1 } set_variable = { which = sae_p_expires value = 400 } set_variable = { which = sae_p_reason value = 0 } set_variable = { which = sae_p_econ value = -1 } set_variable = { which = sae_p_mil value = -1 } set_variable = { which = sae_p_mil_target value = -1 } set_variable = { which = sae_p_dip value = 3 } set_variable = { which = sae_p_dip_target value = 1 } set_variable = { which = sae_p_ship_corvette value = -1 } set_variable = { which = sae_p_ship_frigate value = -1 } set_variable = { which = sae_p_ship_destroyer value = -1 } set_variable = { which = sae_p_ship_cruiser value = -1 } set_variable = { which = sae_p_ship_battleship value = -1 } set_variable = { which = sae_seq_pending value = 18 } }
```

A knob-only file:

```sae-ini
effect set_variable = { which = sae_quiet value = 1 }
```

The neutral file (§2.1) after the last `HB` read `period=3|quiet=0`:

```sae-ini
effect set_variable = { which = sae_emit_period value = 3 } set_variable = { which = sae_quiet value = 0 }
```

Invalid: posture set without a target.

```sae-ini invalid
effect every_country = { limit = { has_country_flag = sae_managed check_variable = { which = sae_sid value = 3 } } set_variable = { which = sae_p_schema value = 1 } set_variable = { which = sae_p_expires value = 378 } set_variable = { which = sae_p_reason value = 0 } set_variable = { which = sae_p_econ value = -1 } set_variable = { which = sae_p_mil value = 3 } set_variable = { which = sae_p_mil_target value = -1 } set_variable = { which = sae_p_dip value = -1 } set_variable = { which = sae_p_dip_target value = -1 } set_variable = { which = sae_p_ship_corvette value = -1 } set_variable = { which = sae_p_ship_frigate value = -1 } set_variable = { which = sae_p_ship_destroyer value = -1 } set_variable = { which = sae_p_ship_cruiser value = -1 } set_variable = { which = sae_p_ship_battleship value = -1 } set_variable = { which = sae_seq_pending value = 19 } }
```

Invalid: `sae_seq_pending` not last (variables out of order).

```sae-ini invalid
effect every_country = { limit = { has_country_flag = sae_managed check_variable = { which = sae_sid value = 3 } } set_variable = { which = sae_seq_pending value = 19 } set_variable = { which = sae_p_schema value = 1 } set_variable = { which = sae_p_expires value = 378 } set_variable = { which = sae_p_reason value = 0 } set_variable = { which = sae_p_econ value = -1 } set_variable = { which = sae_p_mil value = -1 } set_variable = { which = sae_p_mil_target value = -1 } set_variable = { which = sae_p_dip value = -1 } set_variable = { which = sae_p_dip_target value = -1 } set_variable = { which = sae_p_ship_corvette value = -1 } set_variable = { which = sae_p_ship_frigate value = -1 } set_variable = { which = sae_p_ship_destroyer value = -1 } set_variable = { which = sae_p_ship_cruiser value = -1 } set_variable = { which = sae_p_ship_battleship value = -1 } }
```

Invalid: free console text (anything that is not a C2 template).

```sae-ini invalid
effect add_resource = { energy = 1000 }
```

Invalid: knob value out of range.

```sae-ini invalid
effect set_variable = { which = sae_emit_period value = 13 }
```

## 5. Mod-side semantics (`sae.0` apply step, T1; the mod simulator reproduces this exactly)

Monthly, for each country with `sae_sid` and `sae_seq_pending` set, with `P = sae_seq_pending` and
`A = sae_seq_applied`, the first matching rule wins (order fixed by proposal 006; normative detail in
`mod/sae_bridge/SEMANTICS.md` §6):

0. `P` outside 1..1,000,000 → `sae_seq_pending := A`; no ACK (an ACK would violate C1 `seq ≥ 1`; R2).
1. If `P == A` → already processed; do nothing, print nothing.
   If `P < A` (an older file was run after a newer one) → print
   `ACK … seq=<pending>|status=DUPLICATE|code=SEQ_OLD|applied=<sae_seq_applied>`, set `sae_seq_pending := sae_seq_applied`; stop.
   (Re-running the *same* file therefore produces no second ACK.)
2. The country lacks `sae_managed`, or is now played by a human (`play`, hot-join) → `REJECTED/NOT_MANAGED` (R3).
3. If `is_multiplayer` (A-13) → `REJECTED/MULTIPLAYER`.
4. If `sae_p_schema != 1` → `SCHEMA_MISMATCH/SCHEMA`.
5. If mod month `m > sae_p_expires` → `STALE/EXPIRED` (`m == expires` still applies).
6. Range-check every `sae_p_*` (an unset staged variable reads as 0, A-41); any failure, including a
   self-target → `REJECTED/RANGE`. Target sid matches no living country → `REJECTED/BAD_TARGET`.
7. Apply every knob whose value is not -1 (flags, ship-size values, opinion modifier), all or nothing.
   If `sae_p_reason > 0` and differs from the last applied reason, fire the flavour event
   `sae.10` with localisation key `sae_reason_<name>`, at most one popup per human per pulse (R10).
8. Set `sae_seq_applied := sae_seq_pending` and print `ACK … status=APPLIED|code=OK|reason=<r>`.
   Steps 2–6 also set `sae_seq_applied := sae_seq_pending` after printing their ACK, so a rejected
   batch is acked exactly once.

`sae_managed` is sticky: once set it is never removed (R4). Reloading an earlier save reverts
`sae_seq_applied` and `m`, so re-running a file the engine already holds as terminal can print a
second terminal ACK for that seq; consumers take the last event per (country, seq) (C3 §5) and the
seq floor rule never lowers the next seq.

Knob lines (`sae_emit_period`, `sae_quiet`) set variables on the console-scope country, i.e. the
player country (A-09); the mod reads both knobs from the player country and copies them into its own
state on the next monthly pulse, before that pulse's `HB` and stagger, so the next `HB` shows them (R8).
A knob value outside its range or non-integer is ignored silently and the previous value kept (R5).
In multiplayer knobs are not read. In observer mode there is no player
country and knobs cannot be changed (they keep their last values).

The mod never applies a value it did not range-check, and every knob is additive (C3 §6, safety floor).

Multiplayer: console effects are local, so running the file in multiplayer desyncs the issuing client
before the mod can answer `REJECTED/MULTIPLAYER`. Live mode is single-player only. **Engine guard
(maintainer 2026-10-19, C6 §1.6):** the adapter never writes `sae_in.ini` while the current session's
`HELLO` reported `mp=1`; `emit()` returns `EmitReceipt(ok=false, error="multiplayer")`. It never
deletes the file; since v1.2 it rewrites it once with the neutral file (§2.1) on entering `mp=1`, so a
batch left over from a single-player session no longer carries country lines into the multiplayer game.

Fleet bias: dropped from v0 (C3 v1.1). The five `sae_p_ship_<size>` slots stay in the template so the
mod and `sae_p_schema = 1` are untouched; the engine writes -1 in all of them.

## 6. Versioning

The template is fixed for v1. Any new variable, reordering, or a new line template is a v2 change:
`sae_p_schema` becomes 2 and an old mod answers `SCHEMA_MISMATCH`, so skew is visible, not silent.

v1.2 (§2.1) adds a rule for when the engine writes, not a new template: the neutral file is an
ordinary knob-only file a v1 mod already accepts.

v1.1 changes no byte a v1 mod could see differently: the ship slots keep their place and range, the
engine just only writes -1 there; `parse_ini` (engine tests, T2 simulator) narrows to match. Removing
the slots would be a v2 template change plus a mod change, then another at M0-7 re-entry, so they stay.

## 7. UNVERIFIED assumptions (M0 checklist)

| Id | Assumption | M0 test | Fallback |
|---|---|---|---|
| A-08 | `run sae_in.ini` reads `<user_dir>/sae_in.ini` and executes each line as a console command | M0-6 | Config path; document the exact folder |
| A-09 | Console `effect` runs in the player-country scope and `every_country = { limit … }` works from it; mod script can address the player country from its monthly events to read the knobs | M0-6 | `event sae.50 <target>` with payload pre-staged in global variables |
| A-10 | `check_variable = { which = sae_sid value = N }` is an equality test and `set_variable` works from console in 4.4 | M0-6 | Per-sid flags `sae_sid_<N>` in the limit |
| A-11 | `run` accepts lines of ≥ 2048 characters and LF-only line endings on Windows | M0-6 | Split a country line into several `effect` lines with `sae_seq_pending` only on the last |
| A-12 | `run` executes all lines in order, mid-tick, without visible stutter | M0-6 | Player runs the file while paused |
| A-13 | An `is_multiplayer`-style trigger exists | M0-6 | Document: live mode is single-player only |
| A-17 | Variables hold integers up to ±1,000,000 exactly | M0-5 | Lower the cap; wrap `seq` |
| A-37 | Variable arithmetic/comparison with variable operands and cross-scope reads (`set/change/subtract/modulo_variable`, `check_variable` `<`/`>`/`>=`/`<=`, `is_variable_set`) | M0-5 | Per-country countdown for the stagger (SEMANTICS v2) |
| A-39 | `is_country_type = default`, `is_ai`, `every_country`/`any_country` over all living countries, `save_event_target_as`, `country_event` from a global event, opinion-modifier scoping | M0-3, M0-6 | Per construct in `mod/sae_bridge/UNVERIFIED.md` |
| A-41 | An unset variable reads as 0; `check_variable` accepts negative literals | M0-5, M0-6 | Initialise every variable explicitly |
