# 022-T9 — Recording size cap and PLYR-redacted recordings (C8 v1.2 `record` section; C5 health kind)

Status: **ACCEPTED-WITH-CHANGES** (2026-10-19, architect, WU-1: C8 v1.2 `record`, C6 v1.3 §3, C5 v1.3 `record_capped`; change W1-8).
Original: PROPOSED (2026-10-19, T9 lead, from architecture review S5 and Wave 3 N2/N4). Reporter: T9 on behalf
of the expansion plan; implementation owners T2 (recorder) and T4 (`sae daemon --record` wiring).
Contracts: C8 §2 (new `record` section, v1.2 together with 020/021); C6 §3 (recording rule text); C5 §3
(one `health.kind`, v1.3 together with 019). Consumers: T2, T4, T6 (console alert), T7 (tests), T8 (user guide).
Plan: `docs/expansion/player_profile_and_rsi_plan.md` §2.4, §6.

## Problem
`--record` copies every autosave (real autosaves are 10–50 MB → 8–40 GB per 200-year campaign at monthly saves,
architecture review §5) and the raw `game.log` including unredacted `PLYR` lines (Wave 3 N2). Labelled real
recordings are the input of TEAM_PLAN V-1 and V-5, so they will be kept for months; today only a 5 GiB disk
warning exists.

## Decision
New C8 section `record` (defaults chosen so a forgotten `--record` cannot fill a disk):

| Key (default) | Rule |
|---|---|
| `max_bytes` (`4294967296`, 4 GiB) | per recording dir; when the next save copy would exceed it, the recorder stops copying saves (the log keeps recording) and emits one `health kind=record_capped` |
| `saves` (`"all"`) | `all` / `none` / `every_n` |
| `save_every` (`12`) | with `every_n`: copy every N-th eligible save (saves/ stays a valid replay session; replay simply sees fewer saves) |
| `redact_plyr` (`true`) | the recorded `game.log` gets the same redaction as the bus (`logtail._redact`, proposal 012); `false` only in the `dev` preset, and then the dir gets a `GOD_VIEW_INSIDE` marker file |

C5 v1.3: `health.kind` adds `record_capped` (component `adapter`, detail = bytes used). Nothing is ever deleted
(Wave 3 N4 rule kept).

`saves = "none"` is the **strict recording** mode: a log-only session, enough for audit and for the detector
replay evaluators (they read `bus.jsonl` observations), not for re-parsing. Recordings used for V-1/V-5 need
`all` or `every_n`.

## Migration
Additive; defaults change behaviour only for recordings above 4 GiB and for raw PLYR lines (now redacted).
A replay of a redacted recording is unchanged for the engine (PLYR never reached it, C6 §4.2); the god file of a
replay of a redacted recording has no PLYR fields — documented in the user guide.

## Follow-ups
architect: C8 rows, `HealthKind` enum, schemas. T2: recorder cap/redaction (`src/sae/adapters/live.py`, `synth.py`
record paths). T4: pass `record` config. T7: test that a capped recording replays; test that a recorded
`game.log` contains no PLYR sentinel. T8: user guide (supersedes the N2 sentence).
