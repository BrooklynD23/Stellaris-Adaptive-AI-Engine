# C1 — `SAE|v1|` log lines

| | |
|---|---|
| Version | 1 |
| Status | FROZEN v1 (2026-10-19; §5 PLYR redaction clarified by proposal 012) |
| Owner | architect (contract); T1 implements the producer |
| Producer | `sae_bridge` mod (T1); Python mod simulator (T2) |
| Consumers | LiveAdapter and ReplayAdapter log tailer (T2); Governor spm sensing via HB (T5, through T2) |
| Code | `src/sae/contracts/c1_log.py` — `parse_log_line`, `format_log_line`, record models |

## 1. Purpose

The mod's only data-out channel besides autosaves. Monthly hidden events print one line per record
with the `log` effect into `game.log`. The line carries a small feature vector, acks and a heartbeat.
Ground truth comes from the autosave (C4); log values are a fast, partial preview.

## 2. Grammar

```text
line      = [junk] "SAE|" version "|" type "|" date *( "|" field ) [ "|" name-field ] [CR] LF
junk      = any text not containing "SAE|v"          ; the game's own prefix (A-04), ignored
version   = "v1"
type      = "HELLO" / "HB" / "SNAP" / "PLYR" / "ACK"
date      = 1*4DIGIT "." 1*2DIGIT "." 1*2DIGIT      ; normalised to YYYY.MM.DD, month 1-12, day 1-30
field     = key "=" value
key       = %x61-7A *31( %x61-7A / DIGIT / "_" )   ; [a-z][a-z0-9_]{0,31}
value     = *( any char except "|" CR LF )
name-field= "name=" *( any char except CR LF )      ; MUST be last; may contain "|"
int       = ["-"] 1*DIGIT [ "." 1*"0" ]             ; "17" and "17.000" both mean 17
dec       = ["-"] 1*15DIGIT [ "." 1*15DIGIT ]       ; no exponent, no thousands separator; DIGIT = ASCII 0-9 only
```

Rules:

1. The consumer searches for the **first** `SAE|v` in the raw line and parses from there (a line without it is game noise).
2. Maximum 4096 characters from `SAE|` to end of line. Longer: error `too_long`.
3. Field order is free, except `name=` which is always last and runs to end of line.
4. A key may appear once per line. Unknown keys are kept verbatim in `extra` (forward-compatible);
   `extra` values must be ASCII. ACK `status`/`code` must be one of the pairs in the table below.
5. Lines are split on the byte `\n` only (never `str.splitlines()`, which also splits on U+2028 and
   friends inside names); a trailing `\r` is stripped. A final line without `\n` is buffered until completed.
6. Encoding: decode as UTF-8 with `errors="replace"` (A-16). Only `name` may contain non-ASCII.
7. `name` longer than 256 characters is truncated to 256, never rejected.

## 3. Record types

| Type | When | Required keys | Optional keys |
|---|---|---|---|
| `HELLO` | At game start, and when the mod first initialises in a running campaign (006 R6); on save load only if A-35 holds (mod 0.1.0 has no load hook, so a reloaded session may start with `HB`); **never suppressed** | `mod` (str), `schema` (int ≥ 1), `period` (1–12), `quiet` (0/1) | `mp` (0/1) |
| `HB` | Every game month, **never suppressed** by `sae_quiet` (signoff change 1) | `m` (int ≥ 0, mod month counter), `period`, `quiet` | — |
| `SNAP` | Per managed AI, staggered by slot: when `(m + sid) mod period == 0`; suppressed by `sae_quiet=1` | `sid`, `seq` (0..1,000,000, `sae_seq_applied`), `name` | `fleet`, `navcap_used`, `navcap`, `en`, `mi`, `al`, `al_inc` (dec); `wars` (int ≥ 0); `econ` (0–4), `mil` (0–3), `contact` (0/1) |
| `PLYR` | The player country, same stagger and quiet rule as SNAP | `sid`, `name` | same stats as SNAP (no `seq/econ/mil/contact`) |
| `ACK` | Once per (country, seq) the mod processes, including duplicates and rejections; **never suppressed** | `sid`, `seq` (1..1,000,000), `status`, `code` | `reason` (0–6, applied ReasonKey code); `applied` (0..1,000,000, `sae_seq_applied`; required by convention on DUPLICATE) |

- `sid` is the **SAE country id**: the mod assigns every country (player and AIs) a unique integer
  from a global running counter on its first monthly pulse, independent of emits and `sae_quiet`, and stores it in the country variable `sae_sid` (A-31). The same
  value is the stagger slot (resource proposal "first-emit slot counter"). `[This.GetID]` is not used
  (advisor: unverified for countries). The parser (C4) reads `sae_sid` from the save to map `sid` to
  the save's country id.
