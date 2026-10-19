# T3 Parser plan — `sae-parse` (contract C4 v1)

Owner: T3 lead. Paths: `parser/**` only. Contract: `contracts/C4_sae_parse.md` (FROZEN v1), models in
`src/sae/contracts/c4_parse.py`. Every save key below is **UNVERIFIED (A-18/A-19/A-20/A-30, M0-11/M0-14)**.

## 1. Architecture (one binary, no threads)

```
main.rs      argv → Command; dispatch; writes exactly one JSON doc + LF; maps Error → exit 2..6
cli.rs       hand-rolled argv parser (no clap): parse|fixture|--version, --sections, --threads, --background
error.rs     enum Failure { Usage, Input, Format, Parse, Unstable } + detail (truncated to 2000 chars)
cost.rs      rss_peak_bytes / cpu_s / wall_s (unix: getrusage; windows: GetProcessMemoryInfo + GetProcessTimes)
background.rs cfg(windows): SetPriorityClass(PROCESS_MODE_BACKGROUND_BEGIN) then EcoQoS via SetProcessInformation;
             non-windows: no-op. Failures → stderr line, not fatal.
input.rs     stat → read whole file → stat; size/mtime moved → Unstable. Envelope detection:
             "PK\x03\x04" → zip (entries `meta`, `gamestate`; CRC error / truncated central dir → Unstable;
             missing gamestate → Format); anything else that is not binary → plain text; binary header
             (e.g. starts with a non-text magic, NUL bytes in first 4 KiB) → Format.
             Caps sized under the 1 GiB Job commit cap (adversary F1): zip file ≤ 256 MiB; declared
             `gamestate` size (central directory, read BEFORE allocating) ≤ 600 MiB; plain text ≤ 600 MiB;
             else Format (exit 4). Decompress with `Vec::with_capacity(declared)` + read through
             `take(declared+1)`; actual ≠ declared → Unstable. Drop the compressed buffer before splitting.
             Windows sharing violation (raw os error 32/33) on open/read → Unstable (game still writing;
             NTFS does not bump mtime until handle close). Any other zip error after a `PK` header → Unstable.
             Pin `zip >= 2`.
split.rs     top-level splitter over the gamestate bytes: returns spans of top-level `key = value`
             entries (brace depth + quote aware, iterative, never recursive). Only spans of keys needed by
             the requested sections are handed to jomini → section-selective memory.
extract.rs   jomini TextTape over each needed span, ONE span at a time, tape dropped before the next, → plain Rust structs (SaveModel). Iterative/limited
             recursion (depth cap 64 in our walkers; jomini tape itself is flat).
features.rs  pure functions SaveModel → countries, self_state, legal, god (C4 §3). Unit-testable without I/O.
output.rs    serde structs mirroring ParseResult / ParseError / Cost / ... field-for-field.
fixture.rs   FixtureSpec (serde, deny_unknown_fields, same validation as the pydantic model) → save bytes.
             Deterministic: fixed zip timestamps (1980-01-01), stable ordering, Deflate.
```

Dependencies: `jomini` (text), `zip` (default-features off, `deflate`), `serde`, `serde_json`,
`libc` (unix), `windows-sys` (windows, features Win32_System_Threading, Win32_System_ProcessStatus,
Win32_Foundation). Dev: `proptest`. `[profile.dev.package."*"] opt-level = 3` and dev `opt-level = 1`
so the size tests run in debug in seconds. `panic = "abort"` stays in release; tests use
`catch_unwind`-free design: no `unwrap`/`expect`/indexing on input-derived data (clippy
`unwrap_used`, `expect_used`, `indexing_slicing` denied in non-test code).

## 2. Save key map (T3-internal, UNVERIFIED until M0-11 — mirrored in `parser/README.md`)

