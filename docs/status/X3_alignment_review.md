# Fable alignment review: wave X3 (game-deferred test harness)

Date: 2026-10-20. Reviewer: sae-adversary (Fable 5.1). Scope: `git diff` + untracked files on
`feat/x3-game-test-harness`. Verdict: **ACCEPT WITH CHANGES** (3 blocking, all doc/test-only, no architecture change).

## Re-verification (this review, this worktree)

| Command | Result |
|---|---|
| `uv run pytest tests/game tests/unit/t7/test_game_marker.py tests/unit/t7/test_make_recording.py tests/unit/t7/test_run_deferred.py tests/e2e/test_game_dryrun.py -o addopts="" -q -rs` | 20 passed, 12 skipped, 15.97s; every skip reads `game input missing: SAE_GAME_*` and cites `docs/handoff/deferred/X3.md#x3-gN` |
| `SAE_GAME_SAVES_DIR=... uv run sae bench spy <dir>` | exit 0 (bench does not load C8 env; G5's spy call is safe) |
| `SAE_GAME_SAVES_DIR=... uv run sae m0 steps M0-2` / `sae m0 check M0-14` | exit 0 (runbook section 3 env does not break step 1) |
| Lead's full gates (pytest 1252 passed / 15 skipped, cov 96.68%, mypy strict + win32, ruff, clippy, cargo test) | taken as reported; not rerun (three waves share the machine) |

## Architecture alignment

| Axis | Status | Evidence |
|---|---|---|
| (1) Performance and memory | ON TARGET | `parsed_saves` parses each save once per session (one spawn per save, 60s timeout, newest 24 cap via `SAE_GAME_MAX_SAVES`). G2 asserts `cost.wall_s <= 3.0` and `rss_peak_bytes <= GovernorConfig().worker_mem_cap_bytes`, the real R-5 budget. No daemon, priority or affinity touched. Minor: G7/G8 load the whole `game.log` into RAM; acceptable on the test PC, not a runtime path. |
| (2) Telemetry | ON TARGET | Nothing changes the telemetry process. Evidence goes to junit `user_properties` (`deferred_id`, `A-20`, `A-24`) and `summary.md`; raw junit/basetemp stay in `private/`. Redaction covers env paths and `$HOME`; the T7.md note that <8-char values pass through is honest. |
| (3) Properly adaptive engine | ON TARGET | No engine code changed. G9 (`check_chain(min_decisions=3)`) and G10 (equal digests) are the first real-data V-3 signal checks; the belief table and 24-month commitment are untouched. |
| (4) Readiness to learn from saves (bounded RSI) | ON TARGET | `make_recording.py` turns a real `game.log` + autosave folder into a replay session (labels left to X4, correct). G11 runs the F1 fog check on real recordings with a non-vacuity assert (`god` bus non-empty). Fairness: `PerturbingParser` seeds only touch god-view fields; decision stream is compared, beliefs cannot see the perturbation. No runtime self-modification introduced. |

## Deferral audit

All 12 entries (X3-G0..G11) in `docs/handoff/deferred/X3.md` have a runnable PowerShell command, an input list, a pass
rule and a record rule. `BUILD_REPORT.md` 7c, `components/T7.md`, `TEAM_PLAN.md` and all test docstrings say UNVERIFIED.
Nothing claims a game result. Two exceptions below.

## Required changes (blocking, before commit)

1. **`docs/status/T7.md` has four `<lead fills>` placeholders in "Gate commands".** A status doc with blanks reads as
   unverified work. Fix: replace with the lead's numbers: `tests/game -q`: 0 passed 12 skipped; mypy strict: 95 files
   clean (also `--platform win32`); ruff: clean; full suite: 1252 passed / 15 skipped, TOTAL 96.68%.
2. **X3-G5 body has never executed.** Synth saves are named `<offset>-<name>.sav` (`src/sae/adapters/replay.py:9`), so
   `test_autosave_cadence` skips with "no autosave_" and `tests/e2e/test_game_dryrun.py::_ALLOWED_SKIP` whitelists it.
   A typo there surfaces tomorrow with no time to fix. Fix: in the dry run, copy the synth saves into a second dir as
   `autosave_<n>.sav` (3 lines) and remove `("X3-G5", "no autosave_")` from `_ALLOWED_SKIP`; or record in
   `deferred/X3.md` G5 that the test body is itself UNVERIFIED and the agent should read the traceback as a harness bug.
3. **X3-G7 pass rule gives a false FAIL on any session that starts mid-month.** `months` is built from every record
   (HELLO, SNAP, PLYR), `hb_months` from HB only. HELLO fires on load, HB on `on_monthly_pulse` (A-40), so the HELLO
   month usually has no HB. Fix in `tests/game/test_log.py::test_log_grammar`: assert HB months are consecutive
   (`month_index` gaps <= 1) and `len(hb_months) >= 1`; drop `months - hb_months`. Update the G7 rule text to match.

## Suggestions (not blocking)

- `docs/handoff/GAME_PC_AGENT.md` section 3 sets `SAE_GAME_*` before any hand-run `sae` command; `sae daemon/replay/
  profile/control` load `env_layer(os.environ)` (C8) and will exit 3 `extra_forbidden`. `sae m0` and `sae bench` do not.
  Add one line after the env block: "Run `sae replay`/`sae daemon` by hand only after `Remove-Item Env:SAE_GAME_*`."
- G4 accepts exit 6 for a truncated copy while the runbook defines 6 as "sharing violation". Narrow to 5 after tomorrow's
  evidence, or document why 6 is legal for a truncated zip.
- `game_recording` fixture asserts `rc == 0` with no stderr; on failure the agent sees only "exited 1". Capture
  `make_recording` stderr into the assert message (basenames only).

Bottom line: X3 adds no runtime code and keeps every game claim deferred with a command and a pass rule, so the four
architecture axes stay ON TARGET. Close the three blanks-and-false-fail items above (about 20 lines total) and commit.
