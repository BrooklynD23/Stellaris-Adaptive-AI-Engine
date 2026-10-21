# `sae_bridge` runtime semantics (normative for T2's mod simulator)

Version: mod 0.1.0, C1 v1, C2 v1, C3 v1. Owner: T1. Status: spec for M1; every in-game behaviour below
is **UNVERIFIED** until M0 (ids in `UNVERIFIED.md`). Where this file and the script disagree, the script
is wrong (file a T1 bug). Where this file refines C1/C2, the refinement is listed in §9 and proposed in
`contracts/proposals/006-T1-mod-semantics-and-assumptions.md`.

The simulator reproduces §§1–8 exactly: same lines, same order, same variable effects. "Iteration
order" means: the game's order is unknown (A-39); **the simulator iterates countries by ascending
in-game country id** for sid assignment and by **ascending `sid`** everywhere else.

## 1. State

Global state lives on the anchor country `event_target:global_event_country` (A-36). All values are
integers.

| Anchor variable | Init | Meaning |
|---|---|---|
| `sae_g_m` | 0 | mod month counter `m` (C1 HB `m`, C2 expiry unit) |
| `sae_g_sid_next` | 0 | last sid handed out |
| `sae_g_period` | 1 | effective `sae_emit_period` (1..12) |
| `sae_g_quiet` | 0 | effective `sae_quiet` (0/1) |
| `sae_g_mp` | 0 | 1 if `is_multiplayer` at the last check (A-13) |
| `sae_g_managed` | 0 | number of countries ever flagged `sae_managed` (never decreases) |

Constant: `@sae_max_managed = 16` (`common/scripted_variables/sae_variables.txt`; C6 §2).

"Initialised" means `sae_g_m` is set on the anchor (`is_variable_set`).

Per-country state (only countries that have an `sae_sid`):

| Variable / flag | Init at sid assignment | Meaning |
|---|---|---|
| `sae_sid` | next counter value | SAE country id, ≥ 1, unique, never reused, never changed |
| `sae_seq_applied` | 0 | last processed batch seq |
| `sae_econ`, `sae_mil`, `sae_dip` | 0 | applied codes (0 = never set) |
| `sae_mil_target`, `sae_dip_target` | 0 | applied target sid (0 = none) |
| `sae_ship_<size>` (5) | 50 | applied hull-size bias (50 = neutral) |
| `sae_reason` | 0 | last applied non-zero ReasonKey code |
| flag `sae_managed` | absent | set once, never removed (sticky), AI countries only |
| flags `sae_econ_<plan>`, `sae_mil_<posture>`, `sae_dip_<stance>` | absent | at most one of each family |

Staged (written only by the console `run` of C2 lines): `sae_p_*` (13 variables) and `sae_seq_pending`.
Knob inputs (written by the C2 knob line on the console-scope = player country): `sae_emit_period`,
`sae_quiet`.

Eligible country: `is_country_type = default` (A-39). Human: eligible and `is_ai = no`. AI: eligible and
`is_ai = yes`.

## 2. Events and hooks

| Hook | Event | Scope | Does |
|---|---|---|---|
| `on_game_start` | `sae.3` | global | §3 HELLO procedure |
| `on_monthly_pulse` | `sae.0` | global | §4 monthly procedure |
| — (console test hook) | `sae.2` | country | §6 apply for this country only (M0-6 hook; not used by `sae.0`; does not clear `sae_t_popup`, so its popup may be suppressed until the next pulse) |
| — (console test hook) | `sae.1` | country | §7 emit for this country only, ignoring stagger and quiet |
| fired by §6 step 10 | `sae.10` | human country | flavour popup, text by reason |

All events are hidden except `sae.10`. `sae.0` does its work through scripted effects, in the exact
order of §4, so ordering does not depend on how the engine queues `country_event`s.

## 3. HELLO procedure (`sae.3`)

1. If not initialised: initialise the anchor (table §1 init values).
2. `sae_g_mp := 1` if `is_multiplayer`, else 0.
3. Print `SAE|v1|HELLO|<date>|mod=0.1.0|schema=1|period=<sae_g_period>|quiet=<sae_g_quiet>|mp=<sae_g_mp>`.

