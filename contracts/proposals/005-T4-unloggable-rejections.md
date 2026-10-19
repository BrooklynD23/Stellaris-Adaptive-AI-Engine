# 005-T4 — rejections that the C5 payload models cannot carry

Status: ACCEPTED (architect, 2026-10-19; C3/C5/C9 stay v1, optional widening per C5 §6: no T6 reader of
`ControlPayload.command` exists yet). Model also enforces: `command = null` only with `accepted = false` and
`error` in `invalid|too_large|unknown_cmd`. Proposed 2026-10-19. Author: T4 lead. Owner: architect. Consumers: T6 (bus, `sae ctl`, console views), T7.

## What
1. C5 `ControlPayload.command: ControlCommand` → `ControlCommand | None`. `None` when the inbox file was
   unreadable, too large or invalid (C9 §3 `error` in `invalid|too_large|unknown_cmd`); the `ControlResult`
   (with `command_id = "unknown"` or the echoed id) still carries the error.
2. C3 §4 note: for raw (non-`Intent`) input, `reserved_kind` is checked **before** `schema` (`PRIORITIZE_TECH`
   is not in the `Intent` union, so in strict order `reserved_kind` could never fire). A `schema` or
   `reserved_kind` rejection of a raw dict cannot be an `intent_rejected` event (`IntentRejectedPayload.intent:
   Intent`); it is logged as `health` `kind=invalid_event`, `component=engine`, rule id in `detail`.

## Why
C9 §3 says "every rejected command is still logged as a `control` event", but `ControlPayload` requires a
valid `ControlCommand`, so an unreadable command cannot be logged. C3 §4 says every rejection is logged as
`intent_rejected`, but the payload requires a valid `Intent`. In v0 the engine only generates valid intents, so
(2) is a documentation fix with no runtime effect; (1) loses audit rows today.

## Exact diff
- `src/sae/contracts/c5_bus.py`: `class ControlPayload: command: ControlCommand | None` (docstring: None = unreadable).
- `contracts/C5_bus.md` §3 `control` row: "C9 `ControlCommand` (null if unreadable) and `ControlResult`".
- `contracts/C3_intents.md` §4: add the note in (2) under the rule table.
Optional field widening: stays `v = 1` if T6 readers are updated in the same change (C5 §6).

## Consumers
T6 console views and `sae ctl status` must accept `command: null`. T4 switches `daemon/control.py` to log
error-only inbox items once accepted. Until then T4 completes them (result file written) without a bus event.
