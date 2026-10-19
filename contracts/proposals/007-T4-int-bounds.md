# 007-T4 — Upper bound on seq, sid, m and LegalView integers

Status: ACCEPTED (architect, 2026-10-19; C1/C4/C6 stay v1, clarification under A-17/D-06). Proposed
2026-10-19. Author: T4 lead. Owner: architect. Consumers: T2 (adapters, simulator), T3 (parser), T4.

## What
Every integer that crosses the game boundary is at most `CLAUSEWITZ_INT_MAX` = 1,000,000
(`sae.contracts.common`, A-17, M0-5). Already bounded before this proposal: `Sid`, `Seq`, `MonthIndex`.
Newly bounded (`le=CLAUSEWITZ_INT_MAX`):

| Contract | Model.field | New type |
|---|---|---|
| C1 | `SnapRecord.seq`, `AckRecord.applied` | `SeqOrZero` |
| C4 | `SelfState.applied_seq`, `SelfState.pending_seq`, `FixtureCountry.applied_seq`/`pending_seq` | `SeqOrZero` / `le=` |
| C4 (used by C6) | `LegalView.systems_owned`, `frontier_systems`, `fortified_border_starbases`, `player_war_count` | `GameCount` |
| C6 | `ManagedState.applied_seq`, `ManagedState.pending_seq`, `IntentAck.applied` | `SeqOrZero` |

New aliases in `common.py`: `SeqOrZero` (0 = none) and `GameCount`, both `ge=0, le=CLAUSEWITZ_INT_MAX`.
Not bounded: C5 envelope `seq` (a daemon-side line counter, never written into the game).

## Why
T4 clamped or ignored values ≥ the cap itself because the contracts allowed any int. A bound in the
model rejects the value at the producer (parser, adapter), once, instead of in every consumer.

## Error handling
- C1: an out-of-range value makes the line a parse error (existing C1 bad-line handling).
- C4: the parser never emits a larger value; an out-of-range save value is exit 5 `parse`, never clamped.
- C6: an adapter drops the record and reports `health invalid_event`; it never builds an out-of-range model.
- The bound is inclusive: 1,000,000 is valid.

## Consumers
T2: adapters/simulator must catch `ValidationError` on these fields (C6 §2). T3: range check before
emitting JSON. T4: three `tests/unit/t4/test_hardening.py` tests build out-of-range models and now fail
at construction; switch them to asserting `ValidationError` (or drop them), and treat `seq ==
CLAUSEWITZ_INT_MAX` as valid in `core/lifecycle.py` (it uses `>=`).