| Data | Key path (gamestate unless noted) | Section |
|---|---|---|
| game version, date | gamestate top-level `version="…"`, `date="…"`; the zip `meta` entry overrides when present (F5) | meta |
| ironman | `meta` entry `ironman=yes` | meta |
| player | `player={ { name="…" country=<id> } }` | countries |
| country block | `country={ <id>={ name={ key="…" } … } }` (also accept `name="…"`) | countries |
| sid / seq / applied | fixed-point values, e.g. `sae_sid=3.000` (F4; int conversion accepts `3.000`, rejects non-integral → field null/skip) `variables={ sae_sid=<n> sae_seq_applied=<n> sae_seq_pending=<n> sae_applied_econ=<n> sae_applied_mil=<n> }` in country | countries |
| managed | `flags={ sae_managed=<date-int> }` in country | countries |
| contact | `relations_manager={ relation={ owner=<a> country=<b> contact=yes } … }` in country | countries |
| resources | `modules={ standard_economy_module={ resources={ energy= minerals= alloys= } } }` | countries |
| alloys income | `budget={ last_month={ income={ <src>={ alloys=<x> } … } } }` gross income summed over sources; fallback `current_month` (autosaves land at month start, F3) | countries |
| (M0-11 candidates, not used in v1) | `military_power`, `economy_power`, `tech_power` in country | — |
| naval cap | `used_naval_capacity=`, `naval_capacity=` in country | fleets |
| fleet power | `military_power=` in country | fleets |
| tech count | count of `technology=` entries in `tech_status={ technology="…" level=… … }` | tech |
| systems & ownership | `galactic_object={ <sys>={ hyperlane={ { to=<sys> } } starbases={ <sb> } } }` + `starbase_mgr={ starbases={ <sb>={ owner=<cid> modules={ 0=gun_battery … } orbitals={ … } } } }`; a system is owned by the owner of its starbase | starbases |
| defense modules / platforms | module names in {gun_battery, missile_battery, hangar_bay} counted; `orbitals` entries are platforms | starbases |
| wars | `war={ <id>={ attackers={ { country=<cid> } } defenders={ { country=<cid> } } } }` | wars |

Value shapes (F4): dead countries appear as `<id>=none` → skipped; `tech_status`, `relation`,
`attackers`/`defenders` entries are repeated keys → iterate all, never collapse to a map. Multiple
`player` entries (multiplayer) → first wins, stderr note. Fixture renderer emits the same shapes.

Ironman: `meta` `ironman=yes` → true; key absent in a zip save with a meta entry → false (fixture
writes `ironman=no` explicitly); no meta entry at all → `null` (A-20).

## 3. Section semantics (resolves a C4 gap within the current contract)

`meta` is always extracted (`ParseResult.meta` is required). `--sections` without `meta` still
parses meta but `sections` lists only what the caller asked plus `meta`.
`self_state` and `god` have non-optional numeric fields in the model, so they are produced **only
when all six sections ran**; otherwise `self_state = {}` and `god = null`. `legal` entries are always
produced when `countries` ran (in_contact needs it); each feature whose section did not run is `null`.
Without `countries`: `countries = []`, `self_state = {}`, `legal = []`, `god = null`.
Proposal 001-T3 asks the architect to write this into C4 §1 (doc-only, non-breaking), stating that
`self_state`/`god` require all six sections and the live loop always passes all (seq continuity, C3 §5).
A subset run writes a stderr `level=warn` line saying `self_state`/`god` were not produced.

Feature rules beyond C4 §3: a managed country without `sae_sid` is reported in `countries` only if it
has a sid (C4 rule) — so it never appears; `legal` is sorted by `observer_sid`, `countries` by
`country_id`, `self_state` keys by sid. Category thresholds use f64 ratios exactly as C4 §3.
Frontier: player systems with a hyperlane (either direction) to an observer-owned system.

## 4. Exit-code map (C4 §2)

| Condition | Exit |
|---|---|
| bad/missing args, unknown section, `--threads` not an integer ≥ 1, unknown subcommand | 2 usage |
| `--save` missing on disk / not a file / permission denied; fixture `--spec` unreadable | 3 input |
| fixture `--out` write failure | 3 input |
| not zip, not text; zip without `gamestate`; zip file > 256 MiB; declared gamestate or plain text > 600 MiB; unsupported zip compression method (review U1-6) | 4 format |
| jomini error; required `date` missing; malformed fixture spec JSON / failed validation | 5 parse (parse), 2 (fixture spec invalid per C4 §1) |
| size/mtime moved; zip CRC mismatch; truncated zip / central directory; any zip error after `PK`; declared ≠ actual size; Windows os error 32/33 | 6 unstable |
| serialized output would exceed 4 MiB (F12) | 5 parse, detail "output exceeds 4 MiB" |

Never panics on input. Exit 0 only with a document that would validate as `ParseResult`.

## 5. Work units (sequential: concurrency cap = 1 child at a time)

