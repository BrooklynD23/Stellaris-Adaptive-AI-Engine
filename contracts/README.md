# SAE interface contracts

The contracts below are the only agreements between teams. The pydantic models in
`src/sae/contracts/` **are** the contracts in code; the JSON Schemas in `contracts/schemas/` are
exported from them; `tests/contract/` validates every example in these documents against the models.

## 1. Index and status

| Id | Contract | Version | Status | Producer → Consumers |
|---|---|---|---|---|
| C1 | [`SAE|v1|` log lines](C1_log_lines.md) | 1 | FROZEN v1 | T1 mod (T2 simulator) → T2 adapters, T5 via HB |
| C2 | [`sae_in.ini` command file](C2_sae_in_ini.md) | 1.2 | FROZEN v1.2 | T4 compiler, T2 writer → game console, T1 mod, T2 simulator |
| C3 | [Intent schema v1 + mapping to C2](C3_intents.md) | 1.2 | FROZEN v1.2 | T4 → T2, T6, T1 |
| C4 | [`sae-parse` CLI, JSON, fixtures](C4_sae_parse.md) | 1 | FROZEN v1 | T3 → T4/T5 daemon, T2, T7 |
| C5 | [`bus.jsonl` envelope and events](C5_bus.md) | 1.3 | FROZEN v1.3 | daemon (T4/T5/T2 via T6 writer) → T6, T2 replay, T7 |
| C6 | [GameAdapter, Observation, fog boundary](C6_game_adapter.md) | 1.3 | FROZEN v1.3 | T2 → T4 |
| C7 | [Resource Governor](C7_governor.md) | 1.1 | FROZEN v1.1 | T5 → T4, T6, T7 |
| C8 | [Config, layout, presets, CLI](C8_config_cli.md) | 1.3 | FROZEN v1.3 | all |
| C9 | [Control plane](C9_control_plane.md) | 1 | FROZEN v1 | T6 `sae ctl` → T4 daemon |
| C10 | [Player profile and policy](C10_profile_policy.md) | 1 | FROZEN v1 | T9 `sae profile`/`tune`/`policy`, T7 gate → T4 daemon (policy, read-only), T6, T8 |

Status values: `DRAFT` (Wave 0, open to the adversary loop) → `FROZEN v<N>` (teams build against it;
changes only through §3).

## 2. Ownership map

| Team | Owns | Implements contracts | Consumes |
|---|---|---|---|
| architect | `contracts/**`, `src/sae/contracts/**`, `tests/contract/**`, `pyproject.toml`, `src/sae/cli.py`, `docs/TEAM_PLAN.md` | all (as code) | — |
| T1 Mod | `mod/sae_bridge/**` | C1 producer, C2 §5 consumer, C7 §6 knobs | C3 codes |
| T2 Adapters | `src/sae/adapters/**` | C6 (all modes), C1 parsing, C2 writing, mod simulator, C8 §3 paths | C3, C4, C5 |
| T3 Parser | `parser/**` | C4 | — |
| T4 Core + daemon | `src/sae/core/**`, `src/sae/daemon/**` | C3 validator/expansion, C5 decision events, C9 application, C8 `sae daemon` | C4, C6, C7 |
| T5 Governor + platform | `src/sae/governor/**`, `src/sae/platform/**` | C7 | C4 exit codes, C5 health |
| T6 Telemetry + console | `src/sae/bus/**`, `src/sae/telemetry/**`, `src/sae/control/**`, `tools/console/**` | C5 writer/readers, C9 `sae ctl` + inbox, `sae telemetry` | all event payloads |
| T7 QA/CI/bench | `tests/integration/**`, `tests/e2e/**`, `.github/workflows/**`, `bench/**`, `src/sae/bench/**` | CI-1..CI-5, fog import test (C6 §4) | all |
| T9 Profile + tuning | `src/sae/profile/**`, `src/sae/tune/**`, `tests/unit/t9/**` | C10 §1–§2 producer, C10 §4–§5 writers (`sae tune`, `sae policy`), `sae play` launcher | C5, C6, C10 |
| T8 Release/docs/M0 | `docs/**` (not TEAM_PLAN), `README.md`, `CHANGELOG.md`, `changelog.d/**`, `packaging/**`, `tools/m0/**`, `src/sae/m0/**` | M0 checklist from §5 | all |

