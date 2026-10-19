# T8 plan — Release, docs, M0 kit

Owner: T8 lead. Paths: `docs/**` (not TEAM_PLAN, not other teams' plans/status/components), `README.md`,
`CHANGELOG.md`, `changelog.d/**` (merge only, Wave 3), `packaging/**`, `tools/m0/**`, `src/sae/m0/**`,
tests in `tests/unit/t8/`. Contracts consumed read-only: C1 (log parsing), C2 (test ini), C4 (parse output),
C8 (paths, presets, CLI entry `sae.m0.cli.main(command, argv) -> int`), README §5 registry.

## 1. Goal

The maintainer runs the M0 spike on their own PC in one weekend and ends with `m0_results.md` that answers the
go/no-go rule (design report "Milestones"; integration plan "Go/no-go"). Nothing in the kit launches Stellaris,
writes into the install folder, the user `mod/` folder or `<user_dir>` (not even `sae_in.ini`). Every step that
touches the game is printed as an exact manual instruction; the kit then reads the evidence read-only.

## 2. Work units

| Unit | Files | Content | Tests |
|---|---|---|---|
| U1 `sae m0` | `src/sae/m0/{checks,evidence,results,tail,cli}.py` | check registry; read-only analyzers; per-check result JSON; timestamping tailer; CLI | `tests/unit/t8/test_m0_*.py` |
| U2 checklist | `docs/m0_checklist.md`, `tools/m0/README.md` (weekend runbook), `tools/m0/m0_results_template.md` | one row per A-id + per M0 test | `tests/unit/t8/test_checklist_drift.py` |
| U3 docs | `README.md`, `docs/user_guide.md`, `docs/developer_guide.md` | install, run, presets, Ironman, MP, troubleshooting; contracts, protocol, tests | `tests/unit/t8/test_docs_refs.py` |
| U4 release | `packaging/PLAN.md`, `packaging/merge_changelog.py`, `CHANGELOG.md` | shipping plan; fragment merge tool (not run until Wave 3) | `tests/unit/t8/test_merge_changelog.py` |
| U5 hand-back | `docs/status/T8.md`, `docs/components/T8.md`, `changelog.d/T8.md` | | final gate |

U1 first (largest); U2 needs U1's check ids; U3/U4 independent. One child at a time (orchestrator cap).

## 3. `sae m0` CLI (U1)

Results directory: `--results <dir>`, default `./m0_results/`. Never inside `user_dir`, mod or install folder:
the CLI refuses a results dir under any configured/detected game path (exit 2).

| Command | Does | Writes |
|---|---|---|
| `sae m0 list` | all checks: id, tier (core/stretch/later), A-ids, status from results | nothing |
| `sae m0 steps <ID>` | exact manual steps (paths filled from C8 detection or `--user-dir`), evidence to collect, pass rule, fallback | nothing |
| `sae m0 init --game-version V --checksum C [--starnet-build B] [--playset P]` | session header | `env.json` |
| `sae m0 stage M0-6 --sid N --m M` | renders a contract-valid test `sae_in.ini` with `render_ini` | `<results>/stage/sae_in.ini` + printed copy command |
| `sae m0 check <ID> [evidence flags]` | automatic analyzer → pass/fail/inconclusive | `<results>/<ID>.json` |
| `sae m0 tail [--game-log P] [--seconds N]` | M0-4 timestamping tailer: prints each `SAE|` line with wall time, months per poll, s/month | `<results>/M0-4.tail.jsonl`, then `M0-4.json` |
| `sae m0 record <ID> pass\|fail\|skip [--note T] [--value k=v]` | manual verdicts (eyeball checks, A/B numbers) | `<results>/<ID>.json` (merges with auto evidence) |
| `sae m0 summary` | builds `m0_results.md`: header, table per check, go/no-go verdict per the rule | `<results>/m0_results.md` |

Automatic analyzers (read-only):

| Check | Input | Pass rule |
|---|---|---|
| M0-1 | `--starnet-dir` (Workshop folder) | `str_var_ai_ship_data_factor_` found; records `supported_version` |
| M0-2 | `<user_dir>/logs/error.log` | zero lines matching `sae` (case-insensitive) |
| M0-3 | `game.log` | ≥ 1 HELLO and ≥ 1 HB parse with `parse_log_line`; one HB per game date (A-40); records prefix (A-04), raw date token and zero-padding (A-05, T3 M0-11 note), name resolved, not literal `[This.GetName]` (A-42), UTF-8 decode errors (A-16), `mp` value (A-13) |
| M0-5 | `game.log` | ≥ 1 SNAP with numeric `fleet`, plain decimals (A-06), `sid` > 0 (A-31) |
| M0-4 | live tail | no poll delivers HB lines of ≥ 2 game months while median s/month ≥ 1 s, plus manual "paused-on-the-1st" confirmation |
| M0-6 | `game.log` after the maintainer's `run sae_in.ini` | ACK for staged (sid, seq) with `APPLIED/OK` |
| M0-11 | `--save <autosave>`, `sae-parse` from C8 | exit 0, output validates as `ParseOutput`, wall ≤ 3 s; prints player fleet power/resources/tech count for the UI comparison (manual confirm via `record`) |
| M0-14 | `--save ironman.sav` + `game.log` | `meta.ironman` true; SAE lines present or not (records, not pass/fail on log) |

Everything else (M0-7, M0-8, M0-9, M0-10, M0-12, M0-13, M1-P, R-x, Windows platform items) is `record` only;
`steps` prints the procedure.

## 4. Checklist (U2)

`docs/m0_checklist.md` (path from TEAM_PLAN): table A — one row per M0/M1-P/R test (tier, steps summary, pass,
evidence, fallback, `sae m0` command, result field); table B — one row per A-id A-01..A-42 (assumption, tests,
evidence, fallback); table C — non-registry items: parser key map (M0-11 rows from `parser/README.md`), date
zero-padding, T5 Windows items (process names, `IsIconic`, PDH counter, Job abort, background mode, process-scan
cost), A-15/A-16/A-33/A-34 code notes. Go/no-go rule restated.

Drift test: every A-id in README §5, every `A-NN` on a line containing `UNVERIFIED` under `mod/`, `src/`,
`parser/src/`, `parser/README.md`, and every id in `mod/sae_bridge/UNVERIFIED.md` appears in table B; every M0
id in the registry appears in table A and in `sae.m0.checks`. Test fixture ids (`A-99` in mod tests) excluded by
scanning only non-test files.

## 5. Weekend schedule (runbook, U2)

Sat AM (3 h): init, M0-1, install mod by hand, M0-2, M0-3/5, M0-4, M0-6. Sat PM (3 h): M0-11, M0-12 baseline
(stopwatch s/month with and without mod), start M0-8 game B (save after contact, apply econ=military, `observe`,
15 years overnight; no seed field exists, so A and B load the same base save, one game at a time). Sun (4 h): M0-8 game A (15 years), finish M0-8 (or M0-7 if M0-1 found the hook), M0-14 Ironman, M0-13 if time;
`summary`. Later (not this weekend): M0-9 (20-year perf), M0-10 (keystroke injection, backlog), M1-P, R-1..R-6.

## 6. Docs (U3) and packaging (U4)

- User guide: install (uv, `sae-parse` build, manual mod copy), commands with honest status (daemon/replay/synth/
  ctl/telemetry/bench/m0 — pending ones marked "pending (owner Tn)" after checking `sae <cmd> --help`),
  presets (C8 §4, `low_impact` = H1 floor), Ironman (no live loop, console blocked), multiplayer (engine refuses
  `sae_in.ini` writes while HELLO `mp=1`, C6 §1.6), maintainer decisions (CUSUM diagnostic only; fleet-size
  intent dropped until M0-7), troubleshooting by symptom.
- Developer guide: layout and owners (C8 §7), contracts and change protocol (README §3), test commands, how to add
  an A-id (tag format, registry, checklist row — the drift test enforces it).
- Packaging plan: three artefacts (mod zip, Python wheel `sae` with optional `telemetry` extra per proposal 010,
  `sae-parse.exe` built with MSVC), no GPU deps (CI-4 lock-file check, T7), install layout, versioning.
- `CHANGELOG.md` Keep-a-Changelog with `[Unreleased]`; `packaging/merge_changelog.py` folds `changelog.d/T*.md`
  `### <Kind> (<team>)` sections into `[Unreleased]` grouped by kind, `--check` dry run; not run until Wave 3.

## 7. Acceptance

`uv run pytest -x -q`, `uv run mypy --strict src/sae/m0 packaging/merge_changelog.py`, `uv run ruff check`
green; `sae m0 --help` works; drift test green; docs reference only commands that exist (`sae <cmd>` names from
`sae.cli._COMMANDS`, flags from C8 §5); a test snapshots a fake `user_dir`/mod/install tree before and after every
subcommand and asserts it is byte-identical; a static test asserts `sae.m0` spawns no process except `sae-parse`.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Weekend too short (15-year A/B runs) | A/B runs unattended; M0-7 or M0-8, not both; stretch/later tiers explicit |
| Kit touches game folders | no write path to them; results dir guard; snapshot test |
| M0-4 latency unmeasurable without tick timestamp | burst heuristic + manual paused-on-the-1st check; documented as approximate |
| Docs claim pending features | docs tests check names against `_COMMANDS`; status line per command |
| T2/T6 change CLIs mid-build | docs cite C8 §5 contracted flags only |

## 9. Backlog

Two-save A/B comparer for M0-7/M0-8 (parse both, compute ratio); PresentMon wrapper for R-2; mod zip build script
(after M0-2 settles the descriptor); Workshop page text after M3.

## 10. Adversary round 1 (plan + kit) — applied

Verdict accept-with-changes. Fixed: runbook/template present; M0-8 uses a base save (no seed field) with apply
before `observe`, runs sequential; M0-3 reload starts a new session (no false duplicate HB); M0-4 tail drains
history over several polls and keeps the verdict on Ctrl+C; M0-6 restage bumps seq; game.log streamed, partial last
line skipped; M0-11 judges a warm second run; go rule treats M0-7 pending/skip as not passed; error.log match is
word-bounded; `.mod` staging lines labelled UNVERIFIED(A-01). Kept: results guard (only user_dir and `steamapps`;
nothing is written to game folders anyway).

## 11. Review and harden rounds — applied

Reviewer (opus): 2 HIGH fixed (tail s/month = span mean so buffered flushes fail; corrupt/undecodable results
files exit 1 with a message), 3 MEDIUM fixed (M0-3 fails on skipped HB months; `sae ctl`/`sae telemetry` marked
built; kit path override documented as `--user-dir`), 5 LOW fixed (install-marker guard, `--value` validation,
Ctrl+C everywhere, no args mutation, M0-6 staging in `M0-6.stage.json`). Adversary harden: BLOCKING fixed
(`m0_8_base` saved after M0-5, before M0-6, so twin A is unflagged); reload of a day-1 save is a new session (same
date, `m` not advancing); `name=?` fallback is informational; `s_per_month` key; A-24 and seed-field notes
labelled UNVERIFIED. Kept: a LATER save loaded mid-session reads as a skipped month (documented in M0-3 steps).