| Unit | Files | Tests that must pass |
|---|---|---|
| U1 skeleton | `Cargo.toml`, `src/{main,cli,error,cost,background,output,input}.rs`, `tests/cli.rs` | `--version`; every usage error → 2 + `ParseError` JSON; missing file → 3; random binary → 4; zip without gamestate → 4; file modified between stats (test hook via injected stat closure in unit test) → 6; truncated zip → 6; corrupted CRC → 6; stdout is exactly one JSON doc + LF; cost fields ≥ 0; `cargo check --target x86_64-pc-windows-gnu` |
| U2 fixture renderer | `src/fixture.rs`, `parser/README.md` (key map), `tests/fixture_render.rs` | spec validation: serde `deny_unknown_fields` + unique ids, ≤1 player, unique sids (range/regex left to `validate_fixtures.py`, F11) → exit 2; deterministic bytes (render twice, equal); zip and plain_text; pad_bytes exact inert key; spec starbase in a system not owned by its owner → 2; system owned twice → 2 |
| U3 extraction + features | `src/{split,extract,features}.rs`, parse pipeline in `main.rs`, `fixtures/**`, `tests/{golden,fuzz,pathological}.rs`, `tools/validate_fixtures.py` | golden (F8, no byte equality across dep versions): for every `fixtures/<name>`: parse(save.sav) == expected.json and parse(render(spec)) == expected.json minus cost/save/parser_version; unit tests for each C4 §3 threshold boundary (0.5, 0.8, 1.25, 2, observer 0); no-contact → all features null; observer mode; section subsets; proptest: random bytes and byte-mutated fixtures never panic, exit ∈ {0,4,5,6}; deep nesting (1e6 `{`) → exit 5 or 0, no stack overflow; unbalanced braces → 5; pathological pad fixture parses with `rss_peak_bytes` ≤ 1 GiB and `wall_s` reported; validate script: every fixture output validates with `PARSE_OUTPUT_ADAPTER` |

Fixtures (≥ 1 per archetype + specials): `turtle_small` (C4 example), `tech_rush`, `early_aggression`,
`balanced`, `observer_mode` (no player), `no_contact`, `war_two_fronts`, `plain_text_small`,
`pathological_2x` (pad_bytes 256 MiB, zip — compresses to ~0.3 MB; exercises the splitter/scanner
and size path only, F2; "2× largest real save" is UNVERIFIED A-18/M0-11).

Memory cap test (F2): `tests/pathological.rs` generates a token-heavy stress spec in-test (not
committed: 2 000 countries, 20 000 systems, 5 000 wars, starbases with modules), renders, parses with
the built binary and asserts exit 0 and `rss_peak_bytes ≤ 768 MiB` (headroom under the 1 GiB commit
cap). Linux measurable; Windows path not executed in CI here. Also asserts stdout ≤ 4 MiB. Re-size
the stress spec to 2× the M0-11 measurement when available.

## 6. Acceptance (exit gate)

In `parser/`: `cargo clippy -j 1 --all-targets -- -D warnings`, `cargo test -j 1`,
`cargo check -j 1 --target x86_64-pc-windows-gnu` clean. Repo: `uv run pytest -x -q`, `uv run ruff check`
green; `uv run python parser/tools/validate_fixtures.py` green (T7 asked to wrap it in a pytest).

## 7. Risks

| Risk | Mitigation |
|---|---|
| Real 4.4 key layout differs (A-19) | Key map isolated in `extract.rs` + README; every missing optional key → null/0 per C4 §6, not an error; M0-11 diff |
| Real saves binary-tokenised (A-18) | Exit 4 `format`, backlog binary support |
| Memory: whole-file read + decompressed text + tape | splitter tapes only needed sections; 1 GiB entry cap gives a clean exit 4 before the Job cap aborts |
| Zip bomb | `take(1 GiB)` on decompression + declared-size check |
| Windows-only code unexecuted | `cargo check` on windows-gnu; labelled "not executed in CI on this machine" |
| mtime granularity on NTFS/WSL | compare size and mtime_ns both; documented |

## 8. Expansion backlog

Binary save + token table; rayon with explicit `num_threads` (M2–M3); intel-level legal mask instead
of the diplomacy-screen approximation; dashboard parser cross-check; mmap instead of read (needs a
stability story); streaming zip to drop the compressed buffer early.

## 9. Adversary review log

Pass 1 (sae-adversary, 2026-10-19): accept with changes. All 12 findings applied:
F1 caps under Job cap + allocate-from-declared + one span at a time; F2 memory assertion moved to an
in-test token-heavy stress spec (768 MiB); F3 `last_month` income; F4 fixed-point vars, `none`
countries, repeated keys; F5 version/date from gamestate, meta overrides; F6 os error 32/33 → 6;
F7 subset warning + proposal wording; F8 no golden byte equality; F9 `chars().take(2000)`, zip ≥ 2;
F10 power keys listed as M0-11 candidates, first `player` wins; F11 renderer validation trimmed;
F12 oversize output → exit 5.