HELLO is never suppressed. If the anchor does not exist yet at `on_game_start` (A-36), `sae.3` does
nothing and `sae.0` prints HELLO on its first run (§4 step 0). There is no save-load hook in v0.1.0 (A-35 is
"not used by mod 0.1.0"; an unknown on_action is a load error, A-01), so a reloaded session starts with `HB`, not
`HELLO`: consumers open a session on the first HB when no HELLO
has been seen (and C1 §5 HB date regression still bumps `session_epoch`).

## 4. Monthly procedure (`sae.0`, once per game month, date = the 1st of the month)

0. If not initialised: initialise the anchor, then run §3 steps 2–3 (HELLO). This covers adding the mod
   to a running campaign.
1. `sae_g_m := sae_g_m + 1`.
2. `sae_g_mp := is_multiplayer ? 1 : 0`. If `sae_g_mp = 0`, read knobs from the first human found
   (single player has exactly one):
   - if `sae_emit_period` is set and equals one of the integers 1..12 → `sae_g_period := value`;
     else unchanged (a fractional value such as 2.5 is ignored);
   - if `sae_quiet` is set and equals 0 or 1 → `sae_g_quiet := value`; else unchanged.
   The player's variables are not cleared. In multiplayer or observer mode (no human) knobs are unchanged.
3. For every eligible country without `sae_sid` (iteration order): `sae_g_sid_next += 1`;
   `sae_sid := sae_g_sid_next`; set the §1 per-country init values.
4. For every AI with `sae_sid`, without `sae_managed`, that has communications with at least one human
   (A-32), in iteration order (ascending sid in the simulator): if `sae_g_managed < 16`
   (`@sae_max_managed`, C6 §2), set flag `sae_managed` and `sae_g_managed += 1`; otherwise skip it.
   The cap counts flags ever set (sticky, R4), so a dead managed country still holds its slot. An AI
   over the cap is never flagged: the C2 country line's `has_country_flag = sae_managed` limit does not
   match, nothing is staged and **no ACK is printed** (the engine never targets it because C3 rule
   `not_managed` rejects it first; a hand-made console line that stages it anyway gets
   `REJECTED/NOT_MANAGED` by §6 rule 4). No new ACK code. When more AIs reach contact in the same pulse
   than slots remain, the game's iteration order (A-39) decides; the simulator uses ascending sid.
5. Print HB: `SAE|v1|HB|<date>|m=<sae_g_m>|period=<sae_g_period>|quiet=<sae_g_quiet>`. Never suppressed.
6. For every country with `sae_sid` and `sae_seq_pending` set (ascending sid in the simulator): §6.
7. If `sae_g_quiet = 0`: for every country with `sae_sid` (ascending sid in the simulator) where
   `(sae_g_m + sae_sid) mod sae_g_period = 0`: human → PLYR (§7); AI with `sae_managed` → SNAP (§7);
   anyone else → nothing.

Consequences the simulator must reproduce: a knob line run during month N takes effect in the HB and
stagger of the pulse that ends month N (step 2 precedes 5 and 7). A batch staged during month N is
acked at that same pulse (step 6), before the SNAP of step 7, so a SNAP never shows a seq older than an
ACK printed at the same pulse. With `period = 1` every managed AI and the player emit every month.

## 5. Stagger arithmetic

`slot(c) = (m + sid) mod period` with `m` after step 1 (`modulo_variable`, A-37). If M0-5 shows
variable-operand modulo is unavailable, the documented fallback is a per-country countdown
(`UNVERIFIED.md`, A-37 row); that would be SEMANTICS v2 and T2 would switch with it. v1 simulators
implement only the modulo rule. Values stay ≤ 1,000,000 (A-17): `m` grows by
12 per game year (≈ 3,600 by 2500), `sid` by one per country ever created.

## 6. Apply procedure (C2 §5, one country `c`)

Let `P = sae_seq_pending`, `A = sae_seq_applied`. "ACK X/Y" prints
`SAE|v1|ACK|<date>|sid=<sae_sid>|seq=<P>|status=X|code=Y` (extra keys as noted). "Close" means
`sae_seq_applied := P` (after the ACK is printed). Stop after the first matching rule.

