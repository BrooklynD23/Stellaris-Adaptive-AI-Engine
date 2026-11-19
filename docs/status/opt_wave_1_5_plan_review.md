# Wave 1.5 plan: adversarial pre-implementation review

2026-11-16, `sae-adversary`. Read-only review of `WAVE_1_5_PLAN.md` against the tree on `feat/opt-wave-a1`.
Nothing was run; findings come from reading `src/sae/core/features.py`, `src/sae/core/belief.py`,
`src/sae/profile/summary.py`, `parser/src/features.rs`, `parser/src/extract.rs`, `src/sae/adapters/synth.py`,
`docs/plans/opt/W3_DESIGN.md`, `docs/plans/opt/IMPLEMENTATION_WAVES.md`.

## Verdict: ACCEPT WITH CHANGES (7 blocking)

## Blocking

1. **F1 pooling reintroduces the one-system artefact (WO-C).** `frontier_systems` is per observer, so one player
   system bordering 3 managed AIs gives pooled 3 and passes `MIN_FRONTIER`; its starbase is counted 3 times too,
   so the ratio is 0.0 or 1.0 (0.68 / 0.66 in one tick). This is the normal SAE layout (several managed AIs
   next to the player).
   Fix: gate on `max(frontier_systems over views) >= MIN_FRONTIER` (one observer alone sees 3 distinct systems);
   keep the pooled ratio. Add to T-1 and property case 4: 3 observers x 1 frontier system -> `None`.
   Recheck plan finding 5 for synth (each AI must border >= 3 player systems, not "pooled >= 6").

2. **F2 misses the sole-observer gap (WO-C).** `extract` returns early when there are no in-contact views and
   resets only `vis`. With the plan as written `fleet_vis` / `tech_vis` keep `{A}`; when A returns after 24
   months the set is "unchanged", `k = 24`, weight 1.0, prior decayed to near uniform: one tick, 0.685. This is
   the arch review's "first tick back after a gap" route and plan perturbation (a) with one observer.
   Fix: set both sets to `frozenset()` in the no-views early return (same line that resets `vis`). State that an
   evidence tick with nobody reporting stores the empty set. Add T-2b and a property case: sole observer lost
   and regained, `k >= 12`, prior decayed from a non-uniform posterior (not only from uniform).

3. **F3 leaves the self-induced loop open through alliances (WO-A, WO-B).** "Player's country in `attackers`"
   is true when a managed AI declares a war and the player joins as its ally, federation member or subject.
   Follow-up 3 was "exclude wars where a managed AI is the attacker".
   Fix in 038 and `parser/src/features.rs`: count a war only if the player is in `attackers` and no observer
   country (any `sae_sid` country) is in `attackers`. New fixture `war_ai_led_player_coattacker` -> 0.
   Do not use "first attacker = war leader": list order in the save is UNVERIFIED.

4. **Contract version collision with Wave 3 (WO-A).** 039 takes C10 v1.3 -> v1.4; proposal 036 already claims
   C10 v1.4 and 037 v1.5 (`IMPLEMENTATION_WAVES.md` lines 88-89, `W3_DESIGN.md` line 344). 038's C6 note also
   meets 036's "C6 +1 minor".
   Fix: 1.5 lands first, so 039 = C10 v1.4, 036 -> v1.5, 037 -> v1.6; 038 states whether C6 bumps (a doc note
   with no shape change should not). Add `docs/plans/opt/W3_DESIGN.md` and `IMPLEMENTATION_WAVES.md` to the
   WO-A file set. Proposal file numbers 038/039 are free (highest on disk is 035; 036/037 reserved, no clash).

5. **Assumption id: use A-55, not A-50 (WO-A).** W3_DESIGN rev 2.1 uses A-50..A-54 in its rows and in prose
   (line 537 "If A-50 is false"), and that revision is already reviewed. `tests/unit/t8/test_checklist_drift.py`
   matches `A-\d{2}` and must be checked for a contiguity assumption; if it has one, register A-50..A-54 as
   "reserved (proposal 036)" rows rather than renumbering W3.