## 3. Change protocol

1. A lead writes `contracts/proposals/NNN-<team>-<title>.md` (NNN = next free 3-digit number; take the
   next free NNN; check the folder first) with:
   **Status: PROPOSED**, contract id(s), what, why, the exact diff (doc text and model fields), affected
   consumers, migration, and whether it is breaking (version bump) or additive.
2. The lead notifies the architect and every consumer lead listed in §2 (SendMessage if available,
   else in its status report; the orchestrator relays).
3. Proposals that change process topology, a settled decision, or another team's design go to
   `sae-adversary` as advisor first. The orchestrator arbitrates disputes; settled decisions go to
   the maintainer.
4. Teams keep building against the current FROZEN version until the architect marks the proposal
   **ACCEPTED** (or **REJECTED** with a reason) in the proposal file.
5. On acceptance the architect updates, in one change: the contract doc (version/status line), the
   models in `src/sae/contracts/`, the schemas (`uv run python -m sae.contracts.export`), the doc
   examples, `CONTRACT_VERSIONS`, and an entry in [`CHANGES.md`](CHANGES.md). `uv run pytest tests/contract`
   must pass before the proposal is marked ACCEPTED.
6. Breaking changes bump the contract's version and the on-wire version field (`SAE|v2|`,
   `sae_p_schema = 2`, `v = 2`, `schema_version = 2`); consumers reject versions they do not know.
7. **Minor versions (v1.N).** A change that alters models or rules but leaves every on-wire version
   field and every byte a v1 producer emits acceptable to the deployed v1 consumers (the mod, the
   game) is `v1.N`: the doc version moves, `CONTRACT_VERSIONS` and wire fields stay at the major.
   Doc-only wording that changes no model and no rule stays a v1 clarification (no version change).

## 4. Conventions

- Formats are boring: pipe `key=value` lines (C1), fixed console templates (C2), JSON with a version
  field everywhere else. All models are frozen and forbid unknown fields.
- Game dates are `YYYY.MM.DD` (months of 30 days). Integers written into the game stay within ±1,000,000.
- **Examples.** Fenced blocks are executable: `sae-log`, `sae-ini`, `json model=<Name>`,
  `jsonl model=<Name>`, `toml model=SaeConfig`; add `invalid` for examples that must fail;
  `id=<x>` / `compiled-from=<x>` pair a C3 batch with its exact C2 text. Other fences are prose.
- Regenerate schemas after any model change: `uv run python -m sae.contracts.export`
  (a test fails if the committed schemas drift).

## 5. UNVERIFIED assumptions (input to T8's M0 checklist)

M0 test ids follow the integration plan (M0-1..M0-11, M0-13), plus M0-12 = the resource proposal's
baseline run (s/month, RAM, fault counter, first R-3), and **M0-14 = the integration plan's Ironman
check** (renumbered because the resource proposal reused 12). M1-P and R-x are from the resource
proposal. Nothing here is VERIFIED without evidence from the game.

