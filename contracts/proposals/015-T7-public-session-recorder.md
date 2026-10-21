# 015-T7 — Public session recorder for synth (T2)

Status: ACCEPTED-WITH-CHANGES (2026-10-19). Contracts: C6 §3 (determinism), C8 §5 (`sae replay`). Owner asked: T2. Consumers: T7.

## What
`SynthAdapter(..., record_dir: Path | None = None)` writes the LiveAdapter record format (`game.log`,
`game.log.ts`, `saves/<offset:012d>-<name>.sav`) exactly as `LiveAdapter(record_dir=…)` does, so that a synth campaign
can be replayed with `sae replay`. Optionally `sae synth --record <dir>`.

## Why
CI-1 (resource proposal §8) says "replay a recorded campaign". No public recorder exists for synthetic campaigns; T7
runs CI-1 on synth (equivalent for a deterministic synth run) and builds sessions for replay tests in
`tests/e2e/_session.py` from `ModSimulator` + `sae-parse fixture`. Both go away once this lands.

## Diff
Additive keyword-only parameter with default `None`; no model or wire change. Not breaking (v1.1 clarification).

## Migration
T7 switches `tests/e2e/test_ci1.py` to record + replay, deletes `_session.py`.

## Decision (architect, 2026-10-19)
Accepted with these exact rules (C6 §3, C8 §5; no version move, additive):
1. `SynthAdapter(..., *, record_dir: Path | None = None)`. When set, after each pulse append that pulse's C1 lines
   (the exact strings the simulator printed, ACK lines included, LF-terminated, UTF-8) to `record_dir/game.log`,
   then one line `<size_of_game.log> <i * 1_000_000_000>` to `record_dir/game.log.ts` (`i` = 1-based pulse index;
   synthetic monotonic ns, one second per month, so `--speed realtime` paces at 1 month/s).
2. Each rendered fixture save is copied, before the scratch copy is deleted, to
   `record_dir/saves/<size_of_game.log:012d>-autosave_<yyyy.mm.dd>.sav` (size after that month's lines).
3. ACK lines belong to the pulse that printed them and are recorded with it, so
   `sae replay --ack-model from_recording` reproduces the acks.
4. `record_dir` must not exist or be empty, else the CLI exits 2 (`record dir not empty`). A recording error is
   reported once as `health invalid_event` (`record: <ExcType>`) and recording stops; the run continues.
5. CLI: `sae synth --record <dir>` (C8 §5).
6. Acceptance: `sae synth --archetype turtle --years 5 --seed 1 --record D`, then
   `sae replay D --speed max --ack-model from_recording` yields the same (game_date, strategy) sequence of
   `strategy_selected` events; a unit test in `tests/unit/t2/` asserts it.

Not in scope: `sae daemon --record` for live sessions (LiveAdapter already takes `record_dir`; a config/CLI knob is
a later proposal, needed before M0 recordings).
