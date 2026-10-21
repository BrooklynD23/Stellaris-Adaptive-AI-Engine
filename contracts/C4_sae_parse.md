# C4 — `sae-parse` CLI, JSON output and synthetic save fixtures

| | |
|---|---|
| Version | 1 (`schema_version = 1` in every JSON document) |
| Status | FROZEN v1 (2026-10-19) |
| Owner | architect (contract); T3 implements |
| Producer | `sae-parse` (Rust, jomini; T3) |
| Consumers | Daemon parse step (T4) through the Governor's worker spawn (T5); ReplayAdapter and SynthAdapter (T2); fixtures for T7 |
| Code | `src/sae/contracts/c4_parse.py` — `ParseResult`, `ParseError`, `PARSE_OUTPUT_ADAPTER`, `FixtureSpec`, exit codes |

## 1. Command line

```text
sae-parse parse   --save <path> [--sections <s>[,<s>...]] [--threads <n>] [--background]
sae-parse fixture --spec <spec.json> --out <file.sav>
sae-parse --version            -> prints "sae-parse <semver>" and exits 0
```

- `--sections`: subset of `meta, countries, starbases, fleets, wars, tech`; default all. Unknown name → exit 2.
  Fields whose section was not extracted are `null`/absent per the schema; `sections` in the output lists what ran.
  `meta` is always extracted and listed. `self_state` and `god` are produced only when all six sections ran;
  otherwise `self_state = {}` and `god = null` (not observer mode — check `sections`), and a stderr
  `level=warn` line says so. Without `countries`: `countries = []`, `legal = []`. Each `LegalView` feature
  whose section did not run is `null`. The live loop always passes all sections (seq continuity, C3 §5).
  (Proposal 001-T3.)
  Meta-only (`--sections meta`) is the Ironman detection call (C6 §1.10, proposal 017; no new mode):
  `meta.ironman` comes from the zip `meta` entry only (`null` when the save has none, A-20). The parser
  still inflates and top-level-scans `gamestate` (for `date`/`version` fallback) but extracts no section;
  `countries = []`, `legal = []`, `self_state = {}`, `god = null`.
