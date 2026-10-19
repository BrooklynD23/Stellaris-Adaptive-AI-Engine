# 018-T4 — `RejectRule.deferred` for proposed-but-deferred intents

Status: ACCEPTED (2026-10-19, architect). Was PROPOSED (2026-10-19). Author: T4 lead. Owner: architect. Consumers: T6 (console views), T7 (bus checks).

## What
Add `DEFERRED = "deferred"` to C5 `RejectRule` (additive enum value) and a C3 §4 note: an `intent_rejected`
event with rule `deferred` means the intent was proposed but held back, not refused; it is re-proposed later.
Details used by T4: `deferred: no HB month counter yet (m=None); re-sent when it arrives` and
`deferred: one batch per game month; re-sent next month`.

## Why
The design report requires every proposed-but-unemitted intent to be audited (architecture review, benefit 2).
Two deferrals left no bus line: `m is None` (no `issued_m` possible, C3 §3) and the one-batch-per-month limit
after a same-month reload. T4 now audits both as `intent_rejected`, but no current rule fits the `m=None` case;
the interim uses `rate_limit` for both (exact for the per-month case, a stretch for `m=None`).

## Exact diff
- `src/sae/contracts/c5_bus.py` `RejectRule`: `+ DEFERRED = "deferred"`; schemas regenerated.
- `contracts/C3_intents.md` §4: rule table gets a non-validator row `deferred` (engine-side hold, not a refusal).
- Version: additive enum value → v1.2-style clarification, `v` stays 1 if T6/T7 readers accept it in the same change.

## Consumers
T4 switches `src/sae/core/engine_tick.py` `_deferred()` to `RejectRule.DEFERRED` (one line). T6/T7 treat it like
any other rule id (audit rows; not a validator failure).

## Decision (architect, 2026-10-19)
ACCEPTED as written. Version: C3 v1.1 → v1.2, C5 v1.2 (additive `RejectRule` value; `v`, `schema_version`,
`sae_p_schema` and `CONTRACT_VERSIONS` stay 1; README §3.7). `rate_limit` stays a refusal only.
Follow-ups: T4 — `src/sae/core/engine_tick.py` `_deferred()` uses `RejectRule.DEFERRED` with the two details
above. T6 — console views and `sae ctl status` count `deferred` separately from validator rejections (audit
rows, not failures). T7 — bus checks accept `deferred`.
