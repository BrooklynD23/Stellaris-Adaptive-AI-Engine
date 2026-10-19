# sae-parse

Rust CLI for the SAE save parser (contract `contracts/C4_sae_parse.md`, plan `docs/plans/T3_plan.md`).
Build and test from this directory with `-j 1`:
`cargo clippy -j 1 --all-targets -- -D warnings`, `cargo test -j 1`.

## Usage

```text
sae-parse parse   --save <path> [--sections <s>[,<s>...]] [--threads <n>] [--background]
sae-parse fixture --spec <spec.json> --out <file.sav>
sae-parse --version
```

`fixture` renders a `FixtureSpec` (C4 section 5) to a synthetic save: `format = "zip_text"` (default) writes a
zip with entries `meta` then `gamestate` (Deflate, timestamp 1980-01-01 00:00); `"plain_text"` writes the bare
gamestate. Output is byte-deterministic. On success nothing is written to stdout (diagnostics go to stderr).
`parse` loads the save, extracts the requested sections and prints one `ParseResult` JSON line (see Section semantics).
`fixture` validation also rejects: `\` or control characters in `name`, `game_version`, `date` or a country name;
a `date` that is not zero-padded `YYYY.MM.DD` (month 01-12, day 01-30); a `name` not matching `^[a-z0-9_]+$`; an empty country name; a `sid` outside 1..=1,000,000; `applied_econ` over 4 or `applied_mil` over 3; negative or non-finite `fleet_power` / `naval_cap_used` / `naval_cap` (or non-finite energy, minerals, alloys, alloys_income); a war side with no countries; `pad_bytes` over 2 GiB; `tech_researched`, `defense_modules` or
`platforms` over 1,000,000; a self-contact `[a,a]`; a self-loop hyperlane `[a,a]`. A failed render removes the
partial `--out` file. Orbital ids come from one counter shared by all starbases.

## Exit codes

| Exit | Meaning | `fixture` cases |
|---|---|---|
| 0 | success | rendered |
| 2 | usage | bad args; malformed JSON; unknown field; `schema_version` != 1; empty countries; duplicate `country_id` or `sid`; more than one player; system owned twice; starbase system not owned by its owner; two spec starbases in one system; unknown country id in contacts, starbases or wars |
| 3 | input | spec unreadable; `--out` cannot be created or written |
| 4 | format | `parse`: not zip/text, zip without `gamestate`, over caps, unsupported zip method |
| 5 | parse | `parse`: extraction error |
| 6 | unstable | `parse`: size/mtime moved, truncated or corrupt zip, Windows os error 32/33 |

The fixture renderer replicates the pydantic range and regex checks (`name` pattern, date format, `sid` range,
`applied_econ` 0..4, ...); `tools/validate_fixtures.py` cross-checks them.

## Caps (parse side)

Zip file at most 256 MiB; declared `gamestate` and plain text at most 400 MiB (both exit 4); output at most
4 MiB (exit 5, `output exceeds 4 MiB`). `--threads` is validated (integer >= 1) and ignored: v1 is single-threaded.
The splitter and our walkers are iterative (no recursion), so `{` nesting depth cannot overflow the stack; jomini
sees one small span at a time (a country, a system, a starbase, a war) and the tape is dropped before the next.

## Hardening limits

| Limit | Value | On breach |
|---|---|---|
| Zip file size | 256 MiB (decided from the 4-byte header and stat size, before the file is read) | exit 4 |
| Zip entries | at most 64 (EOCD / zip64 EOCD count, checked before the archive is opened) | exit 4 |
| `gamestate` (zip entry) and plain text | 400 MiB | exit 4 |
| Single span handed to jomini | 32 MiB | exit 4, `span too large (N bytes > 32 MiB)` |
| Entries per `split` call | 4,000,000 | exit 4, `too many entries` |
| Country name | 256 chars (truncated at a char boundary) | truncated |
| `sae_sid` | 1..=1,000,000 | treated as no sid, `level=warn` on stderr |
| Countries sharing a sid | none allowed | all listed truthfully in `countries` (sid and managed as in the save), all dropped from `self_state` / `legal`, `level=warn` |
| stdout | 4 MiB | exit 5 |

Memory budget: jomini's token tape is about 10x its span, so one 32 MiB span bounds the tape near 320 MiB. Together
with the 400 MiB text buffer and overhead that stays under the 1 GiB Job commit cap (spans are parsed one at a time and
the tape is dropped between them).

Save dates are normalised like the contract `GameDate`: `Y.M.D` with 1-4 / 1-2 / 1-2 digits, month 1-12, day 1-30,
re-rendered as zero-padded `YYYY.MM.DD` (`2231.4.1` becomes `2231.04.01`); anything else is exit 5. The fixture
renderer stays strict and accepts only zero-padded dates.

`--sections` syntax: comma-separated section names with no spaces and no empty items (`meta,wars`; `meta,,wars`,
`meta,` and ` meta` are usage errors, exit 2). Duplicates collapse.

`save.path` in the output is `Path::to_string_lossy`: non-UTF-8 path bytes become U+FFFD, so it is lossy. Consumers
must key on the path they passed in, not on this field.

## Section semantics (plan section 3)

- `meta` always runs. `sections` in the output lists the requested sections plus `meta`, in C4 order.
- `self_state` and `god` have required numeric fields, so they are produced only when all six sections ran;
  otherwise `self_state = {}` and `god = null`, and stderr gets a `level=warn` line.
- `legal` is produced when `countries` ran (`in_contact` needs it). Without `countries`: `countries = []`,
  `self_state = {}`, `legal = []`, `god = null`. A feature whose section did not run is `null`:
  `systems_owned`, `frontier_systems`, `fortified_border_starbases` need `starbases`; `fleet_power_cat` needs
  `fleets`; `tech_cat` needs `tech`; `economy_cat` comes with `countries`; `at_war_with_observer` and
  `player_war_count` need `wars`. `fleets` and `tech` read fields inside the `country` block, so `country` is
  read when any of `countries`, `fleets`, `tech` is requested.
- Not in contact (either country's `relation` marks `contact=yes` toward the other): only `observer_sid` and
  `in_contact=false`. Observer mode (no player): `legal = []`, `god = null`. `legal` is sorted by `observer_sid`,
  `countries` by `country_id`. A missing or malformed `date` (not `Y.M.D` with month 1-12, day 1-30; meta wins over gamestate) is
  exit 5; other missing data is absent.
- Category thresholds: ratio r < 0.5 is -2, < 0.8 is -1, <= 1.25 is 0, <= 2 is +1, else +2; observer value 0 (or
  below): +2 if the player's value is above 0, else 0. A category is `null` unless BOTH the player's and the
  observer's value were found in the save (a missing key is not a zero).
- Missing and invalid values: every number is sanitised at extraction. A non-finite number, or a negative one in
  a field the model declares `ge=0` (`fleet_power`, `naval_cap_used`, `naval_cap`), counts as missing. Missing
  becomes **0 in `self_state` and `god`** (the model fields are required); in `legal`, features are `null`
  (never 0). The alloys income is a sum over sources; a non-finite term or sum counts as missing.
- `meta.game_version` is the literal sentinel `"unknown"` when neither the zip `meta` entry nor the gamestate
  carries a `version`.

## Save key map (all UNVERIFIED A-19/A-30, M0-11 unless noted)

Ids: A-18 (zip/text envelope, M0-11), A-19 (key locations, M0-11), A-20 (ironman, M0-14), A-30 (bridge
variables and flags, M0-11).

| Data | Key path (gamestate unless noted) | Section | Unverified |
|---|---|---|---|
| game version, date | gamestate top-level `version="..."`, `date="..."` (first two lines); the zip `meta` entry overrides when present | meta | A-18, M0-11 |
| ironman | `meta` entry `ironman=yes` or `ironman=no` (fixtures always write it) | meta | A-20, M0-14 |
| player | `player={ { name="..." country=<id> } }`; no player: `player={ }` | countries | A-19, M0-11 |
| country block | `country={ <id>={ name={ key="..." } ... } }` (parser also accepts `name="..."`) | countries | A-19, M0-11 |
| sid / seq / applied | `variables={ sae_sid=<n>.000 sae_seq_applied= sae_seq_pending= sae_applied_econ= sae_applied_mil= }` in country, only for countries with a sid | countries | A-30, M0-11 |
| managed | `flags={ sae_managed=<int> }` in country (only if managed; fixtures write `1`) | countries | A-30, M0-11 |
| contact | `relations_manager={ relation={ owner=<a> country=<b> contact=yes } ... }` in country, one repeated `relation=` per partner, written on both sides of a contact pair | countries | A-19, M0-11 |
| resources | `modules={ standard_economy_module={ resources={ energy= minerals= alloys= } } }` | countries | A-19, M0-11 |
| alloys income | `budget={ last_month={ income={ <src>={ alloys=<x> } ... } } }` summed over sources (fixtures write one source, `country_base`) | countries | A-19, M0-11 |
| naval cap | `used_naval_capacity=`, `naval_capacity=` in country | fleets | A-19, M0-11 |
| fleet power | `military_power=` in country | fleets | A-19, M0-11 |
| tech count | count of `technology=` entries in `tech_status={ technology="tech_sae_<i>" level=1 ... }` | tech | A-19, M0-11 |
| systems | `galactic_object={ <sys>={ hyperlane={ { to=<sys> } ... } starbases={ <sb> } } }`; every system mentioned anywhere is listed; hyperlanes written in both directions | starbases | A-19, M0-11 |
| ownership | `starbase_mgr={ starbases={ <sb>={ owner=<cid> level="starbase_level_outpost" modules={ 0=gun_battery ... } orbitals={ <id> ... } } } }`; a system is owned by the owner of its starbase; one outpost starbase per owned system (ids 1.. in ascending system id); a spec starbase fills that system's modules and platforms | starbases | A-19, M0-11 |
| defense modules / platforms | module names in {gun_battery, missile_battery, hangar_bay} counted (fixtures cycle them in that order); `orbitals` entries are platforms | starbases | A-19, M0-11 |
| wars | `war={ <i>={ attackers={ { country=<cid> } ... } defenders={ { country=<cid> } ... } } }`; block omitted when there are no wars | wars | A-19, M0-11 |
| padding | final top-level `sae_pad="xxx..."`; `pad_bytes` is exactly the number of `x` characters between the quotes (the key, quotes and LF are extra); omitted when 0 | none | A-18, M0-11 |
| M0-11 candidates, not used in v1 | `economy_power`, `tech_power` in country | none | A-19, M0-11 |

## Fixtures

`fixtures/<name>/{spec.json,save.sav,expected.json}`: `turtle_small` (C4 example), `tech_rush`,
`early_aggression`, `balanced`, `observer_mode`, `no_contact`, `war_two_fronts`, `plain_text_small`,
`pathological_2x` (256 MiB `pad_bytes`, zip, 0.25 MB on disk; exercises the splitter and size path only).
`expected.json` is the full `ParseResult` minus `cost`, `save` and `parser_version`.

Regenerate after a spec or extraction change: `cd parser && cargo build -j 1 && tools/regen_fixtures.sh`, then
`cd .. && uv run python3 parser/tools/validate_fixtures.py` (validates every spec with `FixtureSpec` and every
output with `PARSE_OUTPUT_ADAPTER`). `tests/golden.rs` compares `parse(save.sav)` and `parse(render(spec))` to
`expected.json` (never zip bytes). `tests/fuzz.rs` (proptest, fixed seed) and `tests/pathological.rs` (rss at most
768 MiB for `pathological_2x` and a generated 2000-country stress spec) run under plain `cargo test -j 1`.

## UNVERIFIED items (M0 checklist)

| Item | A-id | M0 test |
|---|---|---|
| Autosave is zip(`meta`, `gamestate`) Clausewitz text, parseable by jomini | A-18 | M0-11 |
| Key locations: player, country, relations, resources, budget, tech_status, galactic_object, starbase_mgr, war | A-19 | M0-11 |
| Fixed-point ints, `<id>=none`, repeated keys are the real value shapes | A-19 | M0-11 |
| `military_power` is fleet power; `economy_power`, `tech_power` may be better | A-19 | M0-11 |
| Alloys income is `budget.last_month.income.<src>.alloys`, summed; autosaves land at month start | A-19 | M0-11 |
| A system is owned by the owner of its starbase; defense modules and `orbitals` count as defenses | A-19 | M0-11 |
| `relation` blocks mark contact symmetrically enough for the either-side rule | A-19 | M0-11 |
| `ironman=yes` in the `meta` entry; null when there is no `meta` entry | A-20 | M0-14 |
| `variables.sae_*` and `flags.sae_managed` are saved | A-30 | M0-11 |
| Real save size and "2x largest save" padding and stress sizes | A-18 | M0-11 |

### Value shapes

- Floats are fixed-point with three decimals (`412.5` becomes `412.500`), like the game. Ids and counts that
  the game stores as plain integers are plain ints; bridge variables are written `3.000`.
- A dead country is rendered as `<maxid+1>=none` and must be skipped by the parser.
- `tech_status`, `relation`, `attackers` and `defenders` entries are repeated keys, never a map.
- Strings escape `"` and `\`; control characters become spaces.
- Multiple `player` entries (multiplayer): first wins.
