# 017-Wave3 — Neutral `sae_in.ini` on close/`mp=1`; Ironman detect and observe only

Status: ACCEPTED (2026-10-19, architect, recording maintainer decisions). Reporter: Wave3
(`docs/status/WAVE3_review.md` N3, N6). Contracts: C2 §2.1, §5, §6 (v1.2); C6 §1.8–§1.10, §3, §6, §7
(v1.2); C5 §3 + example (v1.2); C4 §1 (clarification, stays v1). Owners of follow-ups: T2, T4, T3, T7, T8.

## Problem
- N3: `sae_in.ini` is never neutralised. After the daemon exits, a later campaign (sids restart at 1)
  or a multiplayer game can apply campaign A's econ/posture to an unrelated AI if the player presses
  the hotkey.
- N6: LIVE ignores `ironman.sav`, so an Ironman player gets a monthly `ack_alarm` with no explanation.
  "Ironman has no live loop" was enforced by the game, not by SAE.

## Decision (maintainer, 2026-10-19)
1. **N3 neutral file.** On adapter `close()` (clean exit incl. SIGTERM) and once per transition to
   `HELLO mp=1`, the shared write path atomically rewrites `sae_in.ini` with the knob line only:
   `period`/`quiet` from the latest `HB`/`HELLO` (else 1/0), no country lines, no seq. Never deleted.
   Event: `health ini_neutralised` (detail `close`|`multiplayer`); failure → `health ini_write_failed`
   (detail `neutralise <trigger>: <cause>`), outside the `ack_alarm` count. Not in release mode; under Ironman
   the `close()` write still happens (amendment below).
2. **N6 Ironman.** LIVE parses `ironman.sav` (modified after adapter start) meta-only via
   `SaveParser.parse(path, sections=("meta",))`. `meta.ironman == True` latches observe-only for the
   adapter's lifetime: one `health ironman_observe_only`, every `emit()` →
   `EmitReceipt(ok=False, error="ironman")` with nothing written; the `close()` neutral write still happens. The engine never
   tracks such a batch for ACKs: no LOST, no re-emit, no `ack_alarm`, no per-batch `ini_write_failed`.
   `ironman == None` (A-20) → no detection.

C4: existing `--sections meta` covers meta-only; no new mode (clarification only).

## Amendment (maintainer, 2026-10-19)
Under Ironman SAE still neutralises `sae_in.ini` on exit (clean exit and SIGTERM): the `close()` write
happens even when latched; Ironman still blocks every intent write. Clarification, stays v1.2.

## Version: v1.2 (README §3.7), not a bump, not a bare clarification
Models change (`HealthKind` +2, `EmitReceipt.error` + `ironman`, `SaveParser.parse` keyword `sections`)
and rules change, but the mod sees no new bytes (the neutral file is an ordinary knob-only file),
log lines are unchanged and `v`/`schema_version`/`sae_p_schema` stay 1. `CONTRACT_VERSIONS` unchanged.

## Follow-ups
T2 (`src/sae/adapters/`):
1. `live.py`: neutral write in `close()` (first call, before iterators end) and on `HELLO mp=1` transition,
   through the existing `AtomicIniWriter`; track latest `period`/`quiet` from HB/HELLO.
2. `live.py:62`: in LIVE watch `ironman.sav` (mtime after start), `parse(path, sections=("meta",))`,
   latch on `meta.ironman is True`, one-shot health, `emit()` → `error="ironman"`.
3. Fake parsers in `tests/unit/t2/test_{live,replay,synth,make_adapter}.py` accept `*, sections=None`.
4. Tests: close writes the neutral text byte for byte; mp=1 HELLO writes it once; Ironman latch refuses
   emits but close still writes the neutral file; old `ironman.sav` (mtime before start) ignored.

T4 (`src/sae/daemon/`, `src/sae/core/`):
1. `saveparser.py:105`: accept `*, sections=None` and pass `--sections a,b` to `sae-parse`.
2. Shutdown: SIGTERM/SIGINT and the C7 self-restart run `await adapter.close()` before closing the sink.
3. Engine: `error="ironman"` receipts → no ACK tracking, no LOST/re-emit, no `ack_alarm`, no
   `ini_write_failed`.

T3: none required (`--sections meta` exists). Optional later: skip inflating `gamestate` when the zip
`meta` entry has a date.

T7: `docs/` user guide §6 (Ironman = observe only, one health line) and §7 (file neutralised on exit/MP).
T8: M0-14 checks `ironman=yes` in `ironman.sav` meta (A-20 row widened).
