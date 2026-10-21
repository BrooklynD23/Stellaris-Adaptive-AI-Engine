# 004-T4 — daemon entry takes an adapter factory

Status: ACCEPTED (architect, 2026-10-19; C8 stays v1, additive). `AdapterFactory` lives in
`sae.contracts.c6_adapter`. Proposed 2026-10-19. Author: T4 lead. Owner: architect. Consumers: T2 (replay/synth CLIs), T7 (e2e harness).

## What
C8 §5 `run` signature changes from

```python
async def run(config: SaeConfig, adapter: GameAdapter, run_id: str) -> int: ...
```

to

```python
AdapterFactory = Callable[[SaveParser, GodSink], GameAdapter]
async def run(config: SaeConfig, adapter: GameAdapter | AdapterFactory, run_id: str) -> int: ...
```

A `GameAdapter` instance keeps working (backward compatible). A factory is called once by the daemon with the
daemon-owned, Governor-gated `SaveParser` (C7) and the bus writer's `GodSink` (C5/C6).

## Why
C6 says `SaveParser` is "injected by the daemon" and runs under Governor control, and god data goes to the bus
writer's god file. But `sae replay`/`sae synth` (T2) build the adapter *before* calling `run`, so they cannot get
either object: the only options today are an ungoverned parser and a second bus writer (two writers on one run
dir, breaking C5 single-writer). A factory keeps the bus, parser, run dir and CURRENT owned by the daemon alone.

## Exact diff (C8 §5)
Replace the code block with the one above and add: "`adapter` may be a `GameAdapter` (its parser and god sink
are the caller's responsibility) or a factory the daemon calls with its governed `SaveParser` and the bus
writer's `GodSink`. Replay/synth CLIs should pass a factory."

## Consumers
- T2: replay/synth CLIs pass `lambda parser, god: SynthAdapter(..., parser=parser, god_sink=god)`.
- T7: e2e harness may use either form.
- T4: implements both forms now (no behaviour change for the instance form).

## Alternatives rejected
`open_runtime(config, run_id) -> DaemonRuntime` + optional `runtime=` arg: second public object, split lifecycle
(who closes the bus, who owns CURRENT). Adversary advice, accepted.