- `m` is the mod's month counter (global, +1 per monthly pulse). C2 expiry is expressed in `m`.
- Integer bound (proposal 007): `sid` (1..), `m` (0..) and every seq-valued key (`seq`, `applied`) are at most
  `CLAUSEWITZ_INT_MAX` = 1,000,000 (A-17). A larger value makes the line a parse error.
- `econ`/`mil` are the *applied* codes (C3 table); `0` means "never set by SAE".
- Mod 0.1.0 omits the optional `en`, `mi`, `al`, `al_inc` from SNAP/PLYR (no exportable source known,
  A-38; proposal 006 R9). Consumers must treat every optional stat as possibly absent.
- `status` ∈ `APPLIED, REJECTED, STALE, DUPLICATE, SCHEMA_MISMATCH`. `LOST` and `SUPERSEDED` are
  engine-only and are an error in a log line.
- `code` ∈ `OK, RANGE, NOT_MANAGED, BAD_TARGET, MULTIPLAYER, SEQ_OLD, EXPIRED, SCHEMA`
  (`TIMEOUT`, `NEWER_BATCH` are engine-only but parse, to keep one enum).

Status/code pairs the mod emits:

| status | code | Meaning |
|---|---|---|
| APPLIED | OK | knobs set, `sae_seq_applied := seq` |
| REJECTED | RANGE / NOT_MANAGED / BAD_TARGET / MULTIPLAYER | nothing applied; `sae_seq_applied := seq` (so the same batch is not retried) |
| STALE | EXPIRED | `m > sae_p_expires`; nothing applied; `sae_seq_applied := seq` |
| DUPLICATE | SEQ_OLD | `sae_seq_pending < sae_seq_applied` (an older file was run after a newer one); the mod resets `sae_seq_pending := sae_seq_applied`. `==` means already processed: no ACK (C2 §5) |
| SCHEMA_MISMATCH | SCHEMA | `sae_p_schema` ≠ mod schema; nothing applied; `sae_seq_applied := seq` |

## 4. Examples

Valid lines (each must parse):

```sae-log
SAE|v1|HELLO|2200.01.01|mod=0.1.0|schema=1|period=1|quiet=0
SAE|v1|HELLO|2230.06.01|mod=0.1.0|schema=1|period=3|quiet=0|mp=0
SAE|v1|HB|2231.04.01|m=375|period=1|quiet=0
[14:02:11][effect_impl.cpp:1234]: SAE|v1|HB|2231.4.1|m=375|period=1|quiet=0
SAE|v1|SNAP|2231.04.01|sid=3|seq=17|fleet=8450|navcap_used=110|navcap=140|en=412.5|mi=1180|al=96.25|al_inc=14.5|wars=1|econ=2|mil=3|contact=1|name=Tzynn Empire
SAE|v1|SNAP|2231.04.01|sid=4|seq=0.000|econ=0|mil=0|name=Kel-Azaan | Hegemony
SAE|v1|PLYR|2231.04.01|sid=1|fleet=12010|al_inc=40|wars=0|name=United Nations of Earth
SAE|v1|ACK|2231.05.01|sid=3|seq=17|status=APPLIED|code=OK|reason=1
SAE|v1|ACK|2231.05.01|sid=4|seq=17|status=REJECTED|code=BAD_TARGET
SAE|v1|ACK|2231.06.01|sid=3|seq=17|status=DUPLICATE|code=SEQ_OLD|applied=18
SAE|v1|HB|2231.05.01|m=376|period=1|quiet=1|newkey=ok
```

Invalid lines (each must raise `LogLineError`):

```sae-log invalid
SAE|v2|HB|2231.04.01|m=375|period=1|quiet=0
SAE|v1|HB|2231.04.01|m=375|period=13|quiet=0
SAE|v1|HB|2231.13.01|m=375|period=1|quiet=0
SAE|v1|SNAP|2231.04.01|sid=3|name=Missing seq
SAE|v1|SNAP|2231.04.01|sid=0|seq=1|name=Zero sid
SAE|v1|SNAP|2231.04.01|sid=3|seq=1|fleet=1,234|name=Thousands separator
SAE|v1|SNAP|2231.04.01|sid=3|seq=1|fleet=1e5|name=Exponent
SAE|v1|ACK|2231.04.01|sid=3|seq=17|status=LOST|code=TIMEOUT
SAE|v1|ACK|2231.04.01|sid=3|seq=17|seq=18|status=APPLIED|code=OK
SAE|v1|ACK|2231.04.01|sid=3|status=APPLIED|code=OK
SAE|v1|NOPE|2231.04.01|x=1
SAE|v1|HB
SAE|v1|HB|2231.04.01|m375
```

## 5. Consumer behaviour and error handling

