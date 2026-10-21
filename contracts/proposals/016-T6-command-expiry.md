# 016-T6 — Control command expiry; canonical `set_layer`

Status: ACCEPTED (2026-10-19, architect). Reporter: T6. Contracts: C9 §2, §3, §3.1 (v1.1); C5 `control` row;
C8 §2. Owners of follow-ups: T6, T4.

## Problem
1. C9 had no age limit. A command left in `inbox/` (e.g. `sae ctl pause` or `force-strategy` that timed out
   because no daemon ran) was applied by the next daemon start or `--resume`, possibly hours later.
2. The `--set K=V` layer builder was copied in three CLIs (`src/sae/control/cli.py:62`,
   `src/sae/adapters/cli.py:55`, `src/sae/daemon/cli.py:52`) and duplicated the body of `c8_config.env_layer`.

## Decision
1. Daemon side (C9 §3.1): at pickup, `command_expired(issued_at, now, session_started_at)` from
   `sae.contracts.c9_control` rejects a command with `error="expired"` when it was issued before this daemon
   session started, more than `COMMAND_MAX_AGE_S = 60` s ago, or more than `COMMAND_MAX_FUTURE_S = 5` s in
   the future. Checked once at pickup, never at the tick boundary. The bus `control` event carries the parsed
   command (not `null`). No run-id field is added: the inbox path already scopes by run, and the session-start
   rule covers `--resume` of the same run.
2. `sae ctl` side (C9 §2.1): writes nothing unless `runs/CURRENT` names the target run with a live pid; else
   exit 1 `no running daemon`.
3. `c8_config.set_layer(sets)` is the canonical `--set` builder; `env_layer` shares its helper.

Why 60 s: the daemon polls every 2 s and `sae ctl` waits 10 s; 60 s leaves room for a slow disk without
letting a forgotten command survive a restart. Same machine clock on both sides, so skew is only the 5 s guard.

## Diff
`ControlResult.error` adds `"expired"` (additive, v1.1); new constants and `command_expired`; new
`c8_config.set_layer`. Command file format unchanged. Schemas regenerated.

## Follow-ups
T6 (`src/sae/control/`):
1. `inbox.py`: `open_inbox(run_dir, started_at=None)` (None → `datetime.now(UTC)`) and `FileInbox(..., started_at,
   now=lambda: datetime.now(UTC))`; in `_read`, after the `command_id` check, if `command_expired(...)` returns a
   detail, return `InboxItem(command=cmd, error=ControlResult(command_id=cmd.command_id, accepted=False,
   error="expired", detail=..., applied_at=None, status=None), handle=stem)`.
2. `cli.py`: before writing, require `runs/CURRENT` to name `run_id` with a live pid (reuse the C8 §6 liveness
   check); otherwise `no running daemon`, exit 1.
3. `cli.py:62`: delete `set_layer`, import `sae.contracts.c8_config.set_layer`.
4. Tests: stale file at startup → `expired`; 61 s old → `expired`; fresh → applied; no live daemon → nothing written.

T4 (`src/sae/daemon/`):
1. `control.py` `apply_item`: an item with `error is not None` is completed with that error and logged with
   `ControlPayload(command=item.command, result=item.error)` (today it only handles `command is None`); never
   call `engine.on_control` for it.
2. `ports.py` `InboxItem` docstring: "a valid command, a ready-made error, or both (expired)".
3. `cli.py:52`: delete `_set_layer`, import `set_layer` from `sae.contracts.c8_config`.

Owner of `src/sae/adapters/cli.py` (T2): `cli.py:55`, delete `_set_layer`, import the canonical one.
