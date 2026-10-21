# M0 checklist

Every row here is **UNVERIFIED** until the maintainer records evidence from a real Stellaris 4.4 game with
`sae m0`. Nothing has run in the game. Sources merged: `contracts/README.md` §5 (A-01..A-46),
`mod/sae_bridge/UNVERIFIED.md` (per-construct tags and fallbacks), `parser/README.md` (save key map),
`docs/status/T3.md`, `T4.md`, `T5.md`, the integration plan's M0 tests and the resource proposal (M0-12, M1-P,
R-x). The runbook for the weekend is [`tools/m0/README.md`](../tools/m0/README.md).

`tests/unit/t8/test_checklist_drift.py` fails when an A-id in the registry, an `UNVERIFIED` tag in `mod/`, `src/`
or `parser/`, or a check in `sae.m0.checks` is missing here.

**Safety.** The kit never launches Stellaris, never writes in the install folder, the user `mod/` folder or the
Stellaris user folder. Files the game must see (`sae_bridge.mod`, `sae_in.ini`) are staged in `m0_results/stage/`
and you copy them by hand.

**Go/no-go** (integration plan): M0-3, M0-6 and M0-11 pass → live dev mode is viable. M0-3 fails but M0-11
passes → save-only observation with between-session control. M0-8 fails and M0-7 did not pass (failed, skipped
or not run) → the knobs are too weak for live adaptation; revisit defines and full plan overrides.
`sae m0 summary` applies this rule.

## A. Tests

Tier: **core** = this weekend, required for go/no-go; **stretch** = this weekend if time allows; **later** =
not this weekend (M1 or release gate). Result field: `m0_results/<id>.json` (`auto` and/or `manual`).

| Test | Tier | What you do (full steps: `sae m0 steps <id>`) | Pass | Evidence | Fallback | Command |
|---|---|---|---|---|---|---|
| M0-1 | core | locate the StarNet Workshop folder | `str_var_ai_ship_data_factor_` present in a 4.x build | hook files, `supported_version` | vanilla profile only; fleet-size intent stays dropped | `check M0-1 --starnet-dir` |
| M0-2 | core | copy the mod and staged `.mod` by hand, enable it last, start a game, quit | zero `error.log` lines mentioning sae | `logs/error.log` | fix descriptor/load order; bisect `common/` | `stage M0-2`, `check M0-2` |
| M0-3 | core | new game, 3 months, check; then save, reload, 1 month, check again | HELLO + HB parse with the C1 parser; one HB per month; names resolved | `SAE|` lines, prefix, raw date token, names, UTF-8 errors | bucketed flags + constant strings; or autosave-only observation | `check M0-3`, `record M0-3` |
| M0-4 | core | tail while playing; pause within 2 days after a month starts | no multi-month batches; paused HB within 5 s | `M0-4.tail.jsonl`, paused delay | save channel only (log for acks) | `tail`, `record M0-4` |
| M0-5 | core | play 12 months with an AI in contact; compare one fleet value with the UI | SNAP with numeric fleet power, sid ≥ 1, matches UI | SNAP lines | bucketed-flag emission | `check M0-5`, `record M0-5` |
| M0-6 | core | stage `sae_in.ini`, copy it, console `run sae_in.ini`, play to next month | `ACK APPLIED/OK` for staged (sid, seq) | ACK line, stutter, opinion modifier | `event sae.2` with pre-staged globals; between-session only | `stage M0-6`, `check M0-6`, `record M0-6` |
| M0-7 | stretch | only if M0-1 passed: corvette factor 100 on one AI, 10 years | corvette share ≥ 0.70 | ship counts | fleet-size intent stays dropped (C3 v1.1) | `record M0-7` |
| M0-8 | core | base save made after M0-5 and before M0-6 (no seed field); B: econ=military via M0-6, APPLIED, then `observe`; A: `observe`; 15 years each, one after the other | fleet power B/A ≥ 1.3 | fleet power of both | defines / full economic-plan overrides (per campaign) | `record M0-8` |
| M0-9 | later | 20 years at 0.95 speed, emit period 1, mod on/off | < 5 % days/minute change | days per minute | emit every 3 months (`low_impact`) | `record M0-9` |
| M0-10 | later | keystroke injection (backlog, T2) | 10/10 clean runs | — | hotkey only (v0 default) | `record M0-10 skip` |
| M0-11 | core | build `sae-parse`, monthly autosave, parse it, compare with UI; note the autosave folder name (A-46) | exit 0, valid C4 output, ≤ 3 s, fields match UI, A-46 folder behaviour recorded | wall time, peak RSS, size, raw date, god-view fields, A-46 folder behaviour | parse a section subset; dashboard parser cross-check | `check M0-11 --save`, `record M0-11` |
| M0-12 | stretch | s/month mod off vs on, game RAM, fault counter, process name | mod ≤ 1 % of s/game-year | s/month, RSS, counter, process name | redesign emission before engine work | `record M0-12` |
| M0-13 | stretch | additive `zz_` economic subplan, 10 years vs twin | new subplan active, StarNet's kept | resource trends | flag-gated budgets only | `record M0-13` |
| M0-14 | stretch | Ironman game with the mod, 2 months | Ironman save parses, `ironman` flagged; meta entry has `ironman=yes` (key UNVERIFIED) and `sae-parse --sections meta` reads it as true; behaviour recorded | `meta.ironman`, SAE lines, console blocked | Ironman stays observe-only (settled) | `check M0-14 --save`, `record M0-14` |
| M1-P | later | CPU-time rate paused vs running (M1, T5) | separable bands, 4–6 s debounce | CPU-rate bands | pause harvesting only when CLOSED | `record M1-P skip` |
| R-3 | later | mod-script cost scaling (release gate; first run in M0-12) | projected ≤ 1 % of s/year | s/year per config | fewer records per month | `record R-3 skip` |
| R-5 | later | Windows Job cap and background mode (release gate) | Job kills at 1 GB; background mode set | worker exit, I/O priority | lower parse frequency (H2) | `record R-5 skip` |

