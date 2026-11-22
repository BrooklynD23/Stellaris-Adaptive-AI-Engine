# Opt Wave 1.5: post-implementation architecture review (wave gate)

2026-11-16, `sae-adversary`. Tree: uncommitted on `feat/opt-wave-a1`, base `bf2e56b`. Read-only review; one probe
ran in a scratch directory (builtin policy: rho 0.02, clip 3.0, threshold 0.45). The "Wave 1.5 after" section of
`docs/plans/opt/DEMO_V1_NUMBERS.md` did not exist when this was written: the verdict is given without it.
Nothing ran against Stellaris. Test status is not confirmed here: `uv run pytest tests/unit/t4 tests/unit/t9
tests/contract tests/unit/t8/test_checklist_drift.py` was killed by its 540 s timeout with no output (machine
shared with other agents).

## Verdict: DONE WITH FOLLOW-UPS, after 2 blocking items

| Area | Rating | Why |
|---|---|---|
| 1. Performance and memory | ON TARGET | Two small frozensets in `FeatureMemory`, one integer per `LegalView`, one pass over `wars` in the parser |
| 2. Telemetry | AT RISK | A dropped categorical and an unreported one are both `null` on the bus (C5 note says so). Starvation cannot be measured from a real run |
| 3. Properly adaptive engine | AT RISK | The artefact routes are closed. Real-valued single ticks still lock, and one of them is likely the normal state of every early game |
| 4. Learning the profile from saves | ON TARGET | `BUILDER_VERSION` and `TUNER_VERSION` 0.2.0, parser 0.1.3, C10 v1.4. Summaries are rebuilt from the bus on every update (`src/sae/profile/cli.py:192`), so builder versions cannot mix |

## Blocking

1. **"After" numbers are not recorded.** `DEMO_V1_NUMBERS.md` has "Wave 1.5 before" only. The wave is not done
   until the "after" section holds: (d) and (g) wrong locks (before 3 and 4, target 0), the (h) kept-categorical
   fraction (before 1.000), and the CI-1 pooled rate (before 1.000, stop rule 0.90).
   Fix: WO-E appends them. If (h) is 0.0 (see item 2) the maintainer decides before merge.
2. **The reporting-set rule starves on intel flicker, and nothing reports it.** Probe on `extract`:
   - reporting set alternating on every save: 0 of 20 evidence ticks kept a categorical (permanent);
   - each observer independently missing a report on 5 % of saves: 81 % kept with 2 observers, 60 % with 5,
     44 % with 8; at 10 %: 67 %, 36 %, 19 %.
   The real flicker rate is UNVERIFIED. More managed AIs means less evidence, which is the wrong direction.
   Fix (no contract change): add to `docs/TEAM_PLAN.md` "Validation backlog" and `docs/m0_checklist.md` a step
   that replays a real run's kept observations through `extract` and records the kept fraction for
   `fleet_power_cat` and `tech_cat`. State the rule to act on it: below 0.5, switch `_stable_cat` to the
   observers present in both the previous and the current set. Do not change the rule now.

## Single-tick paths from a uniform prior (re-scan)

Closed, confirmed by reading and by `tests/unit/t4/test_single_tick_property.py` (posterior equals prior):
observer-set gain, loss and swap for both categoricals; sole-observer gap (the no-view tick stores the empty
set); one or two frontier systems; one system bordering 3 observers; AI-declared war; player as co-attacker of
a tagged AI; role unknown. `first_war_month` keeps "any war" but no code reads it for belief, tuning or the
profile (`belief.py`, `tune/fit.py:146`, `profile/summary.py:132` all use `at_war`).

Still open (one evidence tick, k >= 12):

| Feature alone | Top posterior | Status |
|---|---|---|
| `border_fort_ratio` 0.0 with >= 3 frontier systems | tech_rush 0.681 (0.728 with `at_war` false) | **New concern.** No fortified border starbase at first contact is probably every player's early state (UNVERIFIED). It would lock `early_punish` for 24 months on the first tick |
| `border_fort_ratio` >= 0.85 | turtle 0.660 | By design |
| `expansion_rate` 0 / >= 3 | turtle 0.539 / early_aggression 0.566 | View growth accepted (F4) |
| `fleet_power_cat` +2 / -2 / -1 on the second stable tick | 0.685 / 0.638 / 0.467 | Accepted (039 rule 6). After an observer loss the shifted median counts one tick later: the rule delays the route by one save, it does not remove it |
| `tech_cat` +2 on the second stable tick | 0.625 | Accepted |
| `at_war` true (offensive) | 0.650 | By design; rests on A-55 |
| Stable set, 36-month gap, prior 0.85 on another archetype, `fleet_power_cat` +2 | 0.509 | Post-gap weight, not covered by decision |

With monthly saves the weight is k / 12, so one tick gives 0.282; the exposure is yearly autosaves and gaps.

