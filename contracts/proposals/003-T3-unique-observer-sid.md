# 003-T3 — Uniqueness of `observer_sid` in `ParseResult.legal`

**Status: ACCEPTED** (architect, 2026-10-19; C4 stays v1, tightening). The unstable-retry cap is routed to T4/T5, not a contract change. · Contract: C4 v1 · Breaking: no (tightening; T3 already emits unique sids) · Author: T3 lead · 2026-10-19

## What / why

`ParseResult.legal` is a tuple with no uniqueness rule, so a document with two `LegalView`s for the
same `observer_sid` validates. The T3 hardening pass produced such a save (two countries carrying the
same `sae_sid`, e.g. a mod bug or a copied save). T3 now drops duplicated sids (stderr warn), but the
model should reject the shape so no producer (Replay recordings, hand-written fixtures) can feed it to T4.

## Exact diff (`src/sae/contracts/c4_parse.py`, `ParseResult`)

```python
    @model_validator(mode="after")
    def _unique(self) -> ParseResult:
        sids = [v.observer_sid for v in self.legal]
        if len(sids) != len(set(sids)):
            raise ValueError("observer_sid values in legal must be unique")
        return self
```

C4 §3 gains: "`observer_sid` is unique in `legal`. Countries sharing a `sae_sid` are still listed
truthfully in `countries` (sid and managed as in the save) but are excluded from `self_state` and `legal`,
with a stderr warning."

## Consumers

T4 (daemon), T2 (Replay/Synth), T7. No migration.

## Related, for T4/T5 (not a contract change)

A permanently corrupt save (bad CRC) exits 6 `unstable` on every attempt. The daemon's retry of
`unstable` needs a cap (e.g. 3 tries per file, then treat as `parse_failed`).