6. **Profile meaning change is not versioned where it is built (WO-D, 039).** `BUILDER_VERSION` in
   `src/sae/profile/summary.py:27` stays 0.1.0; only `TUNER_VERSION` moves. Summaries and `ProfileDoc`s built
   before and after are indistinguishable.
   Fix: bump `BUILDER_VERSION` to 0.2.0 (add `summary.py` to WO-D) and state in 039 that the profile build never
   mixes builder versions. Also state the denominator: `summary.py:132` drops `at_war is None` samples, so
   defensive-war months leave `war_share`'s denominator (share is biased up; a besieged turtle gets `None`).
   039 must rewrite C10 section 2 lines 146-147, the `n_obs` meaning, and say the section 3 priors
   (`war_share` 0.2, `first_war_year` 25) were set for "any war" and are HYP.

7. **Measurement gaps make the pass rule partly vacuous (WO-0, WO-E).**
   - Add perturbations: (f) sole observer gap (item 2), (g) one system bordering 3 observers (item 1),
     (h) reporting set alternating on every save. For (h) report the fraction of evidence ticks that kept a
     categorical; this is the starvation measure for F2. Record it; maintainer decides if it is too low.
   - Record the CI-1 pooled rate before (WO-0) as well as after. A fall from 0.99 to 0.91 passes the stop rule
     and would otherwise be invisible.
   - "after <= before" proves nothing where before is 0; state per perturbation the before count that makes
     the comparison meaningful, or mark it "not demonstrated".

## Non-blocking

1. Fog: third-party war sides are the same class as the existing `player_war_count` (public, UNVERIFIED). The
   "observer is a defender" fallback is legal and sufficient; under it a player war on a third party gives
   field 0 with count > 0, hence `None`, which is consistent. Define the field in 038 as "offensive wars of
   the player known to this observer" so the fallback is not a later contract change.
2. F2 starvation is not permanent in normal play: the store updates on a dropped tick, so the cost is one save
   per set change. It is permanent only when the reporting set differs on every save (intel flicker). With
   Wave 3 carry the first tick of a session drops only if sids changed; acceptable. Under D1 the second tick
   with a stable set still locks alone at 0.685 with yearly autosaves; this is accepted scope, say so in 039.
3. Two meanings of "first war" now coexist: bus `first_war_month` (any war) and profile `first_war_year`
   (offensive). Write this in C5 and C10.
4. Tests outside the WO sets build `LegalView` with wars: `tests/unit/t2/_asm_helpers.py`,
   `tests/unit/t4/test_expansion.py`. Check them before the parallel step.
5. Parser scope is right: 7 of 9 `expected.json` carry legal views. Risk is low (additive field).
   WO-C cannot run synth-backed tests green until WO-B lands (synth goes through the real parser): old field
   `None` means `at_war` is never `True`. Order WO-B before the WO-C e2e step.
6. The property test is a finite enumeration; `pytest.mark.parametrize` is enough. After the fixes the artefact
   cases give all-`None` features, so assert the posterior equals the prior, which is stronger than `< 0.45`.
   Not in the arch review list and not covered: `expansion_rate` view growth (accepted, F4), post-gap weight for
   non-categorical features.
7. Ordering before the W3-0 / G-1 golden capture is correct.

## Ratings

| Area | Rating | Why |
|---|---|---|
| 1. Performance and memory | ON TARGET | Two small frozensets, one integer per view |
| 2. Telemetry | AT RISK | A dropped categorical and an unreported one are both `None` on the bus; starvation is not measurable until item 7(h) |
| 3. Adaptive engine | AT RISK | Items 1-3 leave three single-tick lock routes open |
| 4. Learning the profile from saves | AT RISK | Items 4 and 6: version collision, unversioned change of `war_share` / `first_war_year` |

## Bottom line

The plan is the right size and the right order, but as written three of the artefact routes it targets survive
(shared frontier system, sole-observer gap, ally of a managed-AI attacker). Fix items 1-7 in the plan text
before WO-A starts; none needs a maintainer decision.
