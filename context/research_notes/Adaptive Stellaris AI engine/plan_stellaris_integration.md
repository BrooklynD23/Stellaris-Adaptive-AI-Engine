# Plan: Stellaris Integration Layer (mod + companion bridge)

Date: 2026-10-18. Inputs: `stellaris_integration.md`, `dev_monitoring_environment.md` (skimmed the other three notes), a shallow clone of `OldEnt/stellaris-starnet-ai` (commit of 2024-02-28), and four targeted web lookups.

Labels: **VERIFIED** means confirmed from a cited source. **HYPOTHESIS** means it must be tested in Milestone 0 (section 6). In the citations, "SN" means a file in the StarNet clone.

## 0. Key findings that shape the design

- **StarNet on GitHub is stale.** Its `descriptor.mod` has `supported_version="3.10.*"`, the last commit is "3.11.1 update, removed budgets" (2024-02-28), and `remote_file_id="1712760331"` (VERIFIED, SN). The game is now 4.4.x (VERIFIED as of June 2026, patched.gg). The user probably runs a Workshop build (ID 1712760331 or a fork), so the first step is to identify which build is installed. Do not code against the GitHub tree.
- **StarNet already exposes runtime knobs we can drive.** We do not need to override its files:
  - Fleet composition: `str_value_ai_ship_data_<size>` script values end with `modifier = { set = str_var_ai_ship_data_factor_<size>  is_variable_set = str_var_ai_ship_data_factor_<size> }`. These feed `ship_sizes/*/ai_ship_data.fraction` (VERIFIED, SN `common/script_values/str_ship_sizes_script_values.txt`, `common/ship_sizes/00_ship_sizes.txt`). The file's own comment shows they are set from the console: `effect set_variable = { which = str_var_ai_ship_data_factor_corvette value = 0 } ...` (VERIFIED, SN). Setting a country variable therefore changes AI ship-mix targets live.
  - Economy: economic plan `subplan`s are gated by `potential = { has_country_flag = ... }`, for example `str_fill_homeworld_with_science_pls` (VERIFIED, SN `common/economic_plans/04_strdefault.txt`). Budgets are gated by `potential` triggers such as `has_ai_personality` (VERIFIED, SN `common/ai_budget/str_alloys_budget.txt`).
  - Global switches: `str_doomstack_disabled`, `str_global_ai_should_build_starbase_defenses` (VERIFIED, SN).
