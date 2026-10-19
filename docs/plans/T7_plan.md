# T7 plan — QA, CI and bench (v2, after adversary round 1)

Owner: T7 lead. Paths: `tests/integration/**`, `tests/e2e/**`, `.github/workflows/**`, `bench/**`, `src/sae/bench/**`,
plus `tests/unit/t7/**` (C8 §7 team test folder). Sources: TEAM_PLAN §T7, resource proposal §8 as amended by
`signoff_fable.md`, C4–C8, CHANGES.md (008–013). No partial files from the previous lead existed (all T7 dirs empty
except `.gitkeep` and `src/sae/bench/__init__.py`).

## 0. Facts measured before planning (2026-10-19, WSL2)

1. `sae synth --years 5` runs end to end today (exit 0, 279 bus lines, `intent_emitted` 3, `intent_ack` 10 APPLIED).
   30 years ≈ 16 s with the debug `sae-parse` (120 save parses). `sae telemetry --rrd` writes a 260 KB `.rrd` in 11 s.
2. `intent_ack.decision_id` is null on the bus (optional in C5). The chain links an ack to its decision through
   `(country, seq)` → the `intent_emitted` batch with that `seq`.
3. `pathological_2x` peaks at 265 MB RSS; under `RLIMIT_AS` 256 MiB it aborts (exit 134), small fixtures pass at 64 MiB.
4. Real synth (not T4's fake): `early_aggression` seeds 1–2 settle on `turtle` (0.95) → `outscale_siege` (T4 fixed it in belief.py/features.py; the strict xfails XPASSed and were removed).
   `--archetype a@N` takes N as a year offset from the start, not a calendar year.
5. CI-1 matrix (synth 30 y, 5 schedules × 4 seeds, forced H0 vs H2): pooled rate **0.625** (< 0.90). Seeds barely
   change outcomes. H0 picks `outscale_siege` for early_aggression and balanced; H2 picks `hold_counterpunch` for
   early_aggression at month 24 then flips. Routed to T4; **fixed by T4 the same day**: CI-1 now 1.000 over 20 switches, acceptance 4/4, no xfails remain.
6. Orchestrator (2026-10-19): cap 2 children; deadline 2 days, no scope cuts; known defects xfail with reason
   (early_aggression→turtle T4; `paths.parser_exe` default T2); add a demo (below).

## 1. Definitions used by every test

- **Decision chain.** Two different seqs: envelope `seq` (bus line) and batch `payload.seq` (C2/C3). For every
  `intent_emitted` E with `decision_id` D and `payload.receipt.ok`: the bus holds an `observation`, a `belief_update`
  and a `strategy_selected` with `decision_id == D` and envelope seq < E's envelope seq, and an `intent_ack` A with
  `A.payload.seq == E.payload.batch.seq`, `A.payload.country ∈ {i.country for i in E.payload.batch.intents}` and A's
  envelope seq > E's. A negative unit case makes the envelope and batch seqs collide.
  Every `strategy_selected` has an `observation` and a `belief_update` with the same `decision_id`. Every
  `intent_ack` maps back to an `intent_emitted`. Non-vacuity: ≥ 1 emitted batch, ≥ 1 `APPLIED` ack, ≥ 12 decisions.
- **Switch.** A `strategy_selected` with `switched=true`: `(month_index(game_date), strategy)`.
- **CI-1 match.** A switch in run A matches a switch in run B with the same strategy and |Δ months| <
  `engine.commitment_months`, one-to-one (greedy by time). Rate = 2·matched / (|SA| + |SB|) pooled over campaigns.
  Pass ≥ 0.90 (HYPOTHESIS threshold, proposal §8) with ≥ 8 switches pooled (non-vacuity).
- **Bus digest (determinism).** sha256 over events excluding `health` and `run_started`, each with `run_id`,
  `t_wall`, `t_mono_ns`, `seq` removed and `receipt.path` dropped.

## 2. Work units (one child at a time, in this order)

### U1 Integration tests — `tests/integration/` (CI-2 guard, CI-3 map, CI-4, fog, fixtures)

| File | Test |
|---|---|
| (dropped) `test_fog.py` | Already in `tests/unit/t4/test_boundary.py` (adversary 1). Not duplicated. |
| `test_ci4_imports.py` | Allowed set derived, not listed: `uv.lock` runtime closure of `sae` ∪ `sys.stdlib_module_names` ∪ `sys.builtin_module_names` (dist → import names via `importlib.metadata.packages_distributions()`). Fresh subprocess (`python3 -I`-style, `sys.executable`): (a) run `sae synth --years 2` in-process via `sae.cli.main`, (b) import `sae.daemon`, `sae.daemon.wiring` and resolve every C8 §5.1 factory (no call that touches the game). Dump `sys.modules`. Every top-level name ∈ `sys.stdlib_module_names` ∪ {sae, psutil, numpy, pydantic, pydantic_core, typing_extensions, annotated_types, typing_inspection}; none of torch, jax, jaxlib, cupy, rerun, rerun_bindings, duckdb, polars, pyarrow, pynvml, onnxruntime, tensorflow; no `sae.telemetry`, `sae.bench`, `sae.m0`, `tools.console`. Non-vacuity: `sae.daemon.loop`, `sae.adapters`, `sae.governor` present. |
| `test_ci4_footprint.py` | Same subprocess style, synth 5 years: peak RSS ≤ 150 MiB (`resource.getrusage(RUSAGE_SELF).ru_maxrss` KiB on Linux, `psutil` `peak_wset` on Windows) and every bus `health.metrics.daemon_rss_bytes` ≤ 150 MiB; no mapped GPU library in `/proc/self/maps` (`libcuda`, `libnvidia`, `libvulkan`, `libd3d12`, `dxcore`, `libOpenCL`) at exit (Linux only). |
| `test_ci4_manifest.py` | Parse `uv.lock` (tomllib): no package whose name matches the GPU list (torch*, jax*, cupy*, nvidia-*, pynvml, nvidia-ml-py, onnxruntime*, tensorflow*, pycuda, vulkan*, *directml*, triton) anywhere in the lock, extras included (proposal 010 regenerated lock). Runtime closure of `sae` (dependencies without extras, walked through the lock) excludes rerun-sdk, duckdb, polars, pyarrow. `pyproject` runtime deps == {pydantic, psutil}. Non-vacuity: lock has `pydantic` and `rerun-sdk` entries. |
| `test_fog.py` | C6 §4: AST import graph of every module under `src/sae/core` and `src/sae/daemon` never imports `sae.adapters` (any form, incl. `importlib.import_module("sae.adapters…")` string literals outside `wiring.py`, which is the composition root); regex `GodView|GodObservation|PlayerRecord|\.god\b|bus\.god` over the same trees → 0 hits (comments stripped? no: plain grep, as the contract says). |
| `test_plyr_redaction.py` | Proposal 012: recorded session (U2 helper) whose `game.log` adds well-formed PLYR lines and the three malformed forms from CHANGES.md with a sentinel value `fleet=987650`; run `sae replay`; `bus.jsonl` has no `987650` and no `PLYR|` field text beyond the allowed `SAE|v1|PLYR[|date]` prefix; ≥ 1 `log_parse_error` health present; `bus.god.jsonl` holds the well-formed PLYR value (non-vacuity). Depends on U2's session builder → lands after U2, file owned by U1's later fix loop. |
| `test_ci2.py` | CI-2 not applicable: `pytest.skip(reason="opportunistic queue cut (signoff_fable optional cuts, C7 D-13)")`. CI-3 is a named `ci.yml` step `pytest tests/unit/t5 -k "ci3 or ram_ramp or fault_spike or no_flapping or random_traces"` guarded by a `--co -q` count > 0 (adversary 2). |
| (dropped) old `test_ci2_ci3.py` | CI-2: asserts the opportunistic queue does not exist (`GovernorConfig.pause_detection` default false, no `sae.governor.queue`/`opportunistic` module); the docstring says "write CI-2 when this fails". CI-3: AST check that the named trace tests exist in `tests/unit/t5/` (`test_ci3_drifting_spm_never_above_h1`, `test_ci3_step_clears_windows_and_ratio_needs_24_new_samples`, `test_ram_ramp_reaches_h2_on_crossing_poll_with_single_kill`, `test_fault_spike_after_baseline_h2_after_exactly_three_polls`, `test_sustained_h2_goes_h3_then_no_flapping_at_steady_1_6_gib`, `test_random_traces_hold_invariants`) — no duplication. |
| `test_parser_fixtures.py` | T3 request: import `parser/tools/validate_fixtures.py` by path, run `check()` per fixture (parametrized), skip with reason if the binary is not built. |

Shared: `tests/integration/conftest.py` with `parser_exe` fixture (`parser/target/{release,debug}/sae-parse[.exe]`
via `resolve_paths`, else skip with reason "build parser/").

### U2 End-to-end harness — `tests/e2e/`

| File | Content |
|---|---|
| `_session.py` | (Needs real saves for the real parser, so T2's fake-save test helper cannot be reused; proposal 015-T7 asks T2 for a public recorder; this builder only writes the documented record format and is deleted once 015 lands.) `build_session(root, parser_exe, months, *, extra_lines=None, pathological_at=None)`: a LiveAdapter-record-format session (`game.log`, `game.log.ts`, `saves/<offset:012d>-autosave.sav`) from the public `ModSimulator`/`SimCountry` (1 human + 2 in-contact AIs, as `tests/unit/t2/test_replay.py::build_session`) and, every 3 months, a `FixtureSpec` (C4 §5, contract model) whose sids, country ids and date match the simulator, rendered by `sae-parse fixture`. `pathological_at=k` renders that save with `pad_bytes = 256 MiB`. No T2 private APIs. |
| `_bus.py` | `load(run_dir)`, `check_chain(events) -> list[str]` (§1), `switches(events)`, `ci1_rate(a, b, window)`, `digest(events)`. Unit-tested on hand-made event lists including broken chains (missing belief, orphan ack, ack before emit) so the checker cannot pass vacuously. |
| `conftest.py` | `parser_exe` (same rule as U1), `run_cli(argv) -> (exit, run_dir)` running `sae.cli.main` in a subprocess (`sys.executable -m sae`) with `--runs-dir tmp`, timeout 120 s; `T7_E2E_FULL=1` enables the full seed matrix. |
| `test_synth_chain.py` | `sae synth` per archetype (10 years, seed 1) → exit 0, `check_chain` clean, files `bus.jsonl`, `bus.god.jsonl`, `scratch/sae_in.ini` (never outside the run dir), then `sae telemetry --rrd` → non-empty `.rrd` (`importorskip("rerun")`). |
| `test_replay_chain.py` | `build_session` → `sae replay --speed max --ack-model echo` → chain clean; same session twice → equal `digest`; `--ack-model drop:1.0` → no APPLIED, LOST then one re-emission per C3 (chain checker in "lost" mode). |
| `test_ci1.py` | CI-1: `sae synth` (deterministic, same Governor-gated `SaveParser` as replay/live) for {turtle, tech_rush, early_aggression, balanced, `turtle,early_aggression@12`} × seeds {1, 2} (full: ×4), 30 years, each at `--set governor.forced_level="H0"` and `"H2"`; pooled rate ≥ 0.90. **Deviation:** the proposal says "replay a recorded campaign"; T7 cannot record a synth campaign without T2 internals, so CI-1 runs on synth until T2 adds a recorder (`SynthAdapter(record_dir=…)`, requested, backlog), then switches to replay of that recording. Below 0.90 → `xfail(strict=True, reason="T4: H0/H2 diverge, rate 0.625, routed 2026-10-19")` (orchestrator: known defects xfail with reason), so a fix shows as XPASS. Prerequisite: the synth digest is equal for two same-seed runs (asserted first in the same module). |
| `test_ci5.py` | CI-5: (a) `sae-parse parse` on every fixture + `pathological_2x`: valid `ParseResult` with `cost` fields; (b) recorded session with `pathological_2x/save.sav` injected as one autosave, replay with `governor.worker_mem_cap_bytes` and `worker_mem_cap_closed_bytes` = 128 MiB: bus has `health` `budget_breach` with detail in {`timeout`, `exit=134 …`, `exit=None …`} (RLIMIT_AS abort vs systemd-scope OOM kill; the path taken is printed, adversary 6), observations continue after it (log source), exit 0, later saves parse again. Linux only (`RLIMIT_AS`); Windows path is R-5. |
| `test_archetype_acceptance.py` | T4 acceptance on real synth: each archetype reaches its C3 §6 counter by year 30 in ≥ 3 of 4 seeds (full mode; default 1 seed, report only). Known failing for `early_aggression` (§0.4) and `balanced` (T4 xfails): `xfail(strict=False, reason="T4/T2 calibration, routed")` per archetype that fails, nothing else. `outscale_siege` vs `early_punish` asserted only by strategy name, never by game values. |

T6-dependent pieces: only the `.rrd` step (`importorskip("rerun")`). T6 is still running; any `sae telemetry`
failure is reported to T6 with file:line, not fixed.

### U3 `sae bench` — `src/sae/bench/{__init__,stats,gates,cli}.py`, tests `tests/unit/t7/`

Pure stdlib (+ psutil already a dep); mypy --strict.

| Subcommand | Gate | Input | Verdict |
|---|---|---|---|
| `sae bench parse <save>... [--repeat N]` | none (game-free) | saves | wall/cpu/rss_peak median and max from `cost` |
| `sae bench overhead <runs.csv>` | R-1 | CSV `condition,run,seconds_per_year` (conditions a,b,c,d) | slowdown (c vs a) mean ≤ 2 %, percentile-bootstrap 95 % upper ≤ 3 % (seeded, 10 000 resamples) |
| `sae bench stutter <a.csv> <c.csv>` | R-2 | PresentMon CSVs (`MsBetweenPresents`) | p99 ratio ≤ 1.05 |
| `sae bench script-cost <scaling.csv>` | R-3 | CSV `emits_per_month,seconds_per_year` | OLS slope → projected cost at shipped config ≤ 1 % of s/year; ms per monthly tick |
| `sae bench bus <run_dir> --gate R-4\|R-5\|R-6` | R-4/5/6 | a run's `bus.jsonl` | R-4: first H2 within 10 s of the RAM drop (t_mono_ns), acks continue; R-5: `budget_breach` present, daemon kept running; R-6: final level ≤ H2, monthly observations continue |
| `sae bench spy <save_dir>` | R-1 helper | autosave folder | seconds per game year from autosave mtimes and dates (dates via `sae-parse --sections meta`; A-34 UNVERIFIED) |

| `sae bench demo [--years 6] [--seed 2] [--out DIR]` | demo (orchestrator) | none | runs `sae synth` for the 4 archetypes, `sae telemetry --rrd` each, prints one line per run: archetype, final strategy, expected counter, acks APPLIED, rrd path. `bench/demo.sh` wraps it. |

Every verdict prints `PASS`/`FAIL`/`INSUFFICIENT` plus the numbers; exit 0 pass, 1 fail/insufficient, 2 usage.
Never launches the game, never touches the install or mod folder. Tests use synthetic CSV/bus data.

### U4 CI and release-gate kit — `.github/workflows/ci.yml`, `bench/`

- `ci.yml`: jobs `python-linux` (ubuntu-latest: `astral-sh/setup-uv` pinned, `uv sync --all-extras`,
  `dtolnay/rust-toolchain` pinned stable, `cargo build -j 1` in `parser/`, then `uv run pytest -q`, `uv run mypy --strict src`,
  `uv run ruff check`, named steps for CI-1, CI-3, CI-4, CI-5 with `-k`), `rust-linux` (`cargo fmt --check`,
  `cargo clippy -j 1 -- -D warnings`, `cargo test -j 1`, `cargo check -j 1 --target x86_64-pc-windows-gnu`),
  `windows` (windows-latest: uv + pytest/mypy/ruff with the native `sae-parse.exe`, cargo build/test/clippy;
  RLIMIT tests skip with reason). `T7_E2E_FULL=1` on Linux. `permissions: contents: read`, `concurrency` cancel.
- Validation here: `actionlint` via `uvx --from actionlint-py actionlint` if reachable, else a YAML parse + a pytest
  in `tests/integration/test_workflow.py` that checks job names, that every gate command above appears, and that
  every `uses:` is pinned to a version tag. Stated plainly in status: Actions never ran.
- `bench/README.md`: R-1..R-6 procedures for the maintainer's PC and a second machine (conditions, run counts,
  interleaving, what to record, which `sae bench` command judges it), each marked **requires the game**, nothing
  claimed. `bench/balloon.py` (R-4: allocate until available RAM ≈ 1.2 GB, signoff change 4, holds until Ctrl-C;
  refuses if it would leave < 0.8 GB), `bench/r1_runs_template.csv`, `bench/r3_scaling_template.csv`. No script
  launches Stellaris or writes to its folders.

## 3. Hand-back

`docs/status/T7.md`, `docs/components/T7.md`, `changelog.d/T7.md`. Exit gate: `uv run pytest -q`,
`uv run mypy --strict src/sae/bench`, `uv run ruff check`, `cargo fmt --check` + `cargo clippy -j 1 -- -D warnings`.

## 4. Risks and how tests avoid lying

1. Vacuous passes: every test asserts a non-vacuity count (§1), the chain checker has negative unit tests.
2. Timing: no sleeps; replay at `--speed max`; CI-1/CI-5 judged on bus content, not wall time. R-4's 10 s is a
   release gate (bench), not CI.
3. RSS measured in a fresh process, peak not steady (stricter than the 150 MB steady budget).
4. WSL: `RLIMIT_AS` counts address space, not RSS (T5 gap 5); the CI-5 cap (128 MiB) is chosen from §0.3.
5. Runtime: debug parser 16 s per 30-year run → CI-1 default 20 runs ≈ 5 min. Fixtures prefer `parser/target/release`;
   CI builds `--release -j 1`; synth runs fan out 4 at a time. Re-measured and recorded in status.
6. Later (adversary 8–12): SHA-pinned `uses:`; balloon touches every page; overhead INSUFFICIENT below n=5;
   `/proc/self/maps` scan is cheap and near-vacuous (docstring says so); Windows job has no memory-cap evidence (R-5 only).

## 5. UNVERIFIED (to T8)

A-34 autosave names/dates used by `sae bench spy`; PresentMon `MsBetweenPresents` column name; R-gates need the game.