- `--threads`: default 1. The Governor passes the allowance (C7); the parser never uses more.
- `--background` (Windows): the worker's first action is `SetPriorityClass(GetCurrentProcess(),
  PROCESS_MODE_BACKGROUND_BEGIN)`, then EcoQoS via `SetProcessInformation`. Ignored on Linux (the
  spawner already applied `SCHED_IDLE`/`ionice`, C7 §8). Failure to set either is logged to stderr, not fatal.
- `fixture`: renders a `FixtureSpec` (§5) to a save file. Exit 0 on success, 2 on an invalid spec, 3 on I/O error.
- No network, no other files written, no environment variables read. Arguments are the only input.

## 2. Output and exit codes

stdout carries **exactly one JSON document followed by LF**, UTF-8, ≤ 4 MiB. stderr carries
diagnostics as `level=<lvl>|msg=<text>` lines (free-form, for humans; never parsed).

| Exit | Meaning | stdout |
|---|---|---|
| 0 | success | `ParseResult` (`status = "ok"`) |
| 2 | usage error | `ParseError` (`error = "usage"`) |
| 3 | input missing or unreadable | `ParseError` (`"input"`) |
| 4 | unsupported format (e.g. binary save needing a token table, unknown envelope) | `ParseError` (`"format"`) |
| 5 | parse error in a supported format | `ParseError` (`"parse"`) |
| 6 | file changed or truncated while reading (size/mtime moved, bad zip CRC) | `ParseError` (`"unstable"`) |
| anything else, or killed by signal, or allocation abort | crash / memory cap | undefined |

Daemon classification (signoff change 2), recorded as C7 `WorkerOutcome`:

- exit 0 **and** stdout validates as `ParseResult` → `ok`.
- exit 2–6 **and** stdout validates as `ParseError` → `clean_failure` → `health` `parse_failed`.
- anything else — other exit code, signal, timeout, stdout > 4 MiB, missing or invalid JSON → `budget_breach`.
  The Job memory limit makes allocations fail; Rust aborts; the daemon never waits for a Job message.

`cost` is self-measured: `rss_peak_bytes` (Windows `PeakWorkingSetSize`; Linux `getrusage(RUSAGE_SELF).ru_maxrss × 1024`),
`cpu_s` (user + system), `wall_s` (from process start to just before writing stdout).

## 3. What is extracted (normative definitions for T3)

Countries reported: the player (if any) and every country whose save variables include `sae_sid` (A-30).
`managed` = has the `sae_managed` flag. `self_state` is keyed by `str(sid)` for every managed country and
includes the bridge variables `sae_seq_applied`, `sae_seq_pending`, applied econ and posture codes, so
seq continuity and applied state survive `sae_quiet=1` and daemon restarts (C3 §5).
Observer mode (no player country): `legal = []`, `god = null`.
`observer_sid` is unique in `legal` (the model rejects duplicates). Countries sharing a `sae_sid` are
still listed truthfully in `countries` (sid and managed as in the save) but are excluded from
`self_state` and `legal`, with a stderr warning (proposal 003-T3).

`legal` holds one `LegalView` per managed country (the observer) about the player — the v0
**"diplomacy-screen legal" approximation** (report decision 3). If the observer is not in contact
with the player, every feature is `null` (never imputed). Otherwise:

| Field | Definition |
|---|---|
| `in_contact` | observer and player have established contact (A-19) |
| `systems_owned` | systems owned by the player (v0 approximation: borders are on the galaxy map once in contact; not an intel-level mask, A-19) |
| `frontier_systems` | player systems one hyperlane jump from an observer-owned system |
| `fortified_border_starbases` | player starbases in `frontier_systems` with defense modules + defense platforms > 0 |
| `fleet_power_cat` | ratio r = player fleet power / observer fleet power → −2 if r < 0.5; −1 if r < 0.8; 0 if r ≤ 1.25; +1 if r ≤ 2; +2 otherwise. Observer value 0: +2 if the player's value > 0, else 0 |
| `tech_cat` | same thresholds on researched-tech count ratio |
| `economy_cat` | same thresholds on monthly alloys income ratio |
| `at_war_with_observer` | a war has the player and the observer on opposite sides |
| `player_war_count` | wars the player is in |

Integer bound (proposal 007): every `LegalView` integer, `observer_sid`, `sid`, and `self_state` `applied_seq`/`pending_seq`
(also in `FixtureCountry`) are at most `CLAUSEWITZ_INT_MAX` = 1,000,000 (A-17). The parser must never
emit a larger value: a corrupt save value outside the range is exit 5 (`"parse"`), never clamped.

The thresholds are SAE's approximation of the diplomacy-screen categories, not the game's own
(A-19). `god` is the player's true state (`GodView`); it goes only to `bus.god.jsonl` (C6 §4).
`god = null` when no player country exists (observer mode).

## 4. Examples

Success:

```json model=ParseOutput
{
  "schema_version": 1, "status": "ok", "parser_version": "0.1.0",
  "save": {"path": "C:/Users/p/Documents/Paradox Interactive/Stellaris/save games/unitednations_1/autosave_2231.04.01.sav",
           "size_bytes": 18734211, "mtime_ns": 1792345678000000000},
  "meta": {"game_version": "Cepheus v4.4.4", "date": "2231.04.01", "ironman": false},
  "countries": [
    {"country_id": 0, "name": "United Nations of Earth", "sid": 1, "is_player": true, "managed": false},
    {"country_id": 7, "name": "Tzynn Empire", "sid": 3, "is_player": false, "managed": true},
    {"country_id": 9, "name": "Kel-Azaan Hegemony", "sid": 4, "is_player": false, "managed": true}
  ],
  "self_state": {
    "3": {"fleet_power": 8450, "naval_cap_used": 110, "naval_cap": 140, "energy": 412.5, "minerals": 1180,
          "alloys": 96.25, "alloys_income": 14.5, "systems_owned": 31, "wars": 1,
          "applied_seq": 16, "pending_seq": 17, "applied_econ": 1, "applied_mil": 2},
    "4": {"fleet_power": 3020, "naval_cap_used": 60, "naval_cap": 90, "energy": 120, "minerals": 800,
          "alloys": 40, "alloys_income": 6, "systems_owned": 18, "wars": 0,
          "applied_seq": 0, "pending_seq": 0, "applied_econ": 0, "applied_mil": 0}
  },
  "legal": [
    {"observer_sid": 3, "in_contact": true, "systems_owned": 41, "frontier_systems": 6,
     "fortified_border_starbases": 4, "fleet_power_cat": 1, "tech_cat": 1, "economy_cat": 0,
     "at_war_with_observer": false, "player_war_count": 0},
    {"observer_sid": 4, "in_contact": false}
  ],
  "god": {"fleet_power": 12010, "systems_owned": 41, "tech_researched": 58, "alloys_income": 40,
          "fortified_starbases": 9},
  "sections": ["meta", "countries", "starbases", "fleets", "wars", "tech"],
  "cost": {"rss_peak_bytes": 412000000, "cpu_s": 1.84, "wall_s": 2.31}
}
```

Clean failure:

```json model=ParseOutput
{"schema_version": 1, "status": "error", "parser_version": "0.1.0", "exit_code": 6, "error": "unstable",
 "detail": "zip central directory truncated; save still being written",
 "cost": {"rss_peak_bytes": 9000000, "cpu_s": 0.02, "wall_s": 0.03}}
