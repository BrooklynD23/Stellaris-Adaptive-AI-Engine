# C8 — Configuration, repo layout, presets, CLI entry points

| | |
|---|---|
| Version | 1.5 (`v = 1` in the resolved config) |
| Status | FROZEN v1.5 (2026-10-20; v1.5 proposal 026: telemetry memory budget measures private memory, §2; v1.4 proposal 025: `sae profile` and `sae play` dispatched, `tests/unit/t9/**`; 2026-10-19: v1 + composition-root factories §5.1, proposal 008, maintainer decisions; 009 `health=` keyword, 010 optional extras; v1.2 proposals 019 Part A `open_sink(policy=)`, 020 `paths.sae_dir` + `profile`, 021 `policy`, 022 `record`, D5 launcher entry; v1.3 `engine.rho|likelihood_clip|belief_threshold` default `null` = from policy, W1-1 landed) |
| Owner | architect (contract, `pyproject.toml`, `src/sae/cli.py`) |
| Producers | user (`sae.toml`, env, flags); `resolve_config` |
| Consumers | every team |
| Code | `src/sae/contracts/c8_config.py` — `SaeConfig` and sections, `PRESETS`, `deep_merge`, `env_layer`, `resolve_config`, `new_run_id`; `src/sae/cli.py` dispatcher |

## 1. Layering

Later wins: **built-in defaults < preset < TOML file < `SAE_*` environment < CLI flags**.

- TOML file: `--config <path>`, else `$SAE_CONFIG`, else `./sae.toml` if it exists, else none.
  Read with stdlib `tomllib`.
- Environment: `SAE_<SECTION>__<KEY>=<value>`, e.g. `SAE_PATHS__USER_DIR=/data/stellaris`,
  `SAE_PRESET=dev`. Values are parsed as JSON when possible, else taken as strings; a string that is valid JSON
  (e.g. a folder named `123`) must be JSON-quoted (`'"123"'`). `SAE_CONFIG` itself is not a config key.
- CLI: `--preset <name>` and repeated `--set <section>.<key>=<json-or-string>`. `c8_config.set_layer(sets)` builds this layer
  (same JSON-or-string rule and errors as `env_layer`); every CLI uses it, none re-implements it (proposal 016).
- The preset is the last one named by any layer. Unknown keys or bad values are a config error
  (exit 3); nothing is silently ignored (`extra="forbid"` everywhere).
- The daemon writes the resolved config to `runs/<run_id>/config.resolved.json` (JSON, sorted keys)
  and its sha256 into `run_started` (C5).

## 2. Sections

| Section | Owner of semantics | Keys (defaults) |
|---|---|---|
| top level | C8 | `v` (1), `preset` (`balanced`), `adapter` (`live`), `min_level` (`H0`) |
| `paths` | T2 | `user_dir`, `game_log`, `save_dir`, `ini_path`, `parser_exe`, `runs_dir`, `sae_dir` (v1.2) (all `null` = auto) |
| `engine` | T4 | `rho` `null`, `likelihood_clip` `null`, `belief_threshold` `null` (v1.3: `null` = from the policy; built-in 0.1.0 = 0.02 / 3.0 / 0.45), `commitment_months` 24, `max_managed` 16, `safety_floor_ship_min` 10 (unused while fleet bias is dropped, C3 v1.1), `ttl_months` 3, `seed` 0 |
| `bridge` | T1/T2 | `emit_period` 1, `quiet` 0 |
| `governor` | T5 | C7 `GovernorConfig` |
| `telemetry` | T6 | `enabled` false, `tier` T1, `rerun_url` null, `rrd_path` null, `viewer_memory_limit` "512MB" |
| `profile` (v1.2) | T9 | `mode` `"off"` (`off` / `strict`), `player_key` `"default"` (`^[a-z0-9_]{1,32}$`; one profile folder per key; multi-user PCs set it per OS user). The daemon reads no profile file in v1 (C10 §3 deferred) |
| `policy` (v1.2) | T4 | `pin` `null` (`null` = registry current, else built-in; `"builtin"`; or a policy sha256), C10 §4 |
| `record` (v1.2) | T2 | `max_bytes` 4294967296 (per recording dir), `saves` `"all"` (`all` / `none` / `every_n`), `save_every` 12 (1–1000), `redact_plyr` true (`false` is a config error unless `preset = "dev"`); C6 §3 |

