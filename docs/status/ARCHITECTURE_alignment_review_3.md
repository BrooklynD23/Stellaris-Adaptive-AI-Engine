# Architecture alignment review 3 — wave X1 (Fable gate)

Reviewer: sae-adversary (Fable 5.1), 2026-10-20. Scope: uncommitted diff on `feat/sae-mvp` (15 modified files,
`src/sae/profile/` 7 files / 1,178 lines, `tests/unit/t9/`, `tests/contract/test_c10_registry_writer.py`,
`tests/e2e/test_fog_f3.py`). Verdict: **ACCEPT**. No blocking changes; nothing before commit.

## Re-verified (run, not trusted)

| Check | Result |
|---|---|
| `uv run pytest tests/unit/t9 tests/contract/test_c10_registry_writer.py tests/test_cli.py tests/e2e/test_fog_f3.py tests/e2e/test_fog.py::test_f1_plyr_leg_reaches_god_bus tests/integration/test_fog_f4.py` | 124 passed, 32 s, exit 0 |
| Full `uv run pytest -x -q` | exceeded this review's 590 s budget (killed, no failure seen). Gate report: 1232 passed / 1 skipped / 96.68 % cov, taken as reported |
| `uv run mypy --strict src/sae` | Success, 95 files |
| `uv run ruff check` | All checks passed |
| Game behaviour | UNVERIFIED (unchanged); `profile/cli.py:118` labels the process probe UNVERIFIED |

## Review-2 follow-up items 3 and 5

| # | Item | Status | Evidence |
|---|---|---|---|
| 3 (actions) | `.coverage*` ignored, coverage `parallel = true` | **CLOSED** | `pyproject.toml:58-61`, `.gitignore:16-18` |
| 3 (still red) | F1 PLYR leg non-vacuous | **CLOSED** | `tests/e2e/_fog.py::with_plyr_lines` inserts full PLYR lines before the first 3 saves, shifts `game.log.ts` and save names; `tests/e2e/test_fog.py::test_f1_plyr_leg_reaches_god_bus` asserts PLYR reached `bus.god`, 0 `log_parse_error`, equal decision streams across seeds |
| 5 (actions) | C10 §5 trust-on-write + TTY; D-V4 tracked | **CLOSED** | `contracts/C10_profile_policy.md:309-319`; `docs/TEAM_PLAN.md:132-136`, V-4 row line 234 (decision waits on M0-7, tracked not actioned) |
| 5 (still red) | WU-7 registry writer with `isatty()` refusal | **CLOSED** | `src/sae/contracts/c10_profile.py::append_registry_row` refuses `human:` rows before the lock, `O_CREAT\|O_EXCL` lock, LF repair, fsync; `tests/contract/test_c10_registry_writer.py` |

## Verdicts

| Point | Verdict | Evidence |
|---|---|---|
| 1. Performance and memory | **ON TARGET** | Nothing new enters the daemon loop. `sae profile update` is a separate process at idle priority (`_lower_priority`, own process only, `profile/cli.py:126`), refuses while the game runs unless `--force`, holds the index lock only around the append (ms), streams buses via `iter_events`, loads one run's observations at a time. Ceiling: every update rescans every bus and rebuilds every closed summary; cost is linear in run count, acceptable offline, cache when > 30 s on 50 runs (plan review optional item, still open). |
| 2. Telemetry | **ON TARGET** | Untouched. `sae play` runs the update before `daemon_main`, so it is outside the 150 MiB telemetry cap by design (not a hidden allocation inside the loop). |
| 3. Properly adaptive engine | **AT RISK** (unchanged) | X1 does not touch belief or counter selection; daemon imports only `pin_matches` from c10 (`daemon/loop.py:28`). Review-2 evidence stands: 1 switch / 60 synth years, V-1..V-3 open, D-V4 blocked on M0-7. Not an X1 regression. |
| 4. Ready to learn from saves (bounded RSI) | **ON TARGET** | Index, summaries and profile now exist and are rebuildable (`built_at` from last index row, canonical bytes). Fog legality held: `src/sae/profile/` never names or opens `bus.god` (grep clean); summaries use `in_contact` views and `sae.core.features.extract` only. F3 proves it non-vacuously (god buses present and randomised → profile bytes unchanged; legal `systems_owned` edit on an `in_contact=True` view → profile changes). Durability items from the plan review landed: lock, LF repair with seq counted after repair (`store.read_index`), `(run_id, folder_key)` lookup, stale `profile.json`/`summaries` removed on exclude/delete, labels never overwritten. |

## Not blocking, tracked

1. `sae play` with a TTY can stop at the split prompt (`_ask_split`) before the daemon starts; user guide should say so (T8, later).
2. `store.append_events` reads the index twice under the lock (`read_index` then `read_bytes`); harmless, tidy later.
3. `write_atomic` does not fsync the temp file before `os.replace`; a power cut can leave an empty `profile.json`; next `update` rebuilds it. Acceptable for derived data.
4. Full rescan per update (see point 1 ceiling).
5. D-V4 and the adaptive-engine switch rate wait on game data (M0-7); tracked in `docs/TEAM_PLAN.md` V-1..V-4.

Bottom line: X1 adds the save-learning substrate without touching the live loop, the settled decisions or fog
legality, and the two tests that mattered (F1 PLYR, F3) now fail if the property breaks. The adaptive-engine point
stays AT RISK on unchanged evidence, which is a game-data problem, not an X1 one.
