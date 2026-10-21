# 011-T2 — Replay/synth: the consumer handles an observation before pulling the next

Status: ACCEPTED (architect, 2026-10-19; C6 stays v1.1, clarification of the §3 determinism promise; no model or
wire change). Recorded as C6 §1.7 with consumer rules (a) observation lockstep and (b) ACK-before-next-observation,
and the adapter-side ordering obligation. T4 implements (a)/(b) in `_feed_obs`; T2 keeps the handoff until then. Proposed Author: T2 lead. Owner: architect. Consumers: T4 (daemon loop), T7 (e2e harness).
Breaking: no (C6 §1 clarification; behaviour of the live path unchanged).

## Problem
Replay and synth are pull-driven so they can be deterministic (C6 §3: "deterministic for a given seed and
`speed=max`"). The `echo`/`drop`/`delay` ack models apply a written `sae_in.ini` at the first simulated pulse after
`emit` returns. T4's loop (`src/sae/daemon/loop.py` `_feed_obs`) pulls observations into an unbounded queue
without waiting for the consumer, so at `speed=max` the adapter runs to the end of input before the engine emits
anything: zero ACKs (reviewer repro, T2 U5 review), and with synth's subprocess step the interleaving depends on
wall-clock time, which breaks the determinism promise.

## Proposed change (C6 §1, new semantics item 6)
"In `replay` and `synth` modes the consumer handles each observation completely, including any `emit` it causes,
before it pulls the next observation. Live mode is unchanged." T4: for `AdapterMode.REPLAY`/`SYNTH`, `_feed_obs`
awaits `_on_obs` inline (or waits on a per-observation done event) instead of only enqueueing.

## Interim (what T2 ships now)
After yielding each observation, replay/synth hand off control for a fixed number of scheduler steps
(`await asyncio.sleep(0)` × N) and their `emit` completes without real awaits (synchronous scratch write). This works
with today's loop because `_on_obs` → `emit` contains no real I/O awaits, but it is a timing convention, not a
guarantee. A loop-shaped regression test pins it.
