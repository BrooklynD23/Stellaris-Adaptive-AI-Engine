# 038-T3/T4 — `LegalView.player_offensive_war_count` (who declared the war)

Status: **ACCEPTED** (2026-11-16; maintainer-approved, Wave 1.5). Reporters: T4 (features), T3 (parser).
Owner: architect. Work order WO-A. Plan: `docs/plans/opt/WAVE_1_5_PLAN.md` rev 2 §2 F3, §3, §4; plan review
`docs/status/opt_wave_1_5_plan_review.md` blocking 3, 4, 5 and non-blocking 1.
Contracts: C4 §3, model `LegalView` (v1.2 → **v1.3**, `schema_version` stays 1). C6 §4: doc note, **no bump**.
Schema change: `ParseOutput.schema.json` and every schema that embeds `LegalView` (one optional field).
Consumers: T3 (WO-B, emits it), T4 (WO-C, `features.at_war`), T2 (carries `player_legal` unchanged; test
helpers), T9 / T7 (WO-D, fixtures), T8 (A-55 row, kit step).

## Problem
`at_war` is true for any war the player is in. A war a managed AI declares on the player then reads as player
aggression, moves the belief toward `early_aggression`, and the engine reacts to its own action (self-induced
loop). `LegalView` carries no side information; the parser already reads `attackers` / `defenders` per war.

## Change 1 — model field (C4 v1.3)
```python
player_offensive_war_count: GameCount | None = None
```
Validator (in `LegalView`): when both are set, `player_offensive_war_count <= player_war_count`.
The existing rule holds: not in contact → the field is `None`.

## Change 2 — definition (C4 §3)
"Offensive wars of the player **known to this observer**": the number of wars where
1. the player is in `attackers`, and
2. no other country carrying `sae_sid` (managed or not) is in `attackers`.

- A war a managed AI declared with the player as ally, federation member or subject (co-attacker) is **not**
  counted (review blocking 3).
- Never "first attacker = war leader": the order of `attackers` in the save is UNVERIFIED.
- Parser rule today: count every such war (third-party sides are public, A-55). Fallback if A-55 fails: count
  only those with this observer in `defenders`. Both satisfy the definition, so the fallback is no contract
  change; a player war on a third party then gives 0 with `player_war_count > 0`.
- `null` = not known: parser older than v1.3, `wars` section not run, not in contact.

## Change 3 — C6 §4 item 6 (note)
The field rides in `Observation.player_legal`; `Observation` gains no field. Legality as above. No C6 version
change (no shape or rule of C6's own changes; proposal 036 keeps its "C6 +1 minor").

## Consumer rule (T4, informative; `WAVE_1_5_PLAN.md` §2 F3)
`at_war = True` if any view has the field `> 0`; `False` if war info is present, every `player_war_count == 0`
and no `at_war_with_observer`; otherwise `None`. No fallback to the old rule.

## Migration and direction
- Models land first (this change). JSON without the field (parser ≤ 0.1.2, recorded buses, `state.json`) loads
  with `None`. A parser emitting the field against older models is rejected (unknown field), so WO-B follows.
- T3 WO-B: `parser/src/features.rs`, `output.rs`, 7 `expected.json`, fixtures `war_ai_attacker` and
  `war_ai_led_player_coattacker` (both 0). A parser version bump tells the outputs apart.
- Not breaking. `CONTRACT_VERSIONS` keeps `C4: 1`, `C6: 1`. Proposal numbers 036 and 037 stay reserved.

## UNVERIFIED
- **A-55** (new; README §5, C4 §8, C6 §7, `docs/m0_checklist.md` table B): sides of wars an observer is not in
  are public to it, and the save lists both sides per war. A-50..A-54 are reserved for proposal 036.
- No `sae m0` check lists A-55 yet (T8 follow-up: M0-11 step). The plan text says "M0-5"; the save-vs-UI
  comparison is M0-11, so the architect names that one.