1. `P < 1`, `P > 1,000,000` or `P` not an integer (e.g. 17.5) → `sae_seq_pending := A`; no ACK.
   (Unreachable from `render_ini`.)
2. `P = A` → nothing, no ACK.
3. `P < A` → ACK `DUPLICATE/SEQ_OLD` with `|applied=<A>`; then `sae_seq_pending := A`. Not closed
   (A unchanged).
4. `c` lacks `sae_managed`, or `c` is not an AI (a human who took over a managed country via `play`,
   hot-join) → ACK `REJECTED/NOT_MANAGED`; close.
5. `sae_g_mp = 1` → ACK `REJECTED/MULTIPLAYER`; close.
6. `sae_p_schema ≠ 1` → ACK `SCHEMA_MISMATCH/SCHEMA`; close.
7. `sae_g_m > sae_p_expires` → ACK `STALE/EXPIRED`; close. (`m = expires` still applies.)
8. Range check (any failure → ACK `REJECTED/RANGE`; close). Valid iff all hold:
   - every staged `sae_p_*` except `sae_p_schema` is an integer (a fractional value such as
     `sae_p_econ = 2.5` → `REJECTED/RANGE`; `sae_p_schema = 1.5` already fails rule 6). Integrality is
     tested as `x mod 1 = 0` on the fixed-point value (A-37); no rounding is ever applied;
   - `sae_p_expires` ∈ 0..1,000,000; `sae_p_reason` ∈ 0..6; `sae_p_econ` ∈ {-1, 1..4};
   - `(sae_p_mil, sae_p_mil_target)` is `(-1, -1)` or (`mil` ∈ 1..3 and `target` ∈ 1..1,000,000 and
     `target ≠ sae_sid`); same for `(sae_p_dip, sae_p_dip_target)` with `dip` ∈ 1..4;
   - each `sae_p_ship_<size>` ∈ {-1, 0..100}. (C2 v1.1: the engine always writes -1 = keep; the mod still
     accepts 0..100 and stores it in `sae_ship_<size>`, which nothing reads; A-22.)
   An unset staged variable counts as 0 (Clausewitz default; A-41), so a partial manual line fails
   here unless 0 is legal for that variable.
9. Bad target: `sae_p_mil_target ≠ -1` and no living country has `sae_sid = sae_p_mil_target`, or the
   same for `sae_p_dip_target` → ACK `REJECTED/BAD_TARGET`; close.
10. Apply (nothing above changed knob state, so this is all or nothing):
    - `sae_p_econ ≠ -1`: clear the four `sae_econ_*` flags, set `sae_econ_<plan(code)>`, `sae_econ := code`.
    - `sae_p_mil ≠ -1`: clear `sae_mil_*` flags; if `sae_mil_target ≠ 0`, remove all three
      `sae_posture_*` opinion modifiers toward the country whose `sae_sid = sae_mil_target` (the previous
      target; A-23); set `sae_mil_<posture>`, `sae_mil := code`,
      `sae_mil_target := target`; add opinion modifier `sae_posture_<posture>` toward the target.
    - `sae_p_dip ≠ -1`: same with `sae_dip_*`, `sae_dip`, `sae_dip_target`, `sae_stance_<stance>`.
    - each `sae_p_ship_<size> ≠ -1`: `sae_ship_<size> := value`.
    - `sae_p_reason > 0` and `≠ sae_reason`: `sae_reason := sae_p_reason`; for every human that has
      communications with `c` (A-32) and has not yet received `sae.10` in this pulse (per-human pulse
      flag `sae_t_popup`, cleared at §4 step 5): fire `sae.10` with `c` as `From`. At most one popup
      per human per pulse; further AIs re-reasoned in the same pulse update `sae_reason` silently
      (no popup). `From` is passed via `scopes` (A-39); the popup names `From` and reads `From.sae_reason` when shown. Reason 0 leaves `sae_reason` unchanged.
11. ACK `APPLIED/OK` with `|reason=<sae_p_reason>`; close.

