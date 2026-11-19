# 039-T4 — Feature validity: `war_share` / `first_war_year` mean offensive wars

Status: **ACCEPTED** (2026-11-16; maintainer-approved, Wave 1.5). Reporter: T4. Owner: architect.
Work order WO-A. Plan: `docs/plans/opt/WAVE_1_5_PLAN.md` rev 2 §2, §4; plan review
`docs/status/opt_wave_1_5_plan_review.md` blocking 4, 6 and non-blocking 2, 3. Sequenced after 038.
Contracts: C10 §2 (v1.3 → **v1.4**, rule change; no model or schema change). C5 §3: doc note, **no bump**.
Version order: 039 = C10 v1.4; proposal 036 moves to C10 v1.5, 037 to C10 v1.6 (W3 docs updated).
Consumers: T9 (WO-D: `profile/summary.py`, `tune/cli.py`), T7 (gate metrics), T6 (profile panel), T4 (WO-C).

## Problem
Wave 1.5 changes `sae.core.features.at_war` to "the player is the aggressor" (038) and lets features drop to
`None` on artefact ticks. C10 §2 still defines the two war features on `player_war_count > 0`, and nothing says
what a `None` month does to the share.

## Change 1 — C10 §2 definitions (v1.4)
| Feature | Definition |
|---|---|
| `first_war_year` | game years from start to the first month with `at_war = true` (first offensive war); `null` if never. `n_obs` = months with `at_war` not `null` |
| `war_share` | months with `at_war = true` / months with `at_war` not `null`; `null` when there is none. `n_obs` = that denominator |

## Change 2 — rules stated in C10 §2
1. **Denominator.** `at_war = null` months (defensive-only, AI-led, role unknown) leave numerator, denominator
   and `n_obs` (`profile/summary.py`, the `war` list). The share is biased up; a besieged turtle gets `0.0` or
   `null` and `first_war_year = null`.
2. **Old buses** (before C4 v1.3) have `at_war = null` in every war month; not comparable with later ones.
3. **Builder version.** `BUILDER_VERSION` 0.1.0 → 0.2.0 (WO-D). A profile build never mixes builder versions;
   other-version summaries are rebuilt from the bus.
4. **Hyperpriors are HYP.** `war_share` (0.2, 0.15) and `first_war_year` (25, 15) were set for "any war".
5. **Two "first war" meanings.** Bus `first_war_month` = any war (report only); profile `first_war_year` =
   offensive.
6. **Accepted scope.** The second tick with a stable reporting set can still lock alone (D1 stays).
7. **Wave 1.5 rev 3.1 (B1, A1).** First-save evidence counts (B1). The value guard means a categorical change is
   only learned on a tick where the reporting set is stable (A1).

## Change 3 — C5 §3 note
A `belief_update.features` value is `null` when unobserved **or dropped**; the bus does not tell them apart.
`first_war_month` keeps the "any war" meaning.

## Migration
- T9 WO-D: `BUILDER_VERSION` and `TUNER_VERSION` 0.2.0. Earlier fits, gate evidence and summaries are not
  comparable (plan §1 finding 4). Builtin policy sha is unchanged.
- Not breaking on the wire. `CONTRACT_VERSIONS` keeps `C10: 1`, `C5: 1`.

## UNVERIFIED / HYP
- The two hyperpriors above (HYP). A-55 (038) underlies `at_war`.
- Thresholds of the feature rules (`MIN_FRONTIER = 3`, reporting-set rule) are T4 code constants, HYP, and not
  part of any contract.
