# SAE developer guide

## 1. Layout and ownership

| Path | Owner | What |
|---|---|---|
| `contracts/`, `src/sae/contracts/`, `tests/contract/`, `pyproject.toml`, `src/sae/cli.py` | architect | frozen contracts C1–C9 and their pydantic models |
| `mod/sae_bridge/` | T1 | Clausewitz mod (C1 producer, C2 consumer) |
| `src/sae/adapters/` | T2 | log tailer, save watcher, `sae_in.ini` writer, live/replay/synth adapters, mod simulator |
| `parser/` | T3 | `sae-parse` (Rust, jomini) |
| `src/sae/core/`, `src/sae/daemon/` | T4 | engine (features, belief, strategy, validator) and the daemon loop |
| `src/sae/governor/`, `src/sae/platform/` | T5 | Resource Governor, sensors, worker spawning |
| `src/sae/bus/`, `src/sae/telemetry/`, `src/sae/control/`, `tools/console/` | T6 | bus writer/readers, `sae ctl`, telemetry |
| `tests/integration/`, `tests/e2e/`, `.github/workflows/`, `bench/`, `src/sae/bench/` | T7 | CI, benches, release gates |
| `docs/` (not `TEAM_PLAN.md`), `README.md`, `CHANGELOG.md`, `changelog.d/`, `packaging/`, `tools/m0/`, `src/sae/m0/` | T8 | docs, M0 kit, release |

Team docs: `docs/plans/<T>_plan.md`, `docs/status/<T>.md`, `docs/components/<T>.md`; unit tests in
`tests/unit/<t>/`. Never edit another team's paths.

## 2. Contracts

The nine contracts in `contracts/` are the only agreements between teams; the models in `src/sae/contracts/` are
the contracts in code and the JSON Schemas in `contracts/schemas/` are exported from them.

| Id | Interface |
|---|---|
| C1 | `SAE|v1|` log lines (mod → engine) |
| C2 | `sae_in.ini` console command file (engine → mod) |
| C3 | Intent schema and its mapping to C2 |
| C4 | `sae-parse` CLI, JSON output, fixtures |
| C5 | `bus.jsonl` envelope and events |
| C6 | GameAdapter, Observation, fog boundary |
| C7 | Resource Governor |
| C8 | config, layout, presets, CLI |
| C9 | control plane (`sae ctl` file inbox) |

Versions and history: `contracts/README.md` §1 and `contracts/CHANGES.md`.

### Change protocol (README §3, summary)

1. Write `contracts/proposals/NNN-<team>-<title>.md` (next free NNN) with Status PROPOSED, contracts, what, why,
   exact diff, consumers, migration, breaking or additive.
2. Notify the architect and every consumer lead.
3. Topology, settled decisions or another team's design → `sae-adversary` as advisor first; settled decisions go
   to the maintainer.
4. Keep building against the current FROZEN version until the architect marks the proposal ACCEPTED/REJECTED.
5. On acceptance the architect updates doc, models, schemas (`uv run python3 -m sae.contracts.export`),
   `CONTRACT_VERSIONS` and `CHANGES.md`.
6. Breaking → major bump and on-wire version; `v1.N` when every wire byte stays acceptable to deployed v1
   consumers.

## 3. Settled decisions

No LLM in the runtime loop. No GPU on the player's machine through M4. Three processes (daemon, per-autosave
parser that exits, optional telemetry). Never touch the game's priority or affinity. Ironman has no live loop.
v0/v1 strategy selection is a belief → counter-strategy table with 24-month commitments and a safety floor.
Maintainer 2026-10-19: CUSUM `change_score` is diagnostic only; `balanced` default, `low_impact` = H1 floor;
fleet-composition intent dropped until M0-7; multiplayer write guard; `outscale_siege` and `early_punish` share
AI values and differ only in `sae_p_reason` (split returns with the hull mix after M0-7).

## 4. Tests and checks

```bash
uv run pytest -x -q
uv run mypy --strict src/sae/<package>
uv run ruff check
cd parser && cargo clippy -j 1 -- -D warnings && cargo test -j 1
uv run python3 mod/sae_bridge/tools/check_mod.py        # mod static checker (T1)
```

**Coverage.** Run it explicitly, never from addopts: `uv run pytest --cov` (floor: 90 % line coverage,
`fail_under = 90`). `[tool.coverage.run] parallel = true`, so each run writes `.coverage.<host>.<pid>.<rand>`
files; merge them with `uv run coverage combine` before `uv run coverage report`. Concurrent runs (several
agents or shells) must each set their own `COVERAGE_FILE` (for example `COVERAGE_FILE=/tmp/cov-t8`), or they
corrupt a shared `.coverage`. `.coverage` and `.coverage.*` are git-ignored.

Windows-only code sits behind platform checks, is mocked in tests and is labelled "not executed in CI on this
machine". Tests are deterministic and seeded.

## 5. UNVERIFIED assumptions

Every Stellaris script, console or save assumption carries an id from `contracts/README.md` §5 (A-01..A-42) and
the word UNVERIFIED in a comment, e.g. in the mod:

```text
# UNVERIFIED(A-21, M0-8): flag-gated additive ai_budget entry changes AI spending | fallback: ...
```

To add one: propose the registry row (README §5) through the change protocol, tag the code, and add a row to
[`docs/m0_checklist.md`](m0_checklist.md) table B. `tests/unit/t8/test_checklist_drift.py` fails until the
checklist has every registered and tagged id, and every M0 test referenced by the registry exists in
`sae m0` (`src/sae/m0/checks.py`).

## 6. Changelog

Each team writes `changelog.d/<TEAM>.md` with `### Added|Changed|Fixed|Removed (<team name>)` sections. T8 merges
them into `CHANGELOG.md` at Wave 3 with `uv run python3 packaging/merge_changelog.py` (dry run: `--check`).

## 7. Git

No git command (commit, push, branch) until docs and `CHANGELOG.md` are current and the maintainer approved the
diffs (`CLAUDE.md`).
