# Wave 1.5 rev 3: option 2 (maintainer, 2026-11-17)

Why: CI-1 fell 1.000 -> 0.800 (`docs/status/opt_wave_1_5_ci1_diag.md`). Two rules acting together cause it:
- `MIN_FRONTIER` = 3 against the synthetic border geometry, which is a synth artefact.
- The reporting-set rule, which is a real evidence loss. The first save of each session is always dropped,
  and each new contact costs about 2 years at the H2 cadence.

The Fable wave review measured starvation from intel flicker: 0/20 ticks kept when the set alternates, and
44 % kept with 8 observers at a 5 % drop rate.

## WO-F (T2): synth border counts

`src/sae/adapters/synth.py`: `tech_rush` frontier systems per observer 2 -> 3 and `early_aggression` 2 -> 4.
The fort fractions stay 0.0 and 0.5.

Tests in `tests/unit/t2/test_synth.py` and `tests/unit/t2/_asm_helpers.py` as needed.

## WO-C2 (T4): narrower reporting-set rule, `src/sae/core/features.py` `_stable_cat`

Applies to `fleet_power_cat` and `tech_cat`. The stored sets are `fleet_vis` and `tech_vis`.

1. **Never seen vs gap.** In the no-views branch, a stored set that is `None` stays `None`. A non-`None`
   stored set becomes `frozenset()`, which marks a real contact gap. Log ticks before the first save therefore
   no longer poison the first save.
2. **First evidence.** When the stored set is `None`, the categorical counts.
3. **Unchanged set.** When the set equals the stored set, the categorical counts.
4. **Changed set.**
   - If the stored set is non-empty, take the categorical median over observers in `stored ∩ current` only.
   - If that intersection is empty, the tick is dropped.
5. **After a gap.** When the stored set is `frozenset()`, drop one tick. This keeps the F2 sole-observer-gap
   fix.
6. **Update.** The stored set becomes the current reporting set on every evidence tick, as now.

`border_fort_ratio`, `MIN_FRONTIER` and `_at_war` are unchanged.

## Tests

- The property test (`tests/unit/t4/test_single_tick_property.py`) keeps every artefact case closed, including
  swap, gain, loss and the sole-observer gap. Add these cases:
  - subset change with the same values: counts, and the posterior equals the prior only when the values are
    uninformative;
  - alternating set {A,B} -> {A} -> {A,B}: counts on the intersection;
  - first save after log-only ticks: counts.
- Mutation check: reverting each rule must fail at least one case.
- `tests/unit/t9/test_summary.py`: re-measure the code-review values (`:43`, `:46`, `:47`, `:84`) under rev 3.
  Record old -> new and why (CLAUDE.md Testing).
- `contracts/C10_profile_policy.md:145,148`: profile `border_fort_ratio` and the categorical means exclude
  dropped months. Add one line to proposal 039. This is a doc change, with no version bump.

## Stop rule and measurement

- CI-1 pooled rate >= 0.90 with the default seeds and with `T7_E2E_FULL=1`. The threshold is never edited.
- After numbers: `tools/bench/fog_adversarial_measure.py` and `demo_v1_measure.py`. Report the (h) kept
  fraction.
- Targets: (d) and (g) at 0, and (h) at least 0.5.

## Rev 3.1: Fable check edits and maintainer picks (2026-11-17)

The Fable quick check said NO-GO as written, and GO after these edits. Maintainer picks: **A1** (value guard)
and **B1** (count the first save). The rules below override rules 1, 2 and 4 above.

1. **Rule 1, never seen vs gap.**
   - The sets persist in `state.json`, so a resumed session follows rule 5 for its first save. This is
     accepted.
   - A pre-1.5 `state.json` has sets of `None` but `last_evidence_mi` set. It is treated as rule 5 (dropped
     once), never as first evidence.
2. **Rule 2, first evidence.** The categorical counts only when the stored set is `None` **and**
   `memory.last_evidence_mi is None`. That is the first real evidence of the campaign.
   - Maintainer B1: a first-save lock of up to 0.685 under D1 is accepted by design.
3. **Rule 4, value guard (A1).** On a set change with a non-empty stored set:
   - Take the median over `stored ∩ current`.
   - Count it only if it equals the last **counted** value for that categorical. Store this as new
     `FeatureMemory` fields `fleet_last` and `tech_last`, both defaulting to `None`.
   - Otherwise drop the tick. An empty intersection drops the tick.
   - Counting updates `*_last`.
4. **Tests.**
   - List every `test_single_tick_property.py` case that is reclassified from dropped to counted. Expect about
     40: gain, swap, first_tick, `combo/first_tick_all`, `combo/gain_ai_war_shared_system`.
   - Add differing-value cases:
     - gain or partial swap whose intersection median equals A's last counted value: counts;
     - loss that shifts the median, e.g. {A:2, B:-2} -> {A:2}: dropped;
     - one-save flicker {A,B} -> {A} -> {A,B} with differing values: never counts a shifted value;
     - resumed session and pre-1.5 `state.json`: dropped once.
   - Run the mutation check: reverting each rule must fail at least one case.
5. **Proposal 039 note.** First-save evidence counts (B1). The value guard means a categorical change is only
   learned on a tick where the reporting set is stable.
