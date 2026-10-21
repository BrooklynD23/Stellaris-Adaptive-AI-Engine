# SAE user guide

SAE is pre-M0: every command is built and tested game-free (`live` only against a fake game folder); nothing
has been tested against the real game. Every game behaviour below is UNVERIFIED until
M0 ([checklist](m0_checklist.md)).

## 1. What it does and does not do

- Reads `SAE|v1|` lines the `sae_bridge` mod prints to `game.log`, and your autosaves (parsed by `sae-parse`).
- Infers which of four archetypes you play (turtle, tech_rush, early_aggression, balanced) from what the AI could
  legally see, and picks a counter-strategy it keeps for 24 months.
- Writes bounded intents (economic plan, military posture, diplomatic stance) to `sae_in.ini`. Nothing happens in
  game until **you** type `run sae_in.ini` in the console (hotkey workflow; no keystroke injection in v0).
- Never adds resources, never changes the game's CPU priority or affinity, never uses the GPU, never writes in the
  game install folder or the `mod/` folder.
- Fleet composition (ship-size) intents are **dropped from v0** until M0-7 shows a working knob (maintainer
  decision 2026-10-19).
- Counter-strategies (C3 §6): `outscale_siege` (vs turtle) and `early_punish` (vs tech_rush) set the same AI
  knobs (military economy, offensive posture toward you) and differ only in the flavour event you see; they split
  again with the hull mix after M0-7. `hold_counterpunch` (vs early_aggression) goes military but defensive;
  `baseline` (balanced, or no confident read) stays balanced and deterrent.
- All four archetypes pick their own counter in game-free tests. The likelihoods are tuned to the synthetic
  profiles (`sae synth`) and stay guesses until M2 calibration on real saves.
- Change detection (CUSUM `change_score`) is diagnostic only: it is logged, never acted on (maintainer decision).

## 2. Install