**Detector keys and the policy** (v1.3, proposal 021, CHANGES W1-1 landed). `engine.rho`, `engine.likelihood_clip`
and `engine.belief_threshold` default to `null` = take the value from the loaded PolicyVersion's `detector` (C10 §4);
an explicitly set number overrides the policy value. `null` and an absent key mean the same, so a resolved config
written with every field present (`--resume`) no longer overrides the policy unless a number was set. The engine
core only receives the effective config (`effective_engine_config`), never a `null`. Behaviour is unchanged for
the built-in policy 0.1.0, which carries exactly the old defaults (0.02 / 3.0 / 0.45).

**Telemetry process memory budget** (resource proposal §2 and §8; the signoff does not change it; v1.5 proposal 026,
maintainer decision 2026-10-20): `sae-telemetry` **private memory ≤ 150 MiB**. Private memory is measured per
platform: Linux `RssAnon` (anonymous resident pages, `/proc/self/status`); Windows private bytes (psutil
`memory_info().private`); any other platform full RSS as the fallback. File-backed shared library pages (pyarrow,
rerun) are not counted: they are shared and reclaimable (CI run 39442249597: RSS 152-165 MiB = anon 59-60 MiB +
file-backed 92-106 MiB; WSL2 RSS 118-124 MiB). Above the budget it logs one line and exits 1 (it is optional; the
engine is unaffected). This is a fixed budget, not a config key; the 150 MiB value is unchanged. Unlike the daemon
(C7 §4.6, signoff change 6: breach at 150, exit at 300, on `daemon_rss_bytes`) there is no softer exit tier, because
losing telemetry costs no decision. The daemon budget and its RSS metric are unchanged by v1.5. A larger cap needs
measured memory (R-1 run (d)).

## 3. Path auto-detection (T2 implements)

- `user_dir`: Windows → the shell Documents folder (`SHGetKnownFolderPath(FOLDERID_Documents)`, which
  follows OneDrive redirection, A-33) + `Paradox Interactive/Stellaris`; Linux native →
  `~/.local/share/Paradox Interactive/Stellaris`. Proton is not auto-detected; set `paths.user_dir`.
- `game_log` = `<user_dir>/logs/game.log` (A-03); `save_dir` = `<user_dir>/save games`;
  `ini_path` = `<user_dir>/sae_in.ini` (A-08); `parser_exe` = `sae-parse` on `PATH`, else
  `parser/target/release/sae-parse[.exe]`, else `parser/target/debug/sae-parse[.exe]`, both relative to the
  source checkout (proposal 014-T2). Fixed order, no mtime heuristic: PATH beats a repo build, release beats
  debug, so a stale debug build is used only when nothing else exists. The repo fallback is a dev convenience;
  in a packaged install (`packaging/PLAN.md` §3) the paths do not exist and resolution falls through to
  "not found" (exit 1, "set paths.parser_exe"). Every entry point that spawns the parser resolves once and
  passes the resolved path on (`sae replay`/`sae synth` write it into the run's `paths.parser_exe` when the
  user left it `null`); the resolved path is logged at start, and a wrong binary is caught by the C4
  `schema_version` check.
- `sae_dir` (v1.2) = `%LOCALAPPDATA%\sae` (Windows; `~/AppData/Local/sae` if unset) or
  `${XDG_STATE_HOME:-~/.local/state}/sae` (Linux). Resolved only through `c8_config.sae_dir(config)` /
  `default_sae_dir()`; holds the C10 files and the install salt (C6 §2). Never under `user_dir`.