- **Data-out syntax exists.** `log = "---- No suitable planets found for [This.GetName] ----"` appears in a StarNet event, which shows a `log` effect that supports localisation scope tokens (VERIFIED, SN `events/str_its_pop_ai_assistance.txt:147`). Which file it writes to (`game.log`) is a HYPOTHESIS.
- **Effect syntax verified.** `set_variable`/`change_variable = { which = <str> value = <float>/<variable> }`, `set_country_flag`, `set_timed_country_flag`, and `set_policy = { policy option cooldown }` all exist (VERIFIED, [wiki Effects](https://stellaris.paradoxwikis.com/Effects), last verified for 4.3). A wiki fetch did **not** find `log`, any economic-plan setter, or `set_ai_personality` in the visible text, so treat those setters as nonexistent until proven.
- **Data-in has one live channel.** `run <file>` runs a list of console commands from a file in the user Stellaris folder (VERIFIED, [wiki Console commands](https://stellaris.paradoxwikis.com/Console_commands)). The console is unavailable in Ironman, and any console command disables achievements (VERIFIED, [wiki Console](https://stellaris.paradoxwikis.com/Console)).
- **jomini parses Stellaris saves.** It is MIT-licensed, lists Stellaris among supported games, and handles text, binary and zip envelopes (VERIFIED, [github.com/rakaly/jomini](https://github.com/rakaly/jomini)). stellaris-dashboard watches the save directory and parses with its own Rust parser (VERIFIED, [repo](https://github.com/eliasdoehne/stellaris-dashboard)).

## 1. Mod package layout

Working name **SAE (Stellaris Adaptive Engine)**. Every file and every identifier is prefixed `sae_` to avoid filename collisions, because load order is ASCIIbetical by filename with folder-dependent FIOS/LIOS behaviour (VERIFIED, [wiki Modding](https://stellaris.paradoxwikis.com/Modding)).

```
sae_bridge/
  descriptor.mod            name="SAE Adaptive Engine Bridge" version="0.1.0"
                            supported_version="v4.4.*" tags={"Gameplay"} picture="thumbnail.png"
  thumbnail.png
  common/
    on_actions/sae_on_actions.txt          # monthly/yearly hooks, game start, save load
    scripted_effects/sae_emit_effects.txt  # sae_emit_country_snapshot, sae_emit_ack, sae_emit_war
    scripted_effects/sae_apply_effects.txt # sae_apply_knobs (reads vars, sets flags)
    scripted_triggers/sae_triggers.txt     # sae_is_managed, sae_posture_is_X
    script_values/sae_values.txt           # sae_budget_weight_<cat> = base + variable override
    ai_budget/sae_budgets.txt              # ADDITIVE budget entries gated by sae_* flags
    economic_plans/zz_sae_plans.txt        # only if additive subplans prove possible (M0-7)
    scripted_variables/sae_generated.txt   # between-session generated constants (§3.4)
    defines/zz_sae_generated_defines.txt   # optional, between-session NAI tweaks
  events/sae_bridge_events.txt             # namespace sae; hidden country events
  localisation/english/sae_l_english.yml
```

**Design rules**

1. **Additive first.** Add new `ai_budget` entries, on_action event hooks, scripted effects and script values. Do not overwrite vanilla or StarNet objects. StarNet hooks `on_monthly_pulse`, `on_yearly_pulse` and `on_monthly_pulse_country` from its own file, `strpulse.txt` (VERIFIED, SN). That suggests on_action `events = {}` lists merge across files (HYPOTHESIS, test M0-3).
2. **Adapter profiles.** Run in `starnet` profile (drive StarNet's own variables and flags, no overrides) or `vanilla` profile (SAE's own knobs). Detect StarNet at runtime with the global flag set by `str_mod_flag.1` (VERIFIED that the event exists in SN; flag name to read from `events/str_mod_flag.txt`).
3. **Load order.** StarNet's name is `"~ StarNet AI"`, so it sorts late (VERIFIED, SN descriptor). Put SAE **after** StarNet in the playset (Irony: drag below; name it `"~~ SAE ..."` as a fallback). Any unavoidable override file is named `zz_sae_*` so it wins under LIOS. Use Irony's conflict solver to list collisions and keep that list at zero.
4. **Achievements and checksum.** Any `common/` change alters the checksum (inferred in `stellaris_integration.md`). SAE is a gameplay mod and does not try to stay achievement-safe.

## 2. Data-OUT channels (ranked)

| Rank | Channel | Latency | Reliability | Status |
|---|---|---|---|---|
| 1 | Monthly hidden event, `log = "..."` lines, engine tails `logs/game.log` | about 1 in-game month (seconds of wall time) plus file flush | Good if flushed; text only; limited to what script can print | `log` effect VERIFIED (SN); output file and flush HYPOTHESIS |
| 2 | Autosave parse (jomini, or the stellaris-dashboard parser) | autosave interval (months or a year) plus parse seconds | Excellent: full ground truth incl. fleets and planets | VERIFIED as practice; latency HYPOTHESIS |
| 3 | Manual save via `run` file containing `save sae_snap` | on demand | Good, but needs the console and stalls the game | HYPOTHESIS (`save` console command not checked) |
| 4 | `error.log` / `debug_*` dumps | n/a | Debug only | Commands VERIFIED; paths HYPOTHESIS |

**Split responsibilities.** The log channel carries a small, high-frequency **feature vector** plus **acks**. The save channel carries **ground truth** for the player model and for audits, and is used to reconcile drift in the log stream.

**Line format (v1).** Use one line per record, with a fixed prefix so the tailer can grep it out of engine noise:

```
SAE|v1|SNAP|2231.04.01|c=3|is_ai=1|en=412.0|mi=1180.0|al=96.0|fleet=8450|navcap=0.82|wars=1|plan=balanced|ack=17
SAE|v1|PLYR|2231.04.01|c=0|fleet=12010|al=140.0|tech=58|systems=41|at_war_with=3
SAE|v1|ACK|2231.04.01|c=3|seq=17|intent=SET_ECON_PLAN|status=APPLIED
```

**Example script (UNVERIFIED syntax; test in M0).** The `[...]` variable-in-localisation token is the biggest risk. The fallback is bucketed flags plus constant strings.

```
# events/sae_bridge_events.txt
namespace = sae
country_event = {
  id = sae.1  hide_window = yes  is_triggered_only = yes
  trigger = { OR = { has_country_flag = sae_managed  is_ai = no } }
  immediate = {
    export_resource_stockpile_to_variable = { resource = energy variable = sae_en }   # HYPOTHESIS: effect name
    export_resource_income_to_variable  = { resource = alloys variable = sae_al }    # HYPOTHESIS
    set_variable = { which = sae_fleet value = trigger:fleet_power }                 # HYPOTHESIS: trigger name
    log = "SAE|v1|SNAP|[GetYear].[GetMonth]|c=[This.GetID]|en=[This.sae_en]|al=[This.sae_al]|fleet=[This.sae_fleet]|ack=[This.sae_seq_applied]"
  }
}
# common/on_actions/sae_on_actions.txt
on_monthly_pulse_country = { events = { sae.1 } }   # hook name VERIFIED in SN; merge HYPOTHESIS
```

**Tailer.** Python reads the file and remembers its byte offset. It handles rotation, because the game truncates the log on launch (HYPOTHESIS), then filters lines starting `SAE|`. Version mismatches go to quarantine. To keep the cost low, emit only for the player plus managed AIs, and fall back to every 3 months if M0-9 shows a stutter.

**Save parser.** Watch `save games/<empire>/autosave_*.sav` and parse each new file with jomini (Rust, PyO3 binding) or vendor stellaris-dashboard's `rust_parser` (MIT). Shorten the autosave interval in game settings to monthly for development (HYPOTHESIS: option exists).

## 3. Data-IN channels

### 3.1 Live path: engine-written `run` file, which sets variables, which mod events read

1. The engine writes `Documents/Paradox Interactive/Stellaris/sae_in.txt` atomically (write a temp file, then rename). Each line is one console command, for example:
   ```
   effect every_country = { limit = { has_country_flag = sae_managed_3 } set_variable = { which = sae_k_econ value = 2 } set_variable = { which = str_var_ai_ship_data_factor_cruiser value = 60 } set_variable = { which = sae_seq_pending value = 17 } }
   ```
   StarNet's own comment shows a multi-effect `effect` line works from the console (VERIFIED, SN). Whether console `effect` runs in the player-country scope, and whether `every_country` works there, is a HYPOTHESIS (M0-4). The fallback is `event sae.50 <country_id>`, with the payload pre-staged in global variables.
2. **Trigger options**, in order of preference:
   - (a) **Player hotkey.** The player types `run sae_in.txt` once and repeats it with console history (up-arrow). This is honest and robust, but manual.
   - (b) **Keystroke injection.** A companion helper (AutoHotkey on Windows, `xdotool` on Linux) sends backtick, `run sae_in.txt`, Enter, backtick, only when the game window is focused and the game is paused or the engine has a pending batch. HYPOTHESIS: this works while unpaused; keyboard-layout differences matter because the console key varies (VERIFIED, wiki Console).
   - (c) **Cadence.** At most one injection per 1 to 3 in-game months. Strategy-level decisions do not need more (`frontier_game_agents.md` recommends 1 to 2 strategic decisions per month).
3. **Mod side.** The monthly `sae.2` event checks for `sae_seq_pending > sae_seq_applied`, validates ranges, applies the knobs (below), copies the pending sequence number to `sae_seq_applied`, and emits an `ACK` line. Script owns the ack, so the engine knows the game actually applied the change.

**Knob mapping (what changes AI behaviour):**

| Knob | Mechanism | Status |
|---|---|---|
| Economic priority | Set flags that gate SAE (or StarNet) economic-plan subplans; no runtime "set plan" effect found | Gating VERIFIED (SN); additive subplans HYPOTHESIS (M0-7) |
| Spending split | `ai_budget` entries gated by `sae_*` flags, with `weight`/`desired_min` driven by script values that read variables | Gating VERIFIED (SN); script-value-in-`weight` HYPOTHESIS |
| Fleet composition | StarNet `str_var_ai_ship_data_factor_*` variables, or an equivalent SAE script value | VERIFIED (SN) for StarNet builds of that era |
| Policies (war philosophy, economic) | `set_policy` | VERIFIED effect; AI may revert it, HYPOTHESIS |
| Diplo stance / war intent | Flags read by `ai_will_do`/war-goal/attitude weights; truce flags in StarNet style (`str_player_will_not_declare_war_until`) | HYPOTHESIS for weight hooks |
| Personality | `has_ai_personality` gates exist; no setter found | Setter HYPOTHESIS (likely absent) |

### 3.2 What is hardcoded, and the indirect levers

Fleet pathing, target-system choice, combat engagement and war-declaration timing are code-driven (HYPOTHESIS, consistent with the research notes). The levers are:

- **Defines.** `NAI` `WAR_*_PRIO` targeting priorities and `NShip` designer ratios are data (VERIFIED, SN `common/defines/str_defines.txt`), but they load at startup, so they are between-session only.
- **Script nudges.** StarNet's doomstack event sets timed fleet and star flags (`set_timed_fleet_flag`, `rallypoint_*`) to herd idle fleets (VERIFIED, SN `events/str_fleet.txt`). SAE can build "rally/defend system X" nudges the same way, but not real fleet orders.
- **Budgets and posture.** Changing budgets changes fleet size and timing, which is the most reliable indirect lever.

### 3.3 Diplomacy

Opinion modifiers (`common/opinion_modifiers`, which StarNet has) can be added or removed with effects to bias acceptance (HYPOTHESIS for the exact effect). This is the safe way to express DIPLO_STANCE.

### 3.4 Between-session path (works in Ironman)

Between sessions, the engine regenerates `scripted_variables/sae_generated.txt` (for example `@sae_mil_bias = 1.3`), the `zz_sae_generated_defines.txt` file, and the default flag set applied `on_game_start`. The player restarts the game to pick up the changes. Mid-campaign, values referenced through `@vars` in script values are re-read at load (HYPOTHESIS: they are compiled at load, so they do not change in an existing save until restart, which is fine). This is the **Ironman/release** channel. It is per-session adaptation and is still useful for a bandit choosing among counter-policies between sessions (`adaptive_opponents.md`).

## 4. Action contract (Intents v1)

The engine never writes raw console text from a planner or LLM. The pipeline is: planner, then a typed Intent (pydantic, `schema_version=1`), then a validator, then a **compiler** that turns it into a fixed template, then `sae_in.txt`.

| Intent | Params (validated) | Compiles to |
|---|---|---|
| `SET_ECON_PLAN` | `country`, `plan` ∈ {balanced, alloys_rush, tech_rush, consumer_stability, expansion} | `sae_k_econ` = enum int, which sets one `sae_econ_*` flag |
| `SET_MILITARY_POSTURE` | `posture` ∈ {turtle, build_up, aggressive, total_war}, `intensity` 0–1 | `sae_k_mil` plus `sae_k_mil_int` (×100), which feed the budget weights |
| `PRIORITIZE_TECH_TREE` | `area` ∈ {physics, society, engineering}, `weight` 0.5–2 | `sae_k_tech_<area>` feeds tech-weight script values (HYPOTHESIS: hook in `ai_weight`) |
| `FLEET_COMPOSITION_BIAS` | `{size: 0–100}` per standard size | `str_var_ai_ship_data_factor_*` (starnet) / `sae_ship_<size>` (vanilla) |
| `DIPLO_STANCE` | `target`, `stance` ∈ {friendly, neutral, hostile, rival} | `sae_dip_<stance>@<target>` flag plus opinion modifier |
| `NOOP_HEARTBEAT` | none | advances `seq`; liveness check |

**Rules**

- Every batch carries a monotonically increasing `seq`, and every Intent carries `ttl_months`. Script ignores stale or duplicate sequence numbers.
- The validator rejects batches that target non-managed countries or the player, are out of range, or change posture more than once every 6 months. The hysteresis requirement comes from `adaptive_opponents.md`: adaptation must be paced and legible.
- An Intent counts as **APPLIED** only after a matching `ACK` log line arrives. If no ack arrives within 2 months, the engine marks it `LOST` and re-emits once, then alarms.
- Every Intent, ack and observation goes to the engine's JSONL decision log (`dev_monitoring_environment.md` recommendation).
- Intents are versioned: the mod refuses `seq` batches whose `sae_schema` variable does not match, and logs `ACK|status=SCHEMA_MISMATCH`.

## 5. Ironman, achievements, multiplayer and OOS

- **Ironman.** No console, so no `run` (VERIFIED). Release mode must work with **save-parse out plus between-session in only**. The log channel may still work in Ironman (HYPOTHESIS), which would leave out-channels intact.
- **Achievements.** Disabled by any console command (VERIFIED) and by `common/` mods in general (PARTIAL, wiki). Accept this.
- **Multiplayer.** A `run` on one client mutates state locally, which is a near-certain OOS. Recent patches still fix OOS bugs tied to country resources (VERIFIED, patched.gg 4.3.6). **Live mode is single-player only**, and the mod hard-disables `sae.2` application when `is_multiplayer` is true (HYPOTHESIS: trigger name). Host-only between-session regeneration is possible if every client loads identical generated files, but this is out of scope.

**Modes**

| | Dev mode (single-player) | Release mode |
|---|---|---|
| Data out | game.log tail plus monthly autosave parse | autosave parse (plus log if it works in Ironman) |
| Data in | `run` via hotkey or injection, live Intents | between-session regenerated files only |
| Ironman | off | allowed |
| Extras | `observe`/`play` for AI-vs-AI benchmarking (VERIFIED commands) | none |

## 6. Milestone-0 feasibility spike (first weekend, on the user's PC)

Record the Stellaris version, checksum, the StarNet build and Workshop ID, and the exact playset. Each test produces a pass or fail result plus a note in `m0_results.md`.

| # | Experiment | Pass criterion |
|---|---|---|
| M0-1 | Identify the StarNet build: open its Workshop folder `.../workshop/content/281990/<id>/`, diff it against the GitHub clone, and grep for `str_var_ai_ship_data_factor_` | Know which knobs exist in the installed build; pass if the variable hook is still present |
| M0-2 | Minimal SAE mod (descriptor plus one event) loads in the launcher/Irony after StarNet, with no errors in `error.log` | Mod enabled, game starts, zero `sae` errors |
| M0-3 | `on_monthly_pulse_country = { events = { sae.1 } }` with `log = "SAE|v1|HELLO|[This.GetName]"` | Line appears in `logs/game.log` (or note which file) once per month per country, and StarNet's monthly events still fire (merge OK) |
| M0-4 | Log flush latency: tail the file with a timestamping script | Line visible in 5 s of wall time or less after the month tick; otherwise fall back to the save channel |
| M0-5 | Variable printing: `set_variable sae_x = 42.5`, then `log = "x=[This.sae_x]"` and the export-to-variable effects | Numeric value printed; if not, design bucketed-flag emission |
| M0-6 | `run`: create `Documents/.../Stellaris/sae_in.txt` with an `effect set_country_flag = sae_test` line and an `every_country` variant, then `run sae_in.txt` | Flag set on the intended countries (verify with M0-3 logging); record scope behaviour |
| M0-7 | Knob efficacy: set `str_var_ai_ship_data_factor_corvette = 100`, others 0, on one AI; observe for 10 years in `observe` mode | That AI's new builds are predominantly corvettes (save-parse count); pass if the share is at least 70% |
| M0-8 | Budget knob: additive `sae_budgets.txt` entry gated by `sae_mil_rush` flag, with a large alloy-ships weight | Fleet power of the flagged AI is at least 1.3× its unflagged twin after 15 years (same seed, A/B) |
| M0-9 | Performance: emit SNAP for all countries monthly at 0.95 speed for 20 years | Under 5% change in days per minute against the mod disabled |
| M0-10 | Keystroke injection: AHK/xdotool sends the run sequence while paused and while unpaused | 10 out of 10 successful runs with no stray input; otherwise ship hotkey-only |
| M0-11 | Save parse: jomini or dashboard parser on a 4.4 autosave; time it; extract country fleets, resources and tech count | Parse in 3 s or less for a mid-game save; fields match the in-game UI |
| M0-12 | Ironman check: start an Ironman game with SAE | Record whether the mod is allowed, whether `log` lines still appear, and whether the console is blocked (expected) |
| M0-13 | Additive economic subplan: a same-name `economic_plans` block in a `zz_` file that adds one subplan | StarNet's other subplans are kept and the new one is active (check with an `observe` resource-trend comparison); otherwise use flag-gating only |

**Go/no-go.** If M0-3, M0-6 and M0-11 pass, live dev mode is viable. If M0-3 fails but M0-11 passes, use save-only observation with between-session control. If M0-7 or M0-8 both fail, the knobs are too weak, so revisit using defines and a full plan override.

## Open risks

- StarNet's 4.x Workshop build may have renamed its hooks (M0-1).
- Using variables in `ai_budget` `weight` is untested.
- Keystroke injection is fragile and may be considered automation. Keep it optional and off by default.
