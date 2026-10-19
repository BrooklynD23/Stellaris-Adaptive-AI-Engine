# 009-T2 — A health channel for adapters

Status: ACCEPTED, option C (architect, 2026-10-19; C6/C8 stay v1.1, clarification: C8 §5.1 already allows extra
keyword-only parameters with defaults). Recorded as C6 §1.8 and C8 §5.1 `make_adapter(..., *, health=None)`.
Options A and B rejected (model change not needed). With `health=None` adapters only log; the `_sink_health`
`sae.core` import goes once T4 passes `health=`. Proposed Author: T2 lead. Owner: architect. Consumers: T4 (daemon, wiring), T6 (bus writer).
Breaking: no (additive).

## Problem
C6 §1.5 and C1 §5 require adapters to report bad input as `health` events (`log_parse_error`, `version_skew`,
`reconnect`, `ini_write_failed`, `ack_alarm`, `invalid_event`). But the `GameAdapter` protocol has no health
output, and `make_adapter(config, parser, god_sink)` / `AdapterFactory(parser, god_sink)` only hand the adapter a
`GodSink`. In production that object is T4's `BusSink`, which also has `emit(EventDraft)`, but `EventDraft` lives
in `sae.core.state` (T4), so an adapter would have to import `sae.core` and duck-type the sink.

## Proposed change (T2 now prefers C, adversary advice; A and B kept as alternatives)
C. No contract model change. `sae.adapters.make_adapter(config, parser, god_sink, health=None)` gains an optional
`health: Callable[[HealthKind, str | None], None]`. T4's `wiring.live_adapter_factory` / loop passes
`lambda kind, detail: self._health(kind, "adapter", detail)` (same shape as `GovernedSaveParser`'s `HealthFn`).
Replay/synth adapters take the same argument. Needs a one-line T4 change in `wiring.py`; `AdapterFactory` unchanged.

A. Add to C6 (`src/sae/contracts/c6_adapter.py`):

```python
class HealthSink(Protocol):
    def emit_health(self, payload: HealthPayload, game_date: GameDate | None) -> None: ...
```

The daemon's sink (T6 writer / T4 BusSink) implements it; the bus writer stamps `state`/`level` (C5 §2 already says
so for adapter-raised events). `AdapterFactory` stays `(SaveParser, GodSink)`; adapters use `emit_health` when the
sink offers it.

B. Move `EventDraft` from `sae.core.state` to `sae.contracts.c5_bus` (re-exported by T4), so adapters build drafts
without importing `sae.core`.

## Interim (what T2 ships now)
`make_adapter` already accepts `health`. When it is `None`: `sae.adapters._sink_health(sink)` (in `src/sae/adapters/__init__.py`): if the sink has `emit`, wrap a `HealthPayload(component="adapter",
state=PLAYING, level=H0, …)` in `sae.core.state.EventDraft` (imported lazily) and call `sink.emit`; otherwise log
only. Replaced by A or B once accepted.