- `runs_dir` = `%LOCALAPPDATA%\sae\runs` (Windows) or `${XDG_STATE_HOME:-~/.local/state}/sae/runs`
  (Linux): absolute and per-user so `sae daemon` and `sae ctl` started from different directories agree;
  never under `user_dir`.
- SAE never writes inside the game install folder or the user `mod` folder. `ini_path` and its temporary
  sibling `sae_in.ini.tmp` are the only files SAE writes under `user_dir`.

## 4. Presets

| Preset | Overrides | Use |
|---|---|---|
| `low_impact` | `min_level = H1` (sparse parsing), `bridge.emit_period = 3`, telemetry off | players on tight machines |
| `balanced` | none (defaults) | default for players |
| `dev` | telemetry on, tier T2, `bridge.emit_period = 1` | development |

(The resource proposal lists "Low impact" both as an H3 trigger and as "sparse parsing"; v1 resolves it
as an H1 floor. H3 is reached only by RAM triggers. **Confirmed by the maintainer 2026-10-19:**
`low_impact` = H1 floor, `balanced` stays the default preset. Settled for v0.)

## 5. CLI entry points

`sae <command> [args]` (console script `sae = sae.cli:main`). `src/sae/cli.py` dispatches lazily to
`sae.<package>.cli.main(command: str, argv: list[str]) -> int`. A missing implementation prints
`sae <cmd>: not available yet (owner <team>)` and exits 2.

| Command | Module (owner) | Contracted arguments |
|---|---|---|
| `sae daemon` | `sae.daemon.cli` (T4) | common flags; `--adapter live\|release`; `--resume <run_id>` (takes its config only from `runs/<run_id>/config.resolved.json`; only `--runs-dir` may accompany it, any other flag is a usage error) |
| `sae replay <session_dir>` | `sae.adapters.cli` (T2) | common; `--speed realtime\|<N>x\|max`; `--from <date>`; `--stop-at <date>`; `--ack-model echo\|drop:<p>\|delay:<months>\|from_recording` |
| `sae synth` | `sae.adapters.cli` (T2) | common; `--archetype <a>[@<year>][,<a>@<year>...]` (schedule); `--years <n>`; `--seed <n>`; `--record <dir>` (empty or new dir; writes the replay session format, proposal 015) |
| `sae ctl` | `sae.control.cli` (T6) | common flags (to find `runs_dir`); `pause \| resume \| force-strategy <strategy> [--months N] \| clear-force \| status`; `--run <run_id>`; `--reason <text>`; `--runs-dir <dir>` (C9) |
| `sae telemetry` | `sae.telemetry.cli` (T6) | `--run <run_id>`; `--rerun-url <url>` or `--rrd <path>` (the optional third process) |
| `sae bench` | `sae.bench.cli` (T7) | defined in T7's plan; entry point only is contracted |
| `sae m0` | `sae.m0.cli` (T8) | defined in T8's plan; entry point only is contracted |
| `sae profile update\|show\|delete\|close\|exclude\|label` | `sae.profile.cli` (T9, v1.4) | C10 §1–§2 (v1.1 rules); refuses while the game process is detected unless `--force`; idle priority |
| `sae play` (launcher, maintainer D5) | `sae.profile.launcher` (T9, v1.4) | common flags; runs `sae profile update` when `profile.mode != "off"`, then starts `sae daemon` with the same flags; on a profile-update failure it prints a visible notice and **still starts the daemon**; never launches Stellaris, never touches its install or mod folder |

Planned, **not dispatched yet** (added to `src/sae/cli.py` only when the module exists; plan §6 WU-5..WU-7, D5):

| Command | Module (owner) | Contracted behaviour |
|---|---|---|
| `sae tune detector` | `sae.tune.cli` (T9, WU-6) | C10 §4, plan §4 |
| `sae policy prereg\|approve\|reject\|promote\|rollback\|pin` | `sae.profile.policy_cli` (T9, WU-7) | C10 §5 human-only rows: interactive TTY, typed 8-hex confirmation |
| `sae gate stage1\|stage2` | `sae.bench.gate.cli` (T7, WU-7) | C10 §6 |