| Id | Assumption | Test | Contract |
|---|---|---|---|
| A-01 | `sae_bridge` loads with zero errors alongside the playset | M0-2 | C1 |
| A-02 | `log` effect exists in 4.4 and writes to `game.log` | M0-3 | C1 |
| A-03 | `game.log` path is `<user_dir>/logs/game.log` | M0-3 | C1, C8 |
| A-04 | Game prefixes log lines with its own text | M0-3 | C1 |
| A-05 | Localisation date tokens print `Y.M.D` | M0-3 | C1 |
| A-06 | `[This.<var>]` prints plain decimals | M0-5 | C1 |
| A-07 | Log flush ≤ 5 s | M0-4 | C1 |
| A-08 | `run sae_in.ini` reads `<user_dir>/sae_in.ini` | M0-6 | C2, C8 |
| A-09 | Console `effect` + `every_country = { limit … }` work | M0-6 | C2 |
| A-10 | `check_variable` equality and `set_variable` from console | M0-6 | C2 |
| A-11 | `run` accepts ≥ 2048-char lines and LF line endings | M0-6 | C2 |
| A-12 | `run` executes all lines in order without stutter | M0-6 | C2 |
| A-13 | A multiplayer trigger exists | M0-6 | C1, C2 |
| A-14 | `on_monthly_pulse` / `on_game_start` event lists merge across mods | M0-3 | C1 |
| A-15 | `game.log` truncated on launch | M0-3 | C1 |
| A-16 | Country names may contain any char; log is UTF-8 | M0-3 | C1 |
| A-17 | Variables hold integers to ±1,000,000 exactly (`CLAUSEWITZ_INT_MAX`) | M0-5 | C1, C2, C4, C6 |
| A-18 | Autosave = zip(meta, gamestate) text; jomini ≤ 3 s, ≤ 1 GB | M0-11 | C4 |
| A-19 | Contact, ownership, starbase modules, fleet power, tech count locatable in the save | M0-11 | C4 |
| A-20 | Ironman saves parse; `log` works in Ironman | M0-14 | C4, C6 |
| A-21 | Flag-gated additive budgets/subplans change AI behaviour | M0-8, M0-13 | C3 |
| A-22 | A per-size ship-mix knob exists | M0-1, M0-7 | C3 |
| A-23 | Opinion-modifier effect for posture/stance | M0-6 | C3 |
| A-24 | Autosave interval can be monthly | M0-11 | C4, C6 |
| A-25 | CPU-time rate separates paused from running | M1-P | C7 |
| A-26 | Reliable hard-fault counter | M0-12 | C7 |
| A-27 | Job memory limit makes the Rust worker abort | R-5 | C7 |
| A-28 | Background mode lowers I/O/memory priority | R-5 | C7 |
| A-29 | Mod script ≤ 1 % s/game-year | M0-12, R-3 | C7 |
| A-30 | `sae_sid`, `sae_seq_applied`/`pending`, applied econ/mil variables and `sae_managed` flag are stored in the save | M0-11 | C4 |
| A-35 | An on_action fires on save load (HELLO after reload); not used by mod 0.1.0 | M0-3 | C1 |
| A-34 | Autosaves are named `autosave_*.sav` (Ironman: `ironman.sav`) under `save games/<empire>/` | M0-11 | Config glob |
| A-31 | Script can keep a global running counter (`sid`) and month counter (`m`) | M0-5 | C1 |
| A-32 | Script can detect "in contact with the player" to set `sae_managed` | M0-6 | C6 |
| A-33 | Stellaris user folder follows the shell Documents folder (OneDrive) | M0-2 | C8 |
| A-36 | `event_target:global_event_country` exists at `on_game_start` (else first monthly pulse), holds variables, and they persist across save/load | M0-5, M0-11 | C1, C2 |
| A-37 | Variable arithmetic/comparison with variable operands and cross-scope reads (`set/change/subtract/modulo_variable`, `check_variable` `<`,`>`,`>=`,`<=`, `is_variable_set`) | M0-5 | C1, C2 |
| A-38 | SNAP/PLYR stats (fleet power, naval capacity used/max, war count) export to country variables via `export_trigger_value_to_variable` | M0-5 | C1 |
| A-39 | `is_country_type = default`, `is_ai`, `every_country`/`any_country` (all living countries incl. the player), `save_event_target_as`, `country_event` from a global event, opinion-modifier scoping | M0-3, M0-6 | C1, C2 |
| A-40 | `on_monthly_pulse` fires a global event once per game month on day 1; `on_game_start` fires once | M0-3 | C1 |
| A-41 | An unset variable reads as 0; `check_variable` accepts negative literals | M0-5, M0-6 | C2 |
| A-42 | `log` strings resolve `[This.GetName]` and the date token (A-05) | M0-3 | C1 |
| A-43 | The game process is named `stellaris.exe` (Windows, Proton) / `stellaris` (Linux) | M0-12 | C7 |
| A-44 | `IsIconic` on the game's top-level window detects minimise | M0-12 | C7 |
| A-45 | A `psutil.process_iter` rescan on Windows stays under the 100 ms probe bound | M0-12 | C7 |
| A-46 | Each campaign's autosaves live in one folder under `save games/`; a folder name is reused only by a new game of the same empire name | M0-11 | C6, C10 |