R-1, R-2, R-4, R-6 are release gates owned by T7 (`sae bench`, procedures in [`bench/README.md`](../bench/README.md));
none has run. They are not part of M0; their UNVERIFIED inputs are X-12..X-14 in table C.

## B. Assumptions A-01..A-46

| Id | Assumption | Test | Evidence to collect | Fallback |
|---|---|---|---|---|
| A-01 | `sae_bridge` loads with zero errors alongside the playset | M0-2 | `error.log` lines mentioning sae | fix descriptor/load order; bisect by deleting `common/` files |
| A-02 | `log` effect exists in 4.4 and writes to `game.log` | M0-3 | any `SAE|` line | autosave-only ground truth (C1 preview lost) |
| A-03 | `game.log` path is `<user_dir>/logs/game.log` | M0-3 | file where lines appeared | `paths.game_log` / `--game-log` |
| A-04 | Game prefixes log lines with its own text | M0-3 | `prefix_sample` | parser already ignores text before `SAE|` |
| A-05 | Localisation date tokens print `Y.M.D` | M0-3 | `raw_date_sample`, `dates_zero_padded` (also T3/T4 date parsing) | swap the single date token in all templates |
| A-06 | `[This.<var>]` prints plain decimals | M0-5 | SNAP values | bucketed flags + constant strings |
| A-07 | Log flush ≤ 5 s | M0-4 | batching, paused-on-the-1st delay | save channel for observations |
| A-08 | `run sae_in.ini` reads `<user_dir>/sae_in.ini` | M0-6 | ACK after `run` | `paths.ini_path`; copy where `run` looks |
| A-09 | Console `effect` + `every_country = { limit … }` work | M0-6 | ACK for the staged sid only | `event sae.2` with pre-staged globals |
| A-10 | `check_variable` equality and `set_variable` from console | M0-6 | ACK APPLIED | `event sae.2` hook |
| A-11 | `run` accepts ≥ 2048-char lines and LF line endings | M0-6 | staged line (~700 chars, LF) applied; a 2048-char line is not staged by the kit | split country lines (C2 v2) |
| A-12 | `run` executes all lines in order without stutter | M0-6 | `stutter` value | fewer lines per batch |
| A-13 | A multiplayer trigger exists | M0-6 | `mp_values` from HELLO | treat as single player (`mp` stays 0); MP guard then relies on the user |
| A-14 | `on_monthly_pulse` / `on_game_start` event lists merge across mods | M0-3 | `starnet_monthly` value | `zz_sae_` override file |
| A-15 | `game.log` truncated on launch | M0-3 | `truncated_on_launch` value; file size before/after | tailer resets on shrink or head change (T2) |
| A-16 | Country names may contain any char; log is UTF-8 | M0-3 | `utf8_decode_errors` | replacement characters (T2 tailer); `name` is informational |
| A-17 | Variables hold integers to ±1,000,000 exactly | M0-5 | seq/sid values printed exactly | lower caps (contract change) |
| A-18 | Autosave = zip(meta, gamestate) text; jomini ≤ 3 s, ≤ 1 GB | M0-11 | wall time, peak RSS, size | section subset; binary support (T3 backlog) |
| A-19 | Contact, ownership, starbase modules, fleet power, tech count locatable in the save | M0-11 | god-view fields vs UI; table C key map | adjust key paths in `parser/src/extract.rs` |
| A-20 | Ironman saves parse; `log` works in Ironman; meta has `ironman=yes` (the live daemon's observe-only switch, proposal 017) | M0-14 | `meta.ironman` via `--sections meta`, SAE lines | Ironman observe-only |
| A-21 | Flag-gated additive budgets/subplans change AI behaviour | M0-8, M0-13 | fleet power A/B | gate StarNet economic subplans by flag; defines |
| A-22 | A per-size ship-mix knob exists | M0-1, M0-7 | hook files; corvette share | fleet-size intent stays dropped |
| A-23 | Opinion-modifier effect for posture/stance | M0-6 | `opinion_modifier_visible` | copy a vanilla `opinion_modifiers` entry and keep its key |
| A-24 | Autosave interval can be monthly | M0-11 | game setting available | parse less often (yearly/quarterly autosaves) |
| A-25 | CPU-time rate separates paused from running | M1-P | CPU-rate bands | pause harvesting only when CLOSED |
| A-26 | Reliable hard-fault counter | M0-12 | `\Memory\Pages Input/sec` moves under pressure | RAM-only escalation |
| A-27 | Job memory limit makes the Rust worker abort | R-5 | worker exit under the cap | H2 with less frequent parses |
| A-28 | Background mode lowers I/O/memory priority | R-5 | I/O priority in Process Explorer | IDLE class only |
| A-29 | Mod script ≤ 1 % s/game-year | M0-12, R-3 | s/month off vs on | longer emit period, fewer records |
| A-30 | `sae_sid`, `sae_seq_*`, applied econ/mil variables and `sae_managed` are stored in the save | M0-11 | `countries_with_sid`, `managed` | seq floor from ACKs only |
| A-31 | Script keeps a global running counter (`sid`) and month counter (`m`) | M0-5 | sids ≥ 1, HB `m` increments | store counters on the first human country |
| A-32 | Script can detect "in contact with the player" to set `sae_managed` | M0-6 | SNAP `contact=1`; ACK not `NOT_MANAGED` | `has_met`/`is_country_known`, else manage every AI |
| A-33 | Stellaris user folder follows the shell Documents folder (OneDrive) | M0-2 | where the launcher reads `mod/` | `paths.user_dir` / `--user-dir` |
| A-34 | Autosaves are `autosave_*.sav` (Ironman: `ironman.sav`) under `save games/<empire>/` | M0-11 | file names seen | `paths.save_dir` + glob change |
| A-35 | An on_action fires on save load (HELLO after reload); unused by mod 0.1.0 | M0-3 | HELLO count after reload | none needed (informational) |
| A-36 | `global_event_country` exists at `on_game_start`, holds variables, persists across save/load | M0-5, M0-11 | counters continue after reload | dummy hidden country, or counters on the first human |
| A-37 | Variable arithmetic/comparison with variable operands and cross-scope reads | M0-5 | stagger works (SNAP per slot), no script errors | per-value if-ladders; inline literals |
| A-38 | SNAP/PLYR stats export via `export_trigger_value_to_variable` | M0-5 | numeric `fleet`, `navcap`, `wars` | omit the key; engine uses autosave values |
| A-39 | `is_country_type`, `is_ai`, `every_country`, `save_event_target_as`, `country_event` from a global event, opinion scoping | M0-3, M0-6 | SNAP for AIs only, ACK, popup | console-scope flag comparisons; generic text |
| A-40 | `on_monthly_pulse` fires once per month on day 1; `on_game_start` fires once | M0-3 | one HB per date, one HELLO per start | `on_monthly_pulse_country` on one anchor country |
| A-41 | An unset variable reads as 0; `check_variable` accepts negative literals | M0-5, M0-6 | `seq=0` on fresh AIs; `-1` keep values honoured | initialise every variable at sid assignment; shift by +1 |
| A-42 | `log` strings resolve `[This.GetName]` and the date token | M0-3 | `unresolved_names` = 0 | constant `name=?` (C1 v1); sid still maps via save |
| A-43 | The game process is named `stellaris.exe` (Windows, Proton) / `stellaris` (Linux) | M0-12 | Task Manager process name | `make_sensors(game_names=...)` |
| A-44 | `IsIconic` on the game's top-level window detects minimise | M0-12 | Governor CLOSED after 10 min minimised (later, with the daemon) | CLOSED only when the game is absent |
| A-45 | A `psutil.process_iter` rescan on Windows stays under the 100 ms probe bound | M0-12 | `sae daemon` health timing (later) | longer rescan interval |
| A-46 | Each campaign's autosaves live in one folder under `save games/`; a folder name is reused only by a new game of the same empire name | M0-11 | folder names seen across two new games of the same empire (`save_folder_key` stability) | §1 split rule (user confirms) + profile exclude (C10 §1) |

## C. Items outside the registry

X-ids are T8-local (no registry row). Former X-01..X-03 became A-43..A-45 (proposal 013-T8, accepted).

| Id | Item | Source | Test | Evidence | Fallback |
|---|---|---|---|---|---|
| X-04 | Save dates are zero-padded (`2200.01.01`) | T3 status | M0-11 / M0-3 | raw `date` in parse output and log | contract parsers already accept 1–2 digits |
| X-05 | Save key map (`player`, `country`, `relations_manager`, `modules…resources`, `budget.last_month.income`, `naval_capacity`, `military_power`, `tech_status`, `galactic_object`, `starbase_mgr`, `war`, `variables.sae_*`, `flags.sae_managed`) | `parser/README.md` | M0-11 | god-view fields vs UI; `countries_with_sid` | fix key paths in T3 (A-19/A-30) |
| X-06 | `military_power` is fleet power (`economy_power`, `tech_power` may be better) | `parser/README.md` | M0-11 | compare with the UI military power | switch key (T3) |
| X-07 | A system's owner is its starbase's owner; defense modules and `orbitals` count as defenses | `parser/README.md` | M0-11 | owned systems vs UI | adjust ownership rule (T3) |
| X-08 | A-13/A-35: a multiplayer session that prints only HB lines (no fresh HELLO) after an earlier `mp=0` HELLO leaves the MP guard open, because the guard follows the latest HELLO; mod 0.1.0 has no load hook | T2 status | later (MP host/join, HELLO count); M0-3 reload is informational (`hello_after_reload`, expected no with mod 0.1.0) | HELLO count after loading/joining | user responsibility (user guide §7); a load-hook HELLO needs a mod change (T1 proposal) |
| X-09 | Related to A-33: OneDrive (or antivirus) locks `sae_in.ini.tmp` so the atomic replace into `<user_dir>/sae_in.ini` fails | T2 status | later, with `sae daemon` on a OneDrive Documents folder (the kit never writes in `user_dir`) | `health ini_write_failed` events in `bus.jsonl` | no retry: the next batch restates desired state (D-05); 3 consecutive failures raise `ack_alarm` (C2 §2); pause OneDrive sync for the Stellaris folder |
| X-10 | A-15: the first 512 bytes of `game.log` differ between launches, so the tailer tells a relaunch from an append | T2 status | M0-3 (relaunch step) | first 512 bytes (about the first lines) from two launches; `banner_differs` | tail-bytes check (`src/sae/adapters/logtail.py`) catches an identical head |
| X-11 | A-34 family (T2 status gap 4): a rewrite of the same save file with the same size and mtime on a coarse-mtime filesystem is missed | T2 status | later (daemon); optional during M0-11 | overwrite one named save twice within 1 s; does the watcher re-parse it? | rotating autosave names (A-34) make it rare |
| X-12 | R-gate (bench, not M0): A-34 autosave mtimes and in-game dates measure seconds per game year (`sae bench spy`) | T7 status | R-1; first look in M0-11 | `spy` s/year vs a stopwatch over the same years | stopwatch (bench/README.md R-1) |
| X-13 | R-gate (bench, not M0): PresentMon CSV has a `MsBetweenPresents` column (name varies by PresentMon version) | T7 status | R-2, R-6 | CSV header of your PresentMon version | rename the column in the CSV before `sae bench stutter` |
| X-14 | R-gate (bench, not M0): `bench/balloon.py` holds available RAM near 1.2 GB under Windows memory compression | T7 status | R-4 | Task Manager available RAM and compressed memory during the run | adjust `--leave-gb` until available RAM is about 1.2 GB |

T5's Windows items map to A-25..A-28 and A-43..A-45 in table B (process names, `IsIconic`, process-scan cost).
Code-level notes already covered above: A-15 (T2 tailer resets on truncation), A-16 (T2 decodes UTF-8 with
replacement), A-33 (T2 path detection), A-34 (T2 save watcher), A-26/A-27/A-28 (T5 Windows code is mock-tested
only, not executed in CI on this machine).
