# Wave 1.5 plan: single-tick false-confidence fixes (feature level), rev 2

2026-11-16, sae-architect. Plan only; nothing run. D1 stays: no confidence or evidence-tick guard.
Rev 2 folds in the `sae-adversary` plan review (ACCEPT WITH CHANGES, 7 blocking). Wave review follows WO-E.

## 0. Changes from review

| Review item | Change | Where |
|---|---|---|
| B1 shared frontier system | Gate on per-view `max(frontier_systems)`, pooled ratio kept; 3 observers x 1 system test; synth profiles need >= 3 borders (new WO-F) | §2 F1, §5, §6 |
| B2 sole-observer gap | No-views early return resets `fleet_vis`, `tech_vis`; T-2b; decayed-prior property case | §2 F2, §6 |
| B3 ally of an AI attacker | War counts only if no observer country is in `attackers`; fixture `war_ai_led_player_coattacker` | §2 F3, §4, §5 |
| B4 version collision | 039 = C10 v1.4, 036 -> v1.5, 037 -> v1.6; C6 gets a doc note, no bump; W3 docs in WO-A | §4, §5 |
| B5 assumption id | A-55 (A-50..A-54 are Wave 3's). `test_checklist_drift.py` has no contiguity check | §3, §4 |
| B6 profile versioning | `BUILDER_VERSION` 0.2.0; 039 states the `war_share` denominator and HYP priors | §4, §5 |
| B7 measurement | Perturbations (f), (g), (h); CI-1 "before"; "not demonstrated" where before is 0 | §6 |
| N1 | Field defined observer-relative | §4 |
| N3 | Two "first war" meanings written in C5 and C10 | §4 |
| N4 | `_asm_helpers.py`, `test_expansion.py` in file sets | §5 |
| N5 | WO-B before WO-C's e2e step | §5 |
| N6 | Property test parametrized, asserts posterior == prior | §6 |

## 1. Findings that shape the plan

1. `LegalView` has no attacker field; follow-up 3 needs a C4 field. The parser already reads `attackers` /
   `defenders` per war (`parser/src/extract.rs` `WarRec`).
2. `systems_owned` is the player's true total, not intel-masked (C4 §3, A-19). The "5 -> 18 view growth"
   artefact cannot occur with today's parser.
3. All new thresholds are code constants in `features.py`: **builtin policy sha unchanged**.
4. Tune and profile inputs **do** change (`bench/gate/metrics.feature_steps` and `profile/summary.py` call
   `extract`). Earlier fits, gate evidence and summaries are not comparable.
5. Synth changes: categoricals drop on the first evidence tick and the month-24 late-contact tick; every
   observation gains a field; the bus digest changes (§7).
6. Synth `PROFILES` give tech_rush and early_aggression 2 border systems per observer. Under F1 they would
   lose `border_fort_ratio` entirely. WO-F raises them.

## 2. Exact spec per feature (`src/sae/core/features.py`)

| # | Feature | Rule |
|---|---|---|
| F1 | `border_fort_ratio` | `MIN_FRONTIER = 3` (HYP). `None` unless `max(frontier_systems over views) >= MIN_FRONTIER` (one observer alone sees 3 distinct systems). The ratio itself stays pooled |
| F2 | `fleet_power_cat`, `tech_cat` | New memory fields `fleet_vis`, `tech_vis: frozenset[int] \| None = None` = observers reporting that categorical at the last evidence tick. Value is the lower median only when the current reporting set equals the stored set and is not empty; else `None`. `None` (first tick, old state) counts as changed. Sets update on evidence ticks only; a tick with nobody reporting stores `frozenset()`. The no-views early return sets both to `frozenset()` (same line that resets `vis`). A dropped tick still advances `last_evidence_mi`. `reset_session` carries both sets |
| F3 | `at_war` | `True` if any view has `player_offensive_war_count > 0`. `False` if war info is present, every `player_war_count == 0` and no `at_war_with_observer`. Otherwise `None` (defensive-only, AI-led, or role unknown). No fallback to the old rule |
| F4 | `expansion_rate` | **No code change (accepted).** A-19 fallback gains: "if `systems_owned` becomes intel-masked, samples must be per observer". Parser pin test: `systems_owned` equal across in-contact observers |

`first_war_month` / `first_war_observed` stay "any war" (report only). `core/state.py` needs no edit.

## 3. Fog legality of the attacker (C6 §4)

- War between player and observer: the observer is a party; its own side is legal.
- Third-party war sides: same class as `player_war_count` (public). UNVERIFIED, new **A-55** (M0-11).
- Legal fallback if A-55 fails: count only wars in which the observer is a defender. The field definition
  already allows it, so the fallback is no contract change.

## 4. Contract impact

| Proposal | Change |
|---|---|
| 038-T3-T4-offensive-war-count | C4 v1.2 -> v1.3, additive `LegalView.player_offensive_war_count: GameCount \| None` = "offensive wars of the player known to this observer": the player is in `attackers` and no observer country (any `sae_sid` country) is in `attackers`. Never "first attacker = leader" (list order UNVERIFIED). Validator: `<= player_war_count`. Schemas re-exported. C6 §4 doc note, **no C6 bump**. A-55 in `contracts/README.md` §5 and `docs/m0_checklist.md` table B |
| 039-T4-feature-validity | **C10 v1.3 -> v1.4** (036 moves to v1.5, 037 to v1.6). §2 lines 146-147 rewritten: `war_share`, `first_war_year` mean offensive wars; months with `at_war is None` (defensive-only, role unknown) leave the denominator and `n_obs`, so the share is biased up and a besieged turtle gets `None`. §3 priors (`war_share` 0.2, `first_war_year` 25) were set for "any war" and are HYP. A profile build never mixes builder versions. C5 and C10 note the two meanings: bus `first_war_month` (any war) vs profile `first_war_year` (offensive). C5 note: a dropped feature is `None`. Accepted scope: the second tick with a stable set can still lock alone |

## 5. Work orders (disjoint file sets)

| WO | Owner | Files | Work | h |
|---|---|---|---|---|
| WO-0 | T7 | `tools/bench/fog_adversarial_measure.py` (new), `docs/plans/opt/DEMO_V1_NUMBERS.md` | Tool (§6); "before" wrong-lock numbers and CI-1 pooled rate on the untouched tree | 3 |
| WO-A | architect | `contracts/proposals/038-*.md`, `039-*.md`, `contracts/C4_sae_parse.md`, `C5_bus.md`, `C6_game_adapter.md`, `C10_profile_policy.md`, `CHANGES.md`, `README.md`, `contracts/schemas/**`, `src/sae/contracts/c4_parse.py`, `tests/contract/**`, `docs/m0_checklist.md`, `docs/plans/opt/W3_DESIGN.md`, `docs/plans/opt/IMPLEMENTATION_WAVES.md` | §3, §4; W3 docs: C10 versions, "merged Waves 1-2 + 1.5 commit" | 3 |
| WO-B | T3 | `parser/src/features.rs`, `output.rs`, `parser/fixtures/*/expected.json` (7), new fixtures `war_ai_attacker`, `war_ai_led_player_coattacker`, `parser/tests/**` | Emit the field; both fixtures give 0; F4 pin test | 3 |
| WO-C | T4 | `src/sae/core/features.py`, `tests/unit/t4/test_features.py`, `test_hardening.py`, `test_expansion.py`, `fake_synth.py`, new `test_single_tick_property.py` | F1-F3, T-1..T-9, property test. Synth-backed and e2e tests run only after WO-B and WO-F land | 4.5 |
| WO-D | T9 | `src/sae/tune/cli.py`, `src/sae/profile/summary.py`, `tests/unit/t9/**`, `tests/unit/t7/test_gate_metrics.py` (T7 consents) | `TUNER_VERSION` and `BUILDER_VERSION` 0.1.0 -> 0.2.0; new field in war fixtures | 1.5 |
| WO-F | T2 | `src/sae/adapters/synth.py`, `tests/unit/t2/test_synth.py`, `_asm_helpers.py` | `PROFILES` borders: tech_rush 2 -> 3, early_aggression 2 -> 4 (fort fraction stays 0.0 and 0.5); helper gains the field | 1 |
| WO-E | T7 | `docs/plans/opt/DEMO_V1_NUMBERS.md`, `docs/status/T7.md`, `changelog.d/T7.md` | Full suite, CI-1, "after" numbers | 2 |
| Gate | sae-adversary | none | Wave review (4 ratings) | 1 |

Total **19 h**. Order: WO-0 -> WO-A -> (WO-B, WO-D, WO-F, WO-C unit part in parallel) -> WO-C e2e step -> WO-E
-> wave review.

## 6. Tests and measurement

Unit tests (WO-C):

- T-1 one view with frontier 1 or 2: `None`; 3: ratio. **3 observers x 1 system: `None`.**
- T-2 categorical `None` on the first tick and on an observer gained, lost, or swapped at equal size.
- T-2b sole observer lost (no views) for 24 months, then back: `None` on the return tick.
- T-3 the tick after a change counts, `k` measured from the dropped tick.
- T-4 old state (sets `None`) loads and drops once.
- T-5 AI attacker, player defender: `None`. T-6 player attacker: `True`. T-7 no wars: `False`.
- T-8 `count > 0` with the field `None`, or field 0: `None`. T-9 `reset_session` keeps both sets.

Property test (`pytest.mark.parametrize`, `test_single_tick_property.py`): **no single tick crosses 0.45 for any
feature artefact case.** Through `extract` then `belief.update`, builtin policy, `k >= 12`. Assert every belief
feature is `None` and **posterior == prior** (stronger than `< 0.45`). Priors: uniform, and one decayed from a
non-uniform posterior. Cases, alone and combined:

1. `fleet_power_cat` in -2..2, first tick.
2. `fleet_power_cat` in -2..2, reporting set changed (gain, loss, swap).
3. `tech_cat` in -2..2, both routes.
4. Sole observer lost and regained, either categorical.
5. `border_fort_ratio` 0.0 and 1.0 with one view at frontier 1 or 2.
6. Same with 3 observers x 1 frontier system.
7. War declared by a managed AI on the player.
8. War declared by a managed AI with the player as co-attacker.
9. War declared by a third party on the player.
10. `player_war_count > 0`, offensive field `None`.
11. Negative control (must cross from uniform): stable set with `fleet_power_cat = +2`; one view at frontier 3,
    ratio 0.0; a player-led offensive war.

Not covered, by decision: `expansion_rate` view growth (F4), post-gap weight of non-categorical features.

CI-1: `uv run pytest tests/e2e/test_ci1.py -q -s`, full seeds, **before (WO-0) and after (WO-E)**; both rates
recorded. Stop rule: pooled rate `< 0.90` -> stop, escalate. `THRESHOLD` is never edited.

Wrong-lock rate, before and after, same commands:

1. `uv run python tools/bench/demo_v1_measure.py 0.45 <empty-dir>`: 40 runs; lag per archetype reported.
2. `fog_adversarial_measure.py`: one seeded perturbation per run on the 40 runs' bus observations, replayed
   through `feature_steps` + `belief.update` + `strategy.select`. Runs on both trees.

| Id | Perturbation |
|---|---|
| a | One of several observers lost 12-24 months, then back |
| b | Observer swapped |
| c | One view cut to 1 frontier system |
| d | War declared by a managed AI at a random month |
| e | Save gap of 12+ months plus (a) |
| f | Sole observer lost and regained |
| g | One system bordering 3 observers |
| h | Reporting set alternating on every save |

8 x 40 = 320 runs. Wrong lock = first `table` counter is not the label's counter.

3. Pass: after <= before per perturbation; (c), (d), (f), (g) reach 0 wrong locks at the perturbed tick.
   Where before is 0, the row is marked **"not demonstrated"**, not passed.
4. (h): record the fraction of evidence ticks that kept a categorical (F2 starvation). Maintainer judges it.

## 7. Ordering vs Wave 3

**Land Wave 1.5 before the W3-0 and G-1 golden capture.** It changes the default bus, so a golden captured first
is invalidated, and a mid-Wave 3 recapture defeats flag-off invariance. No file overlap with W3-1b.
W3_DESIGN wording: "merged Waves 1-2 + 1.5 commit" (§9 W3-0, W3-1b, E2); done in WO-A.

## 8. Open items

1. WO-F changes synth profiles (HYP). Alternative: leave them and accept no border evidence for 2 archetypes.
2. CI-1 may drop (H2 sees fewer saves; dropped ticks cost more). Before/after rates make it visible.
3. One view at frontier 3 with ratio 0.0 still gives 0.68 in one tick: real evidence under D1.
4. A dropped and an unreported categorical are both `None` on the bus; only (h) measures starvation.

## Coordinator decision (2026-11-16)

WO-F (raise synth border counts) is NOT in scope for now: measure CI-1 without it. If the pooled rate falls below 0.90, stop and escalate to the maintainer.