1. Python 3.11+ and [uv](https://docs.astral.sh/uv/); Rust (`rustup`, MSVC toolchain on Windows).
2. `uv sync` in the repository.
3. `cd parser && cargo build --release -j 1` → `parser/target/release/sae-parse(.exe)`. SAE finds it there or on
   `PATH`; else set `paths.parser_exe`.
4. The mod: until a release zip exists ([packaging plan](../packaging/PLAN.md)), copy `mod/sae_bridge/` into your
   Stellaris user `mod/` folder by hand with a `sae_bridge.mod` launcher file (`sae m0 stage M0-2` writes one for
   you to copy). Enable it **last** in the playset.
5. Optional telemetry (Rerun viewer): `uv sync --extra telemetry` (`rerun-sdk==0.39.0`, for `sae telemetry`); offline
   analysis: `uv sync --extra console` (`duckdb>=1.1,<2`). Both extras per C8 §7 (proposal 010, accepted).

## 3. Paths

Detected per contract C8 §3; override any of them in `sae.toml` (`[paths]`) or with `--set paths.<key>=<value>`.

| Key | Default |
|---|---|
| `user_dir` | Windows: shell Documents folder (follows OneDrive, A-33) + `Paradox Interactive/Stellaris`; Linux: `~/.local/share/Paradox Interactive/Stellaris`; Proton: set it yourself |
| `game_log` | `<user_dir>/logs/game.log` (A-03) |
| `save_dir` | `<user_dir>/save games` |
| `ini_path` | `<user_dir>/sae_in.ini` (A-08) — the only file SAE writes under `user_dir` (plus its `.tmp` sibling) |
| `parser_exe` | `sae-parse` on `PATH`, else `parser/target/release/sae-parse[.exe]` |
| `runs_dir` | Windows `%LOCALAPPDATA%\sae\runs`; Linux `${XDG_STATE_HOME:-~/.local/state}/sae/runs` |

Config layering (later wins): built-in defaults < preset < `sae.toml` (or `--config`, or `$SAE_CONFIG`) <
`SAE_<SECTION>__<KEY>` environment < `--preset` / `--set`.

## 4. Commands

| Command | Status | Use |
|---|---|---|
| `sae daemon [--adapter live\|release] [--resume <run_id>]` | built (T4, T2 live adapter); tested against a fake game folder only | the engine next to a running game |
| `sae replay <session_dir> [--speed realtime\|<N>x\|max] [--from <date>] [--stop-at <date>] [--ack-model ...]` | built (T2) | re-run a recorded session game-free |
| `sae synth [--archetype <a>[@<year>],...] [--years <n>] [--seed <n>]` | built (T2; `--record <dir>` saves a replayable session) | simulated game for development |
| `sae ctl pause\|resume\|force-strategy <s> [--months N]\|clear-force\|status [--run <id>] [--reason <text>]` | built (T6) | control a running daemon (file inbox) |
| `sae telemetry --run <id> (--rerun-url <url> \| --rrd <path>)` | built (T6; needs the `telemetry` extra) | optional third process: Rerun view; exits 1 if its own private memory (not shared library pages) passes 150 MiB and never starts a viewer on your machine ([remote viewer setup](../tools/console/README.md): viewer command, its memory limit, LAN-only warning) |
| `sae bench` | built (T7) | release-gate analysis (`spy`, `overhead`, `stutter`, `bus`); R-1..R-6 need the game and have not been run ([procedures](../bench/README.md)) |
| `sae profile update [--include-synth] [--force]` | built (T9 WU-5) | index finished runs into campaigns and rebuild the profile; exit 2 if `profile.mode = "off"`, 1 if Stellaris is running (override: `--force`), the index is locked or `campaigns.jsonl` is corrupted |
| `sae profile show [--json]` | built (T9) | list campaigns and the profile panel (read-only) |
| `sae profile close <campaign>` | built (T9) | mark a campaign finished (exit 2 if unknown id) |
| `sae profile exclude <campaign> [--reason <text>]` | built (T9) | keep a campaign out of the profile; removes `profile.json` at once |
| `sae profile delete [--campaign <id>] [--yes]` | built (T9) | delete one campaign (or, without `--campaign`, the `profile/<player_key>/` folder and mark every campaign deleted; the index file stays); needs `--yes`, or a terminal and typing `DELETE` (else exit 2) |
| `sae profile label [<campaign> \| --recording <dir>] --segment <date>=<archetype> [--segment ...] [--note <text>] [--not-blind]` | built (T9) | write a ground-truth label; exit 2 if the label file exists, the id is unknown or the label is invalid |
| `sae play [--adapter live\|release] [--resume <run_id>]` plus the common flags | built (T9 D5) | run `sae profile update`, then `sae daemon` (see 8c); exit code is the daemon's |
| `sae m0 list\|steps\|init\|stage\|check\|tail\|record\|summary` | built (T8) | the M0 spike kit ([runbook](../tools/m0/README.md)) |

Common flags for daemon/replay/synth/ctl/profile/play: `--config <path>`, `--preset <name>`, `--set <section>.<key>=<value>`
(repeatable), `--runs-dir <dir>`. Exit codes: 0 ok, 1 runtime failure, 2 usage, 3 invalid config (`sae profile` uses the same codes).

Every run writes `runs/<run_id>/` with `config.resolved.json`, `bus.jsonl` (every decision: observation →
belief → strategy → intent → ack) and `state.json` for `--resume`.

## 5. Presets

| Preset | Effect | For |
|---|---|---|
| `balanced` (default) | defaults | most players |
| `low_impact` | Governor floor H1 (sparse parsing), mod emits every 3 months, telemetry off | tight machines |
| `dev` | telemetry on (tier T2), emit every month | development |

The Resource Governor lowers SAE's own work (H0..H3) when RAM or hard faults rise; it never slows the game.
`low_impact` is an H1 floor, not H3 (maintainer decision 2026-10-19).

## 6. Ironman

No live loop. Ironman disables the console, so `run sae_in.ini` is impossible and nothing is ever applied
(settled decision). Whether the mod is even allowed and whether its log lines appear is test M0-14.

In live mode SAE also detects Ironman itself: it reads the metadata of a new `ironman.sav` and switches to
observe-only (no intent writes to `sae_in.ini`, no ack alarm, one `health ironman_observe_only` line) until the
daemon restarts. On exit it still rewrites `sae_in.ini` with knob-only content (§7). The Ironman flag (`ironman=yes` in the save's meta) is UNVERIFIED until M0-14.

## 7. Multiplayer

Not supported. The mod reports `mp=1` in its HELLO line (A-13, UNVERIFIED); while the latest HELLO says `mp=1`
the engine refuses every `sae_in.ini` write (`health ini_write_failed`, detail `multiplayer`) (maintainer
decision 2026-10-19, C6 §1.6). A file already on disk from a single-player session is not deleted: do not run
it in multiplayer, it would desync the issuing client.

**Neutral file.** On clean exit, on SIGTERM and when a multiplayer HELLO (`mp=1`) appears, SAE rewrites
`sae_in.ini` with knob-only content and no intents (`health ini_neutralised`), so an old campaign's intents
cannot reach a new game. It never deletes the file. Under Ironman this exit rewrite still happens; only intent writes are blocked during
the session (maintainer decision).

## 8. Recordings and sharing

`sae synth --record` and live recordings copy every autosave into the run's record dir. With the default
`record.redact_plyr = true` the recorded `game.log` has the same `PLYR` redaction as `bus.jsonl`. **Never
share the record dir: its `saves/` hold full unredacted autosaves. Share only `bus.jsonl` and `.rrd`.**

**Replaying a redacted recording is supported.** With redaction on, each `PLYR` line in the recorded `game.log`
becomes a stub, `SAE|v1|PLYR|<date>|sid=<n>`: the player id is kept, the name and other fields are dropped.
`sae replay` parses the stub like any `PLYR` line (no `log_parse_error`); the player is shown as `sid <n>`
(UNVERIFIED until T2 lands the recorder change; M0 tests it against a real log).

**Recording hygiene** (C6 §3, `record` config; UNVERIFIED until M0-11/M0-3 show real logs):

| Key | Default | Effect |
|---|---|---|
| `record.saves` | `all` | `none` = log-only recording; `every_n` = every `record.save_every`-th save (default 12) |
| `record.max_bytes` | 4294967296 | per recording dir; past it save copies stop, the log keeps recording, one `health record_capped` |
| `record.redact_plyr` | `true` | `false` needs `preset = "dev"` and writes an empty `GOD_VIEW_INSIDE` file: never share that dir |

Nothing is deleted automatically. `sae synth --record` also writes `label.json` (the campaign label).

## 8a. Privacy, profile and salt reset

Profile files live in `<sae_dir>` (default under your user folder) and are never uploaded. They hold hashes,
enums and numbers only: no save path, folder name, empire, species, leader or player name (C10). A campaign is
identified by `save_folder_key`, an HMAC of the save folder name with a random 32-byte install salt in
`<sae_dir>/salt`. The folder name itself never reaches a profile, bus or policy file (recordings copy raw saves, see §8; A-46, UNVERIFIED).

- **What is stored** under `<sae_dir>`: `salt` (install salt), `runs/` (unless `paths.runs_dir` is set), and the
  profile store: `campaigns.jsonl` (append-only index, shared by every `player_key`) and `campaigns.jsonl.lock`
  (exists only while a command writes) sit directly in `<sae_dir>/`. Per-player files live in
  `profile/<player_key>/`: `summaries/<id>.json` (per-campaign numbers), `labels/<id>.json` (your ground-truth
  labels), `profile.json` (the rebuilt profile).
- **Strict profile mode** is the only mode in v1 (`profile.mode = "off"` or `"strict"`, default `off`): it learns
  from fog-legal data only, i.e. legal bus lines only, and never reads `bus.god.jsonl`. Reputation mode is not in v1
  (maintainer decision D1).
- **The profile is a read-only panel.** The engine does not use it until it is eligible: at least 8 closed
  campaigns and a positive fold score (maintainer D2). Until then `sae profile show` is all it does.
- **Campaigns and the split prompt.** `update` groups runs by save folder. If a run in a folder starts more than 5
  game years before that folder's campaign, `update` asks `[n]ew game, [r]eload, skip?`. Without a TTY the question
  is printed to stderr, left pending, and asked again at the next update.
- **Close, exclude, delete, label.** `close` ends a campaign. `exclude` keeps it out of the profile. `delete
  --campaign <id>` removes that campaign's summary and label file and `profile.json`. Plain `delete` removes the
  `profile/<player_key>/` folder and marks every campaign deleted in the index; `campaigns.jsonl` (campaign ids and
  history), `salt` and `runs/` stay. To erase the index too, delete `<sae_dir>/campaigns.jsonl` by hand while no
  `sae` process runs. `exclude` removes `profile.json` and that campaign's summary at once; the next `update` rebuilds the profile. These commands work
  even when `profile.mode = "off"`. `label` never overwrites: if `labels/<id>.json` (or `<dir>/label.json`, the
  ground-truth schedule of a synth recording) exists, it exits 2.
- **Index lock.** A writer that finds `campaigns.jsonl.lock` exits 1 with `index locked`. Remove a stale lock by
  hand only when no `sae` process is running.
- **Recovery from `campaigns.jsonl corrupted`** (exit 1): rename the file and re-run `sae profile update`. Campaign
  ids are deterministic, labels survive, and split questions are asked again.
- **`--include-synth`** (test-only) also indexes `sae synth` and replay runs. Do not use it on a real profile.
- **Salt reset:** delete `<sae_dir>/salt`. Older folder keys become unlinkable, so past campaigns stop merging
  with new ones. A salt that is not exactly 32 bytes is never rewritten: SAE logs one `invalid_event` and runs with
  no folder key. Delete the file to fix that.
- Several people on one PC: set `profile.player_key` per OS user (`^[a-z0-9_]{1,32}$`).
- `update` runs at idle priority and refuses to run while Stellaris is running. The game-process detection is
  UNVERIFIED (A-46 and T5 process names; M0 runs it with the game open and closed).

## 8b. Policy registry and rollback

The engine's tunable numbers are a policy, resolved once at daemon start (C10 §4): config `policy.pin`
`"builtin"` (the shipped constants), a sha256 (must be approved in the registry), or `null` (the registry's
current policy, else built-in). Any failure falls back to the built-in policy and records `fallback_reason` in
`run_started.payload.policy`. A game-version mismatch only raises one `health policy_pin_mismatch`; the policy is
kept. `--resume` exits 1 if the policy sha changed since the run began.

`policies/registry.jsonl` is append-only. A policy needs `propose, prereg, stage1_pass, stage2_pass, approve`
in order before `promote`. Human rows come only from the planned T9 policy command (`policy` subcommands prereg, approve, reject, promote, rollback) on an interactive terminal.

**Roll back (commands in 8d-8f):** the planned T9 policy rollback command (target: the built-in sha or an earlier promoted sha), or at once set
`policy.pin = "builtin"` in the config. A broken registry also falls back to built-in by itself.

## 8c. The `play` launcher

`sae play` (C8 §5, maintainer D5) accepts the daemon's flags (`--config`, `--preset`, `--set`, `--runs-dir`,
`--adapter`, `--resume`). When `profile.mode != "off"` it first runs `sae profile update` (10 minute timeout).
If that fails, or the config is invalid, it prints a notice on stderr and still starts the daemon, which then
reports any config error itself. It then runs `sae daemon` with the same flags and returns the daemon's exit code.
It never launches Stellaris and never touches its install or mod folder: start the game yourself. Whether
`update` sees the game as running depends on game-process detection, UNVERIFIED until M0 (A-46).

## 8d. Tuning the detector (`sae tune`)

`sae tune detector` fits the detector numbers (likelihood tables, drift rho, clip, lock threshold) to **your own**
labelled campaigns and writes a candidate policy `policies/<sha>.json` plus one `propose` row (`by: tuner`).

- Needs at least 5 closed, labelled campaigns in the `tune` split (label them with `sae profile label`). Fewer: exit 2
  "need 5 labelled tune campaigns, have N". The sealed campaign is never opened.
- Selection is leave-one-campaign-out over a fixed grid of 180 points. A point is feasible only if ECE <= 0.10 on every
  fold and the counter-strategy is right on >= 4 of 5 folds. No feasible point: exit 1, no file written, a per-fold
  table is printed.
- Same inputs give the same sha. It refuses while Stellaris is detected unless `--force`, and runs at idle priority.
- The V-3 block (strategy switch count, reward spread) is a signal check. CUSUM is a **diagnostic only**; it is never a
  gate check.
- Policies are **personal** (D4): no sharing, no signing. A policy never changes during a run; a new one is read at the
  next daemon start.
- `--include-synth` is test-only: it uses synthetic campaigns, writes a diagnostic candidate and is **not
  verification**.
- Everything about real data and the game here is UNVERIFIED until X4-G0..G1 (`docs/handoff/deferred/X4.md`); M0 or a
  game-PC session proves it.

## 8e. Gating a candidate (`sae gate`)

Order: `sae policy propose` or `sae tune detector` -> `sae policy prereg <sha>` (human, interactive terminal) ->
`sae gate stage1 <sha>` -> `sae gate stage2 <sha> --objective ...` -> `sae policy approve <sha>`.

- **Stage 1** checks the schema, parent, mutable surface (only detector/tuning/notes may differ), pinned game version,
  sealed split, seed range, sealed budget, prereg, deterministic replay, held-in log loss and feel limits. The log-loss
  check is `heldin_log_loss_delta`: it is **held-in and biased toward the candidate**. The unbiased check is Stage 2.
  `--repo <checkout>` also runs pytest, mypy and ruff there; without it the report says "repo suite not run".
- **Stage 2** `--objective` is required (no default). `detector_replay` reads the sealed campaign(s) once per
  candidate: a second query is refused. It is a **tripwire, not a significance test** (n_sealed is printed). Pass =
  every `sealed_noninferior:<cid>` check passes; `detection_delay`, `false_lock`, `v3_signal` are informational.
  Reported numbers use 2 significant digits.
- `--objective beat_player` or `challenge_band` (maintainer decision D6, still open): writes a draft protocol to
  `--out`, no registry row, exit **4** "no result". Real game-paired results need D6 and M4 (UNVERIFIED, not built).
- `--include-synth` runs print a report and write evidence but **no registry row** and spend no sealed query. They are
  diagnostic only.
- Exit codes: 0 pass, 1 stage fail or I/O or lock error, 2 usage or refused, 3 invalid config, 4 no result by design.

## 8f. Managing policies (`sae policy`)

| Verb | What it does |
|---|---|
| `propose <file>` | validate and copy a hand-made policy to `policies/<sha>.json` (human row) |
| `prereg <sha> --metric --min-effect --margin --budget --min-n` | write the preregistration, then its row |
| `approve <sha>` / `reject <sha>` | human decision after stage2_pass |
| `promote <sha>` | make it current; refused if its game-version pin does not match its campaigns |
| `rollback [<sha>]` | no sha = one step back (previous policy, or built-in); with a sha = that earlier one |
| `pin builtin` | rollback row to the built-in policy, plus a hint for the hard pin `policy.pin = "builtin"` |
| `show` | current, approved, chain per sha, sealed-query ledger |

Human verbs need an interactive terminal and ask you to type the first 8 hex of the sha. This is a speed bump, not
security (C10 section 5). Rollback and promote take effect at the **next daemon start**; `sae policy` never edits your
config or touches a running daemon. Whether `sae play` really starts with the rolled-back sha is UNVERIFIED until X4-G5.

## 9. Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| No `SAE|` lines in `game.log` | `sae m0 check M0-3`; mod enabled last? | `sae m0 check M0-2` for load errors; check `paths.game_log` |
| `error.log` mentions sae | `sae m0 check M0-2` | report the lines; disable other mods to bisect |
| `run sae_in.ini` does nothing / no ACK | file in `<user_dir>`? AI in contact with you (only contacted AIs are managed)? | `sae m0 steps M0-6`; set `paths.ini_path` |
| `sae-parse not found` | `parser/target/release/` | build it or set `paths.parser_exe` |
| Parse is slow or killed | Governor level in `bus.jsonl` `health` events | `--preset low_impact`; RAM pressure moves SAE to H2/H3 by design |
| Config error, exit 3 | the message names the key | unknown keys are rejected; check spelling |
| Daemon refuses to start | `runs/CURRENT` names a live `sae` process | stop the other daemon; a dead pid is overwritten |
| Disk filling up | size of `runs/` | no automatic cleanup: delete old `runs/<id>/` by hand; recordings copy every autosave, so each recorded campaign costs disk (a size warning is coming, T4) |
| OneDrive Documents | where the launcher reads `mod/` | daemon: set `paths.user_dir`; `sae m0`: pass `--user-dir` (the kit reads only its own flags) |