```

Invalid: features present without contact (a fog leak).

```json model=ParseOutput invalid
{"schema_version": 1, "status": "ok", "parser_version": "0.1.0",
 "save": {"path": "x.sav", "size_bytes": 1, "mtime_ns": 1},
 "meta": {"game_version": "Cepheus v4.4.4", "date": "2231.04.01"},
 "countries": [], "self_state": {},
 "legal": [{"observer_sid": 4, "in_contact": false, "fleet_power_cat": 2}],
 "god": null, "sections": ["meta"], "cost": {"rss_peak_bytes": 1, "cpu_s": 0, "wall_s": 0}}
```

Invalid: exit code outside the clean-failure range.

```json model=ParseOutput invalid
{"schema_version": 1, "status": "error", "parser_version": "0.1.0", "exit_code": 70, "error": "parse", "detail": "x"}
```

## 5. Synthetic save fixtures

A fixture is a directory `parser/fixtures/<name>/` with:

- `spec.json` — a `FixtureSpec` (schema below). Hand-written or generated by the SynthAdapter (T2).
- `save.sav` — produced by `sae-parse fixture --spec spec.json --out save.sav` (T3 owns the renderer,
  so there is exactly one implementation of the save layout).
- `expected.json` — the `ParseResult` the parser must produce, compared after removing `cost`,
  `save` and `parser_version`.

Render rules: `format = "zip_text"` writes a zip with entries `meta` and `gamestate` (Clausewitz text,
A-18); `"plain_text"` writes the bare gamestate text. Key names follow the real 4.4 save layout as far as
T3 knows it; T3 keeps the key map in `parser/README.md`, and every key is UNVERIFIED until M0-11
compares a real autosave (A-19). `pad_bytes` appends an inert top-level key of that size for
memory-cap and size tests (CI-5 uses a fixture at 2× the largest real save).

Specs are strictly typed (proposal 002-T3): exact JSON types (no `3.0` for an int, no `"true"` or `1`
for a bool, no `"1.5"` for a float), ids ≥ 0, `pad_bytes` ≤ 2 GiB, no self-loop hyperlanes and no
self-contacts; the model enforces all of these. The renderer additionally rejects names containing `\`
or control characters and count fields > 1 000 000 (exit 2).

```json model=FixtureSpec
{
  "name": "turtle_small", "date": "2231.04.01",
  "countries": [
    {"country_id": 0, "name": "United Nations of Earth", "sid": 1, "is_player": true,
     "fleet_power": 12010, "tech_researched": 58, "alloys_income": 40, "systems": [1, 2, 3, 4]},
    {"country_id": 7, "name": "Tzynn Empire", "sid": 3, "managed": true, "fleet_power": 8450,
     "naval_cap_used": 110, "naval_cap": 140, "energy": 412.5, "minerals": 1180, "alloys": 96.25,
     "alloys_income": 14.5, "tech_researched": 50, "systems": [10, 11, 12]}
  ],
  "hyperlanes": [[4, 10], [1, 2], [2, 3], [3, 4], [10, 11], [11, 12]],
  "contacts": [[0, 7]],
  "starbases": [{"system": 4, "owner": 0, "defense_modules": 2, "platforms": 6}],
  "wars": []
}
```

Invalid: two players.

```json model=FixtureSpec invalid
{"name": "bad", "date": "2231.04.01", "countries": [
  {"country_id": 0, "name": "A", "is_player": true}, {"country_id": 1, "name": "B", "is_player": true}]}
```

## 6. Error handling inside the parser

- Never panic on malformed input: every error maps to exit 4, 5 or 6 with a `ParseError` document.
- Missing optional data inside a section → the field is absent/`null`, not an error.
- Read the file once into memory, check size and mtime before and after; a change → exit 6.
- Never write anything except stdout/stderr (and `--out` in `fixture`).

## 7. Versioning

`schema_version` is in every document. New optional output fields are non-breaking only after the
contract and models are updated (models forbid unknown fields). Removing or retyping a field,
changing a feature definition (§3), or changing an exit code meaning is a v2 change.

## 8. UNVERIFIED assumptions (M0 checklist)

| Id | Assumption | M0 test | Fallback |
|---|---|---|---|
| A-18 | 4.4 autosaves are zip(`meta`, `gamestate`) Clausewitz text that jomini parses in ≤ 3 s, ≤ 1 GB | M0-11 | Section-selective parsing; binary support if needed |
| A-19 | Contact, ownership, starbase modules, fleet power and tech count are present and locatable in the save | M0-11 | Drop the feature; mark `null` |
| A-20 | Ironman saves are parseable and `ironman` is detectable | M0-14 | `ironman = null` |
| A-24 | The autosave interval can be set to monthly for development | M0-11 | Yearly saves; log channel carries the monthly signal |
| A-30 | Country variables (`sae_sid`, `sae_seq_applied`, `sae_seq_pending`, applied econ/mil) and flags (`sae_managed`) are stored in the save | M0-11 | Map `sid` by `name` (C1); seq from the bus `state.json` |
