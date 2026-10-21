# SAE — Stellaris Adaptive Engine

An open-source adaptive AI companion for modded Stellaris 4.4: a strategic director that reshapes the AI's
economy, fleet doctrine and posture to counter how you play. It adds no resources and no cheats; it only
reallocates what the AI already has, so its influence has a ceiling. Whether that ceiling is high enough to feel
is the open question the M0 spike answers.

**Status: pre-M0.** All components are built and integrated game-free; nothing has run against the real game yet. Every Stellaris script, console and save
assumption is UNVERIFIED until the maintainer's M0 weekend ([checklist](docs/m0_checklist.md),
[runbook](tools/m0/README.md)).

## How it works

```text
sae_bridge mod ──SAE|v1| lines──► game.log ──► sae daemon ──► belief over 4 archetypes ──► counter-strategy
       ▲                                         ▲                                         (24-month commitment)
       │ console `run sae_in.ini` (you press it) │ autosaves ──► sae-parse (one process per save, exits)
       └──────────────── sae_in.ini ◄────────────┘ bounded Intents (validated, safety floor)
```

- Three processes: the daemon, a per-autosave Rust parser that exits, and optional telemetry.
- No LLM in the runtime loop, no GPU use, never touches the game's priority or affinity.
- Fog-of-war legal: the engine sees the player only through what a managed AI could see.
- Single-player, non-Ironman only. Ironman has no live loop; multiplayer writes are refused.

## Run the prototype (game-free)

```bash
source ~/.cargo/env; (cd parser && cargo build -j 1)        # builds parser/target/debug/sae-parse
uv sync --extra telemetry                                    # telemetry extra only for the .rrd step
uv run sae synth --archetype turtle --years 5 --runs-dir runs   # simulated 5-year game
ls -t runs | head -1                                         # newest run id
uv run sae telemetry --runs-dir runs --run <id> --rrd out.rrd   # Rerun recording; open with `rerun out.rrd`
jq -c '{type, game_date}' runs/<id>/bus.jsonl | head         # one JSON event per line: observation → belief → strategy → intent → ack
```

`sae synth` finds `sae-parse` on PATH, then in `parser/target/release`, then `parser/target/debug`; override with `--set paths.parser_exe=<path>`.
Without `--runs-dir`, runs go to `%LOCALAPPDATA%\sae\runs` (Windows) or `~/.local/state/sae/runs` (Linux).

## Quick start (developers, game-free)

```bash
uv sync
(cd parser && cargo build --release -j 1)         # sae-parse; synth and M0-11 need it
uv run sae synth --archetype turtle --years 5     # simulated game, full loop
uv run pytest -x -q
```

## Component status

| Built and integrated (game-free tests) | Needs the game (not run) |
|---|---|
| T1 `sae_bridge` mod 0.1.0, T2 adapters (`replay`, `synth`, live log/save), T3 `sae-parse`, T4 engine (`sae daemon`), T5 Resource Governor, T6 bus, `sae ctl` and `sae telemetry`, T7 CI, e2e tests and `sae bench`, T8 `sae m0` kit and docs, T9 `sae profile` (campaign index, read-only player profile) and `sae play` (profile update, then daemon). Wave 2 integration done: `synth`, `replay` and `live` (against a fake game dir) run end to end | M0 spike ([checklist](docs/m0_checklist.md)), release gates R-1..R-6 |

Release gates R-1..R-6 need the game and have not been run ([procedures](bench/README.md)). GitHub Actions has
not run yet; `ci.yml` passes actionlint locally.

## Documentation

| Doc | For |
|---|---|
| [User guide](docs/user_guide.md) | install, commands (incl. `sae profile`, `sae play`), presets, privacy, Ironman, multiplayer, troubleshooting |
| [Developer guide](docs/developer_guide.md) | layout, contracts, change protocol, tests |
| [M0 checklist](docs/m0_checklist.md) and [runbook](tools/m0/README.md) | the feasibility weekend |
| [MVP 2-day plan](docs/plans/MVP_2day_plan.md) | the current prototype push |
| [Packaging plan](packaging/PLAN.md) | how the mod, the Python package and `sae-parse` ship |
| [Contracts](contracts/README.md) | the frozen interfaces C1–C9 |
| [CHANGELOG](CHANGELOG.md) | release notes |

## License

MIT (see `pyproject.toml`). StarNet is driven, never vendored (it carries no license).
