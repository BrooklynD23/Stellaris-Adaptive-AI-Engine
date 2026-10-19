# 001-T3 — C4 `--sections` subset semantics for `self_state` / `god`

**Status: ACCEPTED** (architect, 2026-10-19; C4 stays v1, clarification) · Contract: C4 v1 · Breaking: no (doc-only) · Author: T3 lead · 2026-10-19

## What

C4 §1 says fields of non-extracted sections are "`null`/absent per the schema", but `SelfState` and
`GodView` have only required numeric fields, so a subset run cannot fill them honestly without imputing.

## Exact diff (C4 §1, after the `--sections` bullet)

```diff
 - `--sections`: subset of `meta, countries, starbases, fleets, wars, tech`; default all. Unknown name → exit 2.
   Fields whose section was not extracted are `null`/absent per the schema; `sections` in the output lists what ran.
+  `meta` is always extracted and listed. `self_state` and `god` are produced only when all six sections ran;
+  otherwise `self_state = {}` and `god = null` (not observer mode — check `sections`), and a stderr
+  `level=warn` line says so. Without `countries`: `countries = []`, `legal = []`. Each `LegalView` feature
+  whose section did not run is `null`. The live loop always passes all sections (seq continuity, C3 §5).
```

No model change.

## Consumers

T4 (daemon parse step; must not pass a subset in the live loop), T2 (Replay/Synth), T7 (fixtures).

## Migration

None. T3 implements this behaviour now; it is consistent with the current models.