| Situation | Consumer action |
|---|---|
| No `SAE|` in line | Ignore (game noise). |
| `LogLineError` (`bad_header`, `bad_field`, `dup_key`, `bad_value`, `invalid`, `too_long`) | Drop the line; count it; emit `health` `log_parse_error` with the code and the first 200 chars, at most once per game month per code. Never crash. **PLYR redaction** (proposal 012): if the line from its `SAE|` marker contains the substring `PLYR` anywhere, the detail is the code plus only `SAE|v1|PLYR`, followed by `|<date>` when the fourth `|`-field matches `YYYY.MM.DD` exactly; no other byte of the line. |
| `version_skew` (`SAE|v2|…`) | Drop; emit `health` `version_skew` once per session. If the line contains `PLYR`, the detail holds no byte of the line (e.g. `version != v1`). |
| File shrinks below the stored offset, or its identity changes (inode/ctime on Linux, creation time on Windows) — truncation or replacement on launch, A-15 | Reset offset to 0, emit `health` `reconnect`. |
| File missing | Retry every 2 s; `health` `reconnect` with detail `missing` once. |
| First `HB` when no `HELLO` has been seen since the adapter started (proposal 006) | Session start at the current `session_epoch` (no bump, no `hello` event); `period`/`quiet` come from the HB; `schema` is unknown, so batches are emitted and a skewed mod answers `SCHEMA_MISMATCH`. |
| `HELLO` | Session start: the adapter increments `session_epoch` (at most +1 per observation, even if a date regression arrives in the same poll) and records `period`/`quiet`/`schema`. If `schema` ≠ 1, emit `health` `version_skew` and do not emit batches. |
| HB date earlier than the previous HB date | Save reload: increment `session_epoch` (C6 `date_regressed`). |
| Duplicate ACK for the same (sid, seq, status) | Idempotent: the second one is ignored by the engine. |
| `PLYR` | God-view: routed to `observation_god` only (C6 §4). |

Byte offsets are tracked on the raw file; the tailer reads at most every 2 s (C7 poll).

## 6. Versioning

- `v1` is in every line. Adding a new **optional** key or a new `code` value is non-breaking within v1
  (consumers keep unknown keys in `extra`; unknown `code` values are a parse error, so adding one
  still needs a change proposal so T2 updates first).
- Removing a key, changing a type, adding a record type or changing semantics requires `v2`.
  Consumers drop lines whose version they do not know.

## 7. UNVERIFIED assumptions (M0 checklist)

| Id | Assumption | M0 test | Fallback |
|---|---|---|---|
| A-02 | The `log` effect exists in 4.4 and writes to `game.log` | M0-3 | Autosave-only observation (C4) |
| A-03 | Path is `<user_dir>/logs/game.log` | M0-3 | Config `paths.game_log` |
| A-04 | The game prefixes lines with its own timestamp/source text | M0-3 | Grammar already ignores any prefix |
| A-05 | Localisation date tokens print `Y.M.D` | M0-3 | Emit `m` only; adapter derives the date from the latest save + months |
| A-06 | `[This.<var>]` prints numbers as plain decimals | M0-5 | Bucketed flags + constant strings |
| A-07 | Flush to disk within 5 s of the month tick | M0-4 | Save channel cadence |
| A-14 | `on_monthly_pulse` / `on_game_start` event lists merge across mods (mod uses the global pulse, 006 R7) | M0-3 | `zz_sae_` override file |
| A-15 | `game.log` is truncated on game launch | M0-3 | Truncation handling covers both cases |
| A-16 | `GetName` may contain any character; file is UTF-8 | M0-3 | `name` last + `errors="replace"` |
| A-35 | An on_action fires on save load as well as on game start, so HELLO is printed after a reload (**not used by mod 0.1.0**) | M0-3 | HB date regression bumps `session_epoch`; first HB opens a session (§5) |
| A-36 | `event_target:global_event_country` exists at `on_game_start` (else at the first monthly pulse), holds variables, and they persist across save/load (anchor for `m`, sid counter, knobs) | M0-5, M0-11 | Dummy hidden country or first human country as anchor |
| A-38 | Fleet power, naval capacity used/max and war count export to country variables (`export_trigger_value_to_variable`) | M0-5 | Omit the stat (all stats are optional) |
| A-40 | `on_monthly_pulse` fires a global event once per game month on day 1; `on_game_start` fires once | M0-3 | — (no live loop without it) |
| A-42 | `log` strings resolve `[This.GetName]` and the date token (A-05) | M0-3 | Constant placeholder `name=?` (still valid v1; no version bump) |
| A-31 | Script can keep a global running counter for `sid` and a month counter `m` | M0-5 | `sid` from a per-country flag bucket |