Codes: econ 1 balanced, 2 military, 3 research, 4 expansion; posture 1 defensive, 2 deterrent,
3 offensive; stance 1 friendly, 2 neutral, 3 rival, 4 hostile; reason 1 turtle_siege,
2 tech_rush_punish, 3 aggression_hold, 4 baseline, 5 operator_forced, 6 safety_floor (C3 §2).

Consequence for the engine: reloading an earlier save reverts `sae_seq_applied` and `m`, so re-running
a file whose seq the engine already holds as terminal prints `APPLIED` (or `STALE`) again for that seq;
consumers take the last event per (country, seq) after a `session_epoch` bump (C3 §5).

Re-running the same file gives `P = A` → no second ACK. Running an older file after a newer one gives
`DUPLICATE`. Staged `sae_p_*` values are never cleared; only `P` vs `A` decides whether they are read.

## 7. Emit records (country scope)

SNAP (managed AI):
`SAE|v1|SNAP|<date>|sid=<sid>|seq=<sae_seq_applied>|fleet=<f>|navcap_used=<u>|navcap=<n>|wars=<w>|econ=<sae_econ>|mil=<sae_mil>|contact=<0/1>|name=<GetName>`

If `[This.GetName]` cannot be printed (A-42), the fallback is the constant `name=?` (C1 v1, proposal
006); the sid still maps the country via the save.

`en`, `mi`, `al`, `al_inc` (optional in C1) are **not emitted** in 0.1.0: no exportable trigger for
stockpile/income is known (A-38). Integer variables may print as `17.000` (A-06); C1's int grammar
accepts that.

PLYR (human): same without `seq`, `econ`, `mil`, `contact`.

Stats are exported into `sae_s_*` country variables with `export_trigger_value_to_variable`
immediately before printing (A-38). `contact` = 1
if the country has communications with at least one human (A-32). The simulator takes stat values
from its own model; the engine treats them as a preview only (C1 §1).

## 8. Things the mod never does

- Never applies a value it did not range-check; never touches knobs on non-sid or human countries.
- Never prints `LOST`, `SUPERSEDED`, `TIMEOUT`, `NEWER_BATCH`.
- Never clears `sae_managed`, never reassigns or reuses a `sid`, never decreases `sae_g_m`.
- Never suppresses HELLO, HB or ACK (`sae_quiet` only gates step 7).
- Running the file in multiplayer desyncs the issuing client before the mod can reject it (console
  effects are local); `REJECTED/MULTIPLAYER` only prevents the knobs, not the desync. Live mode is
  single-player only.
- In Ironman (no console) nothing is ever staged: no ACKs; knobs stay at their init values.

## 9. Refinements of C1/C2 (proposed in 006-T1)

| # | Refinement | Contract text it sharpens |
|---|---|---|
| R1 | Self-target is `REJECTED/RANGE` (step 8), unknown target is `BAD_TARGET` (step 9) | C2 §3 "A target must be another country", §5 step 5 |
| R2 | `P` outside 1..1,000,000 → silent reset, no ACK (an ACK would violate C1 `seq ≥ 1`) | C2 §5 |
| R3 | `NOT_MANAGED` (also for a managed country now played by a human) is checked right after the duplicate check | C2 §5 has no position for it |
| R4 | `sae_managed` is sticky | C1/C6 A-32 |
| R5 | Out-of-range or non-integer knob variables are ignored silently | C2 §5 knob paragraph |
| R6 | HELLO also printed when the mod initialises mid-campaign | C1 §3 HELLO |
| R7 | Monthly hook is global `on_monthly_pulse`, not `on_monthly_pulse_country` | C1 §7 A-14 wording |
| R8 | Knobs read at the pulse take effect in the same pulse's HB and stagger | C2 §5 "next HB shows them" |
| R9 | SNAP/PLYR omit `en`, `mi`, `al`, `al_inc` in 0.1.0 | C1 §3 optional keys |
| R10 | At most one `sae.10` popup per human per pulse | C2 §5 step 6 |
| R11 | Managed set capped at `@sae_max_managed = 16` (first to reach contact; ties by iteration order / ascending sid); over-cap AIs are never flagged and get no ACK | C6 §2 |
| R12 | Non-integer staged values: `P` → rule 1 silent reset; any other `sae_p_*` → `REJECTED/RANGE` | C2 §3 "integers only" |
