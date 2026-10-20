# X1 plan review (Fable gate) — docs/plans/T9_plan.md + work orders X1-A..F

Reviewer: sae-adversary (Fable 5.1), 2026-10-20. Verdict: **ACCEPT WITH CHANGES**.

Checked: fog legality (profile reads only `bus.jsonl` legal `run_started`/`observation`/`belief_update`; features via
`sae.core.features.extract`; `economy_cat`/`systems_owned` are `LegalView` fields — ON TARGET), C10 conformance,
privacy (hashes/dates only; `show` prints the salt *path*, never bytes), launcher (spawns only `python -m sae profile
update`, never the game), file sets (X1-A..F disjoint; `CHANGELOG.md` and `.github/workflows/ci.yml` are already
modified in the tree — X1-E must merge, not clobber), RSI readiness (strict deterministic summaries, blind labels — ON
TARGET), scope (tune/policy/gate correctly out).

## Required changes (blocking)

1. **`campaigns.jsonl` has no writer lock and no partial-line repair.** `sae play` spawns `update`; a second
   `sae profile update` (or a killed subprocess at `timeout=600`) can leave a half line that the next append glues
   onto. Fix in `src/sae/profile/store.py::append_events`: take `campaigns.jsonl.lock` with
   `os.open(O_CREAT|O_EXCL)` (same pattern as C10 §5, raise `IndexCorrupt`-style error "index locked"), and if the
   file is non-empty and does not end with LF, write one LF first (the stub becomes a skipped invalid line; seq
   continues from the last valid row). Tests: lock present → exit 1; partial last line → next append still gapless.
2. **`exclude` and `delete --campaign` leave stale derived data.** `profile.json` and `summaries/<id>.json` keep the
   campaign until the next `update`, which `profile.mode = off` or a running game blocks — so a privacy command can
   never take effect. Fix in `cli.py`: both commands remove `summaries/<id>.json` and `profile.json` (next `update`
   rebuilds). Test: after exclude, `profile.json` is absent. Add to 025 §3.7.
3. **Run lookup rule is ambiguous when one folder holds several campaigns** (after a "new game" split, or a
   deleted one). Fix 025 §3.2 / `index.py::plan_update`: (a) run_id in any non-deleted campaign → that campaign
   (seen only if `last_date` later); (b) run_id in a deleted campaign → skip, no row; (c) otherwise the folder's
   current campaign = latest non-deleted by first `seen`, then the 5-year split rule. Test the three branches.
4. **Two vacuous tests.** `test_summary` "open fails on `bus.god`" passes if the fixture has no god file: `make_run`
   must write a non-empty `bus.god.jsonl` (≥1 `observation_god`) in every run. F3 (b) must assert ≥1 run has a
   non-empty `bus.god.jsonl` before randomising, and (d) must assert the changed `systems_owned` was on an
   `in_contact=True` view (otherwise the control can fail for the wrong reason).
5. **`label --recording DIR` overwrites `DIR/label.json`**, which for synth recordings is the ground-truth schedule
   label. Fix: refuse with exit 2 if the file exists (no `--overwrite` in X1). Same for `labels/<id>.json`.

## Optional

- Folds: define decay age inside a fold relative to campaign k (and the drift fit relative to the newest of the
  others); state it in `model.py` and 025 §3.
- With σ = `hyper_sd`, `player_sd ≥ hyper_sd`, so `drift_alarm` almost never fires; comment this in `model.py`.
- `update` re-reads every bus every time; print elapsed time now, cache later if > 30 s on 50 runs.
- Document recovery for "campaigns.jsonl corrupted": rename the index and re-run `update` (ids are deterministic;
  labels keyed by id survive; split answers are asked again).
- The user asked for GitHub CI checks; no X1 order owns `.github/workflows/ci.yml`. The existing `pytest -q`
  job already picks up the new e2e tests; the orchestrator should confirm the parser build step precedes them.

## Architecture flags
Perf/memory ON TARGET (streaming reads, small segments). Telemetry ON TARGET (untouched). Adaptive engine ON TARGET
(daemon reads no profile). RSI readiness ON TARGET once items 1–3 land.

Bottom line: the plan is fog-legal and correctly scoped; its failure modes are durability (unlocked index, stale
privacy outputs) and two tests that can pass without proving anything. Fix the five items before batch 1.

## Plan review response (T9 lead, 2026-10-20)

All 5 blocking items closed before batch 1. Text: `docs/plans/T9_plan.md` (§2–§4, §6, §7), `contracts/proposals/025-T9-x1-profile-wiring.md` §3, revised work orders X1-A..F.

| # | Issue | Closed by |
|---|---|---|
| 1 | Index lock / partial line | 025 §3.9 (new). X1-B `store.append_events`: `campaigns.jsonl.lock` via `os.open(O_CREAT\|O_EXCL\|O_WRONLY)` → `IndexLocked`, CLI exits 1 "index locked: campaigns.jsonl.lock"; non-empty file without final LF → write one LF first; seq = last valid + 1; fsync; lock removed in `finally`. Tests: lock present → `IndexLocked` and `update` exit 1, lock left untouched; partial last line → next append gapless and `read_index` gap False with 1 invalid line. Recovery note in 025 §3.9 and X1-E docs. |
| 2 | Stale derived data after exclude / delete | 025 §3.7 extended. X1-B `exclude` and `delete --campaign` unlink `summaries/<id>.json` and `profile.json`. Test: with mode off, after `exclude` and after `delete --campaign`, both files absent; next `update` (mode strict) rebuilds without the campaign. |
| 3 | Ambiguous run lookup | 025 §3.2 run lookup (a) any non-deleted campaign holding the run_id, (b) deleted → skip, (c) folder's latest non-deleted campaign by first `seen`, then the 5-year split. X1-B `plan_update` implements it; `test_index.py` has one test per branch, including a folder with two campaigns after a "new game" split. |
| 4 | Vacuous tests | X1-B `_fixtures.make_run` always writes a non-empty `bus.god.jsonl` (≥ 1 `observation_god`); the bus.god open-guard test asserts the file exists and is non-empty first. X1-D F3 (b) asserts ≥ 1 run has a non-empty `bus.god.jsonl` before randomising; (d) edits `systems_owned` on a legal view with `in_contact=True` and asserts that before re-running. |
| 5 | `label --recording` overwrites ground truth | 025 §3.10 (new). X1-B `label` exits 2 if `DIR/label.json` or `labels/<id>.json` exists; no `--overwrite` in X1. Tests for both targets assert the file bytes are unchanged. |

Optional items taken: fold-relative decay age (025 §3.5, `model.py` docstring); `drift_alarm` rarity comment in `model.py`; `update` prints elapsed seconds; corrupted-index recovery documented (025 §3.9, user guide). Not taken: CI workflow edit — `.github/workflows/ci.yml` is outside T9 paths; flagged to the orchestrator (confirm the parser build precedes `pytest`).