## Answers to the gate questions

- **D1 exposure:** reduced to real feature values. The artefact ticks no longer move the belief at all.
- **038 / 039 and the C6 fog boundary:** fit. The field is the same class as `player_war_count` (public,
  UNVERIFIED A-55), set only in contact, and the fallback needs no contract change. No `sae m0` check lists
  A-55 yet.
- **Under-count:** a war the player declares with any `sae_sid` country as co-attacker gives 0, so `at_war` is
  `None`. If the mod tags most AI countries, an aggressive player with allies is never seen at war. Safe
  direction (no false lock), but it starves `war_share`.
- **Comparability:** versions bumped in code. C10 example JSON still shows `builder_version` 0.1.0 (lines 197,
  208, 226). Nothing in `sae tune` or `sae gate` refuses evidence from `tuner_version` 0.1.0; the rule is
  documentation only.
- **Wave 3 readiness:** yes. `W3_DESIGN.md` and `IMPLEMENTATION_WAVES.md` carry 039 = C10 v1.4, 036 = v1.5,
  037 = v1.6, A-50..A-54 reserved, A-55 taken. `fleet_vis` / `tech_vis` load as `None` from old state. Capture
  the W3-0 golden only on the merged commit, after blocking item 1.

## Follow-ups

1. Border ratio at first contact: at M0 record `border_fort_ratio` on the first 5 evidence ticks of a real
   game. If it is 0.0 for an ordinary player, the tech_rush mean (0.1, sd 0.2) is wrong; the maintainer
   decides the fix (it is next to D1).
2. T8: add A-55 to the M0-11 check with one manual step.
3. Count how many AI countries carry `sae_sid` in a real game (decides the co-attacker under-count).
4. C10 example JSON: `builder_version` 0.2.0.
5. `sae gate`: warn when gate evidence was produced by another `tuner_version`.

## Bottom line

Wave 1.5 closes the fog-artefact lock routes it targeted and does not disturb Wave 3. It is done once the
"after" numbers are recorded and the starvation measurement is on the validation backlog.

## Re-review rev 3.1 (2026-11-17)

Tree: uncommitted, option 2 rev 3.1. Checked: `_stable_cat` and `extract` read in full;
`tests/unit/t4/test_single_tick_property.py` passes (174 cases); CI-1 1.000 over 20 and 40 switches (team run,
not re-run). "Wave 1.5 after (rev 3.1)" was still absent from `DEMO_V1_NUMBERS.md`.

**Verdict: DONE WITH FOLLOW-UPS, after 1 blocking item.**

| Area | Rating | Evidence |
|---|---|---|
| 1. Performance and memory | ON TARGET | Two more integers in `FeatureMemory` |
| 2. Telemetry | AT RISK | Dropped and unreported are both `null`; no real-run kept-fraction step in the backlog |
| 3. Adaptive engine | AT RISK | Artefact routes closed. Accepted value locks: first save 0.685 (B1), `border_fort_ratio` 0.0 at 0.681 |
| 4. Learning from saves | ON TARGET | Versions unchanged; 039 rule 7 records B1 and A1 |

### Starvation probe (through `extract`, yearly ticks, 200 seeds x 40 ticks)

| Case | Before | Now |
|---|---|---|
| Alternating set, same values | 0 / 20 | 20 / 20 |
| 5 % drop, 2 / 5 / 8 observers, same values | 0.81 / 0.60 / 0.44 | 0.992 / 1.000 / 1.000 |
| 5 % drop, observers report different values | not measured | 0.909 / 0.777 / 0.779 |
| 10 % drop, different values | not measured | 0.833 / 0.629 / 0.682 |

Fixed: all above the 0.5 target. Residuals:
- Alternating set from a pre-1.5 `state.json` (`fleet_last` None): 0 / 20, permanent, because the guard never
  matches `None`. One stable pair of saves ends it.
- With different values, 2.1 % (5 observers) and 2.7 % (8) of counted ticks carry a shifted median, counted on
  the tick after a dropped loss. This is the delay route accepted in 039 rule 6.

### Blocking

1. **After-numbers (SEMANTIC, unchanged).** Record (d), (g), (h) and CI-1 under "Wave 1.5 after (rev 3.1)".
   Targets: (d) and (g) 0, (h) >= 0.5, CI-1 >= 0.90. A miss goes to the maintainer before merge.

### Follow-ups

1. Add the real-run kept-fraction step for both categoricals to the "Validation backlog" and
   `docs/m0_checklist.md` (old blocking item 2, now TEXT).
2. Pre-1.5 state: note the residual in 039, or reset the categorical sets on load.
3. First-review follow-ups 1 to 5 stand.

### Wave 3 golden capture

Ready. Capture W3-0 only on the commit holding Waves 1-2 + 1.5 and the after-numbers.
