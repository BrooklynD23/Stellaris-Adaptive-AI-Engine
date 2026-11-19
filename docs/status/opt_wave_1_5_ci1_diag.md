# Wave 1.5 CI-1 regression diagnosis (read-only)

Result: CI-1 pooled rate 0.800 over N=20 switches (reproduced; 1.000 at `bf2e56b`). 2 of 20 switches mismatch,
both `tech_rush` (seeds 1 and 2). `src/sae/core/features.py` was not edited. Toggles were monkeypatched through a
scratch `sitecustomize.py` on `PYTHONPATH`, and every variant re-recorded and replayed all 5 schedules x seeds 1, 2
(the default `test_ci1.py` matrix).

## Mismatches

| Campaign | H0 switch | H2 switch | Delta | Window |
|---|---|---|---|---|
| tech_rush-1 | mi 26406 (~2200.07) early_punish | mi 26472 (~2206.01) early_punish | 66 mo | < 24 |
| tech_rush-2 | same | same | 66 mo | < 24 |

The other 8 campaigns match. `balanced` has no switches.

## Rate per single-rule revert

| Variant | Rate | tech_rush H0 / H2 |
|---|---|---|
| current (all Wave 1.5 rules) | 0.800 | 26406 / 26472 |
| MIN_FRONTIER 3 -> 2 | **1.000** | 26403 / 26424 |
| MIN_FRONTIER 3 -> 1 (old rule) | **1.000** | 26403 / 26424 |
| reporting-set rule off (plain lower median) | **1.000** | 26403 / 26424 |
| `_at_war` old rule | 0.800 | 26406 / 26472 |
| reporting-set rule kept, first tick (`prev is None`) accepted | 0.800 | 26406 / 26472 |
| MIN_FRONTIER 2 + reporting-set off | 1.000 | 26403 / 26424 |
| WO-F (synth borders tech_rush 2->3, early_aggression 2->4), all rules kept | **1.000** | (no mismatch) |

## Root cause

The failure needs two rules together. Reverting either one alone fixes it. `_at_war` plays no part.

1. **F1 (MIN_FRONTIER=3).** Synth `tech_rush` reports `frontier_systems=2` for every observer for all 30 years
   (`src/sae/adapters/synth.py` `PROFILES`, borders=2). So `border_fort_ratio` is never evidence.
2. **F2 (reporting-set rule).** H2 sees 15 saves, 24 months apart (2201.12, 2203.12, 2205.12, ...).
   - Tick 1 (2201.12) is always dropped. Before the first save, log observations have no in-contact views, so the
     no-views early return stores `fleet_vis = tech_vis = frozenset()`. The first save then counts as "changed".
     This is why the "accept the first tick" variant does not help: `prev` is never `None` by then.
   - Tick 2 (2203.12) is dropped because AI 4 comes into contact at month 24 (the set goes from 3 to 4 observers).
   - The first categorical evidence arrives at 2205.12, so the switch comes at 2206.01.
   - H0 pays the same two drops, but at a 3-month save cadence they cost only 3 months.

## Synth artefact or real loss?

- **F1: mostly a synth artefact.** A border that stays at 2 systems for 30 years is unrealistic. Real frontiers
  grow, but 1 to 2 systems per neighbour is common in the early game. The early-game loss is the trade-off F1
  intends.
- **F2: real loss.** Real games gain contacts steadily in the first 10 to 20 years. Each new contact drops one
  categorical tick, and at H2 cadence one tick is about 2 years. The pre-first-save reset also drops the first
  save tick in every real session that starts with log lines before the first autosave.

## Fix options (none edit the 0.90 threshold)

1. **Land WO-F as planned.** Measured: 1.000 with F1 and F2 kept. It is 1 h of work, fully in plan, and changes only
   the synth. Trade-off: it hides the F2 starvation instead of fixing it, so real H2 sessions still lose about
   2 years of tech/fleet evidence per contact change.
2. **WO-F plus a narrower F2.** Two changes. First, make the no-views early return leave `fleet_vis` and `tech_vis`
   as `None` when they are already `None`, so the first save tick counts. Second, on a set change, take the median
   over the observers present at both ticks instead of dropping the tick. This keeps most of the jump protection
   but needs a C4/F2 note and new unit cases. Not measured.
3. **Drop F2 and keep F1 + WO-F.** This is the smallest code change and should pass CI-1 (each revert alone gave
   1.000). Trade-off: it brings back the median jump on contact changes that F2 was meant to stop.

Scratch scripts are in
`/tmp/claude-1000/-mnt-c-Users-DangT-Documents-GitHub-Stellaris-Adaptive-AI-Engine/d4efc1c5-69e5-4ddc-879f-1e378d064932/scratchpad/ci1_diag/`
(`drive.py`, `site/sitecustomize.py`).
