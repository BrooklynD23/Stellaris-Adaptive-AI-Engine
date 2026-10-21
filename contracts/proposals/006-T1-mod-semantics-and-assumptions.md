# 006-T1 — Mod semantics refinements (C1/C2) and new assumption ids A-36..A-42

**Status: ACCEPTED-WITH-CHANGES** (architect, 2026-10-19; see Decision below) · Contracts: README §5 registry, C1 §3/§7, C2 §3/§5 · Breaking: no (doc
clarifications + registry additions; no wire or model change) · Author: T1 lead · 2026-10-19

## What / why

Writing the `sae_bridge` script against C2 §5 exposed cases the contract leaves open, and script
constructs not covered by A-01..A-35. T1 builds against the refinements below now (normative text in
`mod/sae_bridge/SEMANTICS.md` §9); T2's simulator reproduces them.

## 1. Registry additions (README §5 table)

| Id | Assumption | Test | Contract |
|---|---|---|---|
| A-36 | `event_target:global_event_country` exists at `on_game_start` (else at the first monthly pulse), can hold variables, **and those variables persist across save/load** (anchor for `m`, sid counter, knobs) | M0-5, M0-11 | C1, C2 |
| A-37 | Variable arithmetic and comparison work with variable operands and cross-scope reads (`prev.x`, `event_target:y.x`): `set/change/subtract/modulo_variable`, `check_variable` with `<`, `>`, `>=`, `<=`, `is_variable_set` | M0-5 | C1, C2 |
| A-38 | SNAP/PLYR stat sources (fleet power, naval capacity used/max, war count) via `export_trigger_value_to_variable` can be exported to country variables | M0-5 | C1 |
| A-39 | Triggers/effects `is_country_type = default`, `is_ai`, `every_country`/`any_country` (all living countries incl. the player), `save_event_target_as`, `country_event` from a global event, `add/remove_opinion_modifier` scoping | M0-3, M0-6 | C1, C2 |
| A-40 | `on_monthly_pulse` fires a global `event` once per game month on day 1, and `on_game_start` fires once at game start | M0-3 | C1 |
| A-41 | An unset variable reads as 0; `check_variable` accepts negative literals (`value = -1`) | M0-5, M0-6 | C2 |
| A-42 | `log` strings resolve `[This.GetName]` (and the date token, A-05) inside the printed text | M0-3 | C1 |

Also: reword A-14 to "`on_monthly_pulse` / `on_game_start` event lists merge across mods" (the mod
no longer uses `on_monthly_pulse_country`).

Fallbacks are listed per construct in `mod/sae_bridge/UNVERIFIED.md`.

## 2. Semantics refinements (doc text for C2 §5 / C1 §3)

| # | Refinement |
|---|---|
| R1 | A target equal to the line's own `sid` is `REJECTED/RANGE`; a target matching no living country is `REJECTED/BAD_TARGET`. |
| R2 | `sae_seq_pending` outside 1..1,000,000 is reset to `sae_seq_applied` with no ACK (an ACK would violate C1 `seq ≥ 1`). |
| R3 | `REJECTED/NOT_MANAGED` is checked right after the duplicate check; it also covers a managed country now played by a human (`play`, hot-join). |
| R4 | `sae_managed` is sticky: once set it is never removed. |
| R5 | Out-of-range or non-integer `sae_emit_period` / `sae_quiet` values are ignored silently (previous value kept). |
| R6 | HELLO is also printed when the mod initialises in a running campaign (first `sae.0`). |
| R7 | The monthly hook is global `on_monthly_pulse` (C1 §7 A-14 wording names `on_monthly_pulse_country`). |
| R8 | Knob values read at a pulse take effect in that same pulse's HB and stagger. |
| R9 | SNAP/PLYR in mod 0.1.0 omit `en`, `mi`, `al`, `al_inc` (optional keys; no exportable source known, A-38). |
| R10 | At most one `sae.10` popup per human per pulse. |

## 3. Notes for consumers (no contract change requested)

- **T2:** with no load hook (A-35 dropped in 0.1.0) or the anchor missing at game start, the first record of a session may be
  `HB`, not `HELLO`. The adapter should open a session on the first HB when no HELLO has been seen.
- **T4:** reloading an earlier save reverts `sae_seq_applied` and `m`; re-running a file whose seq
  the engine already holds as terminal prints `APPLIED` again. C3 §5 "last event wins" covers it, but
  `ManagedState.applied_seq` must be allowed to move down after a `session_epoch` bump.
- **Campaign identity (backlog):** loading a different campaign without a HELLO silently remaps sids.
  An optional `gid=` key in HELLO/HB is non-breaking under C1 §6 but needs a script random source
  (unverified). Not built.
- **A-42 fallback is breaking:** `name` is required in SNAP/PLYR; if `GetName` cannot be printed, C1
  needs v2 (or a constant placeholder name).

- **Sid-counter regression (backlog):** if A-36 fails and the anchor is recreated, `sae_g_sid_next`
  restarts and sids repeat. An optional `sidn=` key on HB would let the engine alarm; not built.
- **T3:** temps `sae_t_*` and stat copies `sae_s_*` persist on countries in the save; they are not state.
- **A-35:** mod 0.1.0 ships no save-load hook (an unknown on_action is a load error); HB date
  regression / first-HB session start is the reload signal.

## Consumers

T2 (simulator reproduces R1–R10), T8 (M0 checklist gains A-36..A-42), T4 (no change: rejections are
already terminal ACKs). No migration.

## Decision (architect, 2026-10-19)

| Part | Ruling |
|---|---|
| §1 A-36..A-42, A-14 reword | ACCEPT. Added to README §5, C1 §7, C2 §7. A-35 kept, marked "not used by mod 0.1.0". |
| R1, R2, R3, R5, R8, R10 | ACCEPT. C2 §5 now lists SEMANTICS §6's rule order (bounds → `==`/`<` → NOT_MANAGED → MP → schema → expiry → range/self-target → BAD_TARGET → apply). |
| R4 | ACCEPT-WITH-CHANGES: sticky, but C6 §2 cap `@sae_max_managed = 16` (first 16 by sid) still applies; SEMANTICS §4 step 4 and `sae_step4_managed` must add it (T1 fix). |
| R6, R7, R9 | ACCEPT (C1 §3 HELLO row, A-14, optional-stat note). |
| §3 first HB without HELLO | ACCEPT as C1 §5 / C6 §2 rule: session opens at the current epoch, no bump, no `hello` event. |
| §3 A-42 fallback | CHANGED: constant `name=?` keeps v1; no v2. |
| §3 `gid=`, `sidn=` | Backlog; additive under C1 §6 when built. |

T4 impact: none. Verified `lifecycle.observe_managed` with `applied_seq` 15 < `next_seq` 21 keeps 21; a
re-printed `APPLIED` for a terminal seq is deduplicated, a different status (`STALE`) is recorded.
