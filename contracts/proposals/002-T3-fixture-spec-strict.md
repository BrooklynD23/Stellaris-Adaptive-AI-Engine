# 002-T3 — Strict `FixtureSpec` models (parity with the Rust renderer)

**Status: ACCEPTED** (architect, 2026-10-19; C4 stays v1, tightening). Spelling: per-field `Strict()` (model-level
`strict=True` would reject Python lists for tuples); `schema_version` is a strict int pinned to 1; the model also
rejects self-loop hyperlanes, self-contacts and `pad_bytes` > 2 GiB. Wars sides are strict ids ≥ 0 too. · Contract: C4 v1 §5 · Breaking: no for generated specs (tightening; hand-written specs with lax types) · Author: T3 lead · 2026-10-19

## What

`sae-parse fixture` deserialises `FixtureSpec` with serde (exact JSON types, `deny_unknown_fields`).
The pydantic models are lax, so pydantic accepts specs the renderer rejects with exit 2:

- `3.0` for int fields, `"true"`/`1` for bools, `"1.5"` for floats, `schema_version: 1.0`, `pad_bytes: 1000.0`;
- negative ids in `FixtureCountry.systems`, `FixtureSpec.hyperlanes`, `FixtureSpec.contacts` (no `ge=0`).

## Exact diff (`src/sae/contracts/c4_parse.py`)

```diff
-class FixtureCountry(Frozen):
+class FixtureCountry(Frozen, strict=True):
 ...
-    systems: tuple[int, ...] = Field(default=(), description="Ids of owned systems.")
+    systems: tuple[Annotated[int, Field(ge=0)], ...] = Field(default=(), description="Ids of owned systems.")
-class FixtureStarbase(Frozen):
+class FixtureStarbase(Frozen, strict=True):
-class FixtureWar(Frozen):
+class FixtureWar(Frozen, strict=True):
-class FixtureSpec(Frozen):
+class FixtureSpec(Frozen, strict=True):
-    hyperlanes: tuple[tuple[int, int], ...] = ()
+    hyperlanes: tuple[tuple[NonNegInt, NonNegInt], ...] = ()
-    contacts: tuple[tuple[int, int], ...] = Field(default=(), ...)
+    contacts: tuple[tuple[NonNegInt, NonNegInt], ...] = Field(default=(), ...)
```

(`strict=True` must still accept JSON arrays for tuples when validating with `model_validate_json`; the
architect confirms the exact pydantic spelling.) C4 §5 gains one line: "Specs are strictly typed; the
renderer also rejects self-contacts, self-loop hyperlanes, names containing `\` or control characters,
`pad_bytes` > 2 GiB and count fields > 1 000 000 (exit 2)."

## Consumers

T2 SynthAdapter (emits specs via `model_dump_json`, already exact-typed: no change expected), T7.

## Until accepted

The renderer stays strict; T2 sees exit 2 only for lax hand-written specs.