Common flags: `--config <path>`, `--preset <name>`, `--set <k>=<v>` (repeatable), `--runs-dir <dir>`.
`replay` and `synth` construct their adapter and call the daemon loop through one function T4 exports:

```python
# sae/daemon/__init__.py (T4); AdapterFactory is in sae.contracts.c6_adapter
AdapterFactory = Callable[[SaveParser, GodSink], GameAdapter]
async def run(config: SaeConfig, adapter: GameAdapter | AdapterFactory, run_id: str) -> int: ...
```

`adapter` may be a `GameAdapter` (its parser and god sink are the caller's responsibility) or a factory
the daemon calls once with its Governor-gated `SaveParser` (C7) and the bus writer's `GodSink` (C5/C6).
Replay/synth CLIs should pass a factory, so the bus, parser, run dir and `CURRENT` stay owned by the
daemon alone (C5 single writer). Proposal 004-T4.

### 5.1 Composition root (daemon factories)

`sae.daemon.wiring` (T4) is the only module that imports other teams' packages. It resolves every
factory below (via `importlib`) before calling any; a missing module or attribute is
`ComponentUnavailable(component, owner)`. Owners build against these signatures, not against
`wiring.py`. Extra keyword-only parameters with defaults are allowed; positional order is frozen.

| Factory | Owner | Returns |
|---|---|---|
| `sae.bus.open_sink(run_dir: Path, run_id: str, config: SaeConfig, *, resume: bool = False, policy: PolicyRef \| None = None)` | T6 | bus writer satisfying T4's `BusSink` port (`emit`, `flush`, `close`) and C6 `GodSink`; writes `run_started` unless `resume` (C5 §1), with `payload.policy = policy` (v1.2, proposal 019; wiring resolves the policy, C10 §4, **before** calling `open_sink`) |
| `sae.control.open_inbox(run_dir: Path)` | T6 | C9 file inbox satisfying T4's `ControlInbox` port (`poll`, `complete`) |
| `sae.governor.make_governor(cfg, min_level, preset, *, cpu_count=None, emit_period=None)` | T5 | C7 `Governor`; wiring passes `emit_period=config.bridge.emit_period` (C7 §1) |
| `sae.platform.make_sensors(game_names=None)` / `make_platform()` | T5 | C7 `Sensors` / `Platform` |
| `sae.adapters.make_adapter(config: SaeConfig, parser: SaveParser, god_sink: GodSink, *, health: Callable[[HealthKind, str \| None], None] \| None = None)` | T2 | C6 `GameAdapter` for `live` / `release`; wiring passes `health=` (C6 §1.8, proposal 009) |

The ports `BusSink` and `ControlInbox` live in `sae.daemon.ports` (T4) and are satisfied structurally.

Exit codes for every command: 0 success; 1 runtime failure (logged); 2 usage; 3 invalid config.

## 6. Run directory

```text
runs/
  CURRENT                      text "<run_id> <pid>" of the running daemon (written at start, removed on clean exit).
                               At start, a CURRENT whose pid is alive and is an `sae` process → refuse to
                               start (exit 1, two daemons would race on sae_in.ini); a dead pid → overwrite. Exception: with
                               `--resume <id>` where CURRENT names the same run_id and its pid is this
                               process, its parent, or dead → proceed.
  <run_id>/                    run_id = YYYYMMDD-HHMMSS-<mode>-<4 hex>, UTC
    config.resolved.json
    bus.jsonl                  C5
    bus.god.jsonl              C5 (god view; engine never reads)
    control/inbox/  control/done/   C9
    scratch/sae_in.ini         replay/synth only (never the real game folder)
    state.json                 T4-owned engine snapshot for --resume (format T4's, versioned)
    telemetry.rrd              optional (T6)
```

## 7. Repository layout and ownership

| Path | Owner |
|---|---|
| `contracts/**`, `src/sae/contracts/**`, `pyproject.toml`, `src/sae/cli.py`, `src/sae/__init__.py`, `src/sae/__main__.py`, `tests/contract/**`, `.python-version`, `.gitignore` | architect |
| `mod/sae_bridge/**` | T1 |
| `src/sae/adapters/**` | T2 |
| `parser/**` | T3 |
| `src/sae/core/**`, `src/sae/daemon/**` | T4 |
| `src/sae/governor/**`, `src/sae/platform/**` | T5 |
| `src/sae/bus/**`, `src/sae/telemetry/**`, `src/sae/control/**`, `tools/console/**` | T6 |
| `tests/integration/**`, `tests/e2e/**`, `.github/workflows/**`, `bench/**`, `src/sae/bench/**` | T7 |
| `docs/**` (except `docs/TEAM_PLAN.md`), `README.md`, `CHANGELOG.md`, `changelog.d/**`, `packaging/**`, `tools/m0/**`, `src/sae/m0/**` | T8 |
| `docs/TEAM_PLAN.md` | architect |
| `src/sae/profile/**`, `src/sae/tune/**`, `tests/unit/t9/**` | T9 (v1.2; tests v1.4) |
| `src/sae/policies/**` (built-in policy package data) | T4, maintainer review required (C10 §4) |
| `tests/unit/<team>/**` (e.g. `tests/unit/t4/`) | that team |

Dependencies: runtime `pydantic`, `psutil` only. Dev-only: `pytest-cov` (coverage is run explicitly with
`uv run pytest --cov`, never in the default `addopts`; `[tool.coverage]` `fail_under = 90`). The daemon's import whitelist is stdlib, psutil,
NumPy, pydantic (resource proposal); `rerun-sdk` is allowed only in `sae.telemetry`. Optional extras
(proposal 010): `telemetry = ["rerun-sdk==0.39.0"]` (pinned exactly; imported lazily by `sae.telemetry`
only) and `console = ["duckdb>=1.1,<2"]` (imported by `tools/console/` only). No CUDA, DirectML, Vulkan, torch, jax, cupy, pynvml, duckdb or polars
in runtime dependencies (zero-GPU manifest rule; CI-4).

## 8. Examples

```toml model=SaeConfig
preset = "dev"
adapter = "synth"

[paths]
runs_dir = "/tmp/sae-runs"

[engine]
seed = 42
belief_threshold = 0.5

[governor]
pause_detection = false
```

v1.2 sections:

```toml model=SaeConfig
[paths]
sae_dir = "D:/sae-state"

[profile]
mode = "strict"
player_key = "danny"

[policy]
pin = "builtin"

[record]
saves = "every_n"
save_every = 6
max_bytes = 2147483648
```

Invalid: raw PLYR recordings outside the dev preset.

```toml model=SaeConfig invalid
[record]
redact_plyr = false
```

Invalid: a pin that is neither `builtin` nor a sha256.

```toml model=SaeConfig invalid
[policy]
pin = "latest"
```

Invalid: unknown key.

```toml model=SaeConfig invalid
[engine]
learning_rate = 0.1
```

Invalid: a preset floor of S (operator-only, C7 §3).

```toml model=SaeConfig invalid
min_level = "S"
```

Invalid: unknown preset.

```toml model=SaeConfig invalid
preset = "ultra"
```

```json model=SaeConfig
{"v": 1, "preset": "low_impact", "adapter": "live", "min_level": "H1", "bridge": {"emit_period": 3, "quiet": 0}}
```

## 9. Versioning

New optional keys with defaults: contract change via proposal, `v` stays 1. Renaming, removing or
changing the meaning of a key, preset or CLI argument listed here: `v = 2` with a migration note.

## 10. UNVERIFIED assumptions

| Id | Assumption | Test | Fallback |
|---|---|---|---|
| A-03 | `game.log` under `<user_dir>/logs/` | M0-3 | `paths.game_log` |
| A-08 | `run` reads `<user_dir>/sae_in.ini` | M0-6 | `paths.ini_path` |
| A-33 | Documents redirection (OneDrive) is where Stellaris puts its user folder | M0-2 | `paths.user_dir` |
