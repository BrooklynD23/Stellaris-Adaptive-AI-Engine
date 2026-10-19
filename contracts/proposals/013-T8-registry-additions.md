# 013-T8 — Register three platform assumptions (A-43..A-45) in README §5

**Status: ACCEPTED** (architect, 2026-10-19; doc-only, README §5 rows A-43..A-45 added; no version change.)
Proposed 2026-10-19, T8 lead.

Contracts: `contracts/README.md` §5 (registry), C7 (consumer note). Doc-only, additive; no model or wire change.

## What

Add three rows to the README §5 table. They are UNVERIFIED Windows/platform assumptions T5 reported for the M0
checklist (`docs/status/T5.md`) that have no A-id today; the checklist carries them as T8-local X-01..X-03.

```diff
+| A-43 | The game process is named `stellaris.exe` (Windows, Proton) / `stellaris` (Linux) | M0-12 | C7 |
+| A-44 | `IsIconic` on the game's top-level window detects minimise | M0-12 | C7 |
+| A-45 | A `psutil.process_iter` rescan on Windows stays under the 100 ms probe bound | M0-12 | C7 |
```

## Why

Every game/platform assumption should keep an A-id (TEAM_PLAN quality bar). Without ids the drift test in
`tests/unit/t8/test_checklist_drift.py` cannot tie T5's code comments to checklist rows.

## Consumers and migration

- T5: optionally tag `src/sae/platform/sensors.py` comments with `UNVERIFIED A-43/A-44/A-45`.
- T8: on acceptance, rename X-01..X-03 to A-43..A-45 in `docs/m0_checklist.md`, add them to `sae.m0.checks`
  M0-12 assumptions. Until then the checklist keeps the X-ids.
- Additive, no version change (registry rows are doc text).
