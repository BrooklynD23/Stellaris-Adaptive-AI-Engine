# Demo v1 numbers (W1-9)

Measured 2026-11-16 on the uncommitted tree on `feat/opt-wave-a1` (Waves 1 and 2 in). Environment: WSL2, 6 cores,
Python 3.12, **machine shared with other agents: load average 11 to 12 throughout**, so wall times are inflated and
noisy; CPU seconds are the steadier figure. Nothing here ran against Stellaris. Run measurements one at a time.

Caveats that apply to every row: all adaptiveness numbers are synth-only and circular (likelihoods were fitted to the
synth generator; V-1 to V-5 open). The meta fast path is UNVERIFIED on real saves (A-47 / A-18, M0-11). No in-game
effect is proven. Windows is UNVERIFIED.

## 1. Reaction lag and 2. wrong-lock rate (synth, seeds 0-9 x 4 archetypes = 40 runs, 6 game years)

Command (per run; `sae bench demo` runs seed 2 only and does not report lag, so the sweep was scripted):
`uv run python tools/bench/demo_v1_measure.py 0.45 <empty runs dir>`, which calls
`uv run sae synth --archetype <a> --years 6 --seed <0..9> --runs-dir <dir>` (parser exe default) and reads `bus.jsonl`.
Definitions: confident = first `belief_update` with max posterior >= 0.45 (builtin `belief_threshold`); counter =
first `strategy_selected` with `reason == "table"`. Wrong lock = that strategy is not the label's table counter
(`sae.bench.demo.ARCHETYPES`).

| Archetype | Expected counter | Lag, months (10 seeds) | Wrong first counter | Before (study) | Caveat |
|---|---|---|---|---|---|
| turtle | outscale_siege | 0 (min 0, max 0) | 0 / 10 | 21 | synth only |
| tech_rush | early_punish | 0 (0-0) | 0 / 10 | 21 | synth only |
| early_aggression | hold_counterpunch | 0 (0-0) | 0 / 10 | 21 | synth only |
| balanced | baseline | n/a: the counter is baseline, so no switch; the 21 the script prints is the next 24-month window boundary, not a lag | 0 / 10 | n/a | artifact, do not quote |
| **All** | | | **0 / 40** | | synth only, circular |

The "before" 21 is the study figure (`IMPLEMENTATION_WAVES.md` §4); not re-measured on old code. Lag 0 means
pre-emption of the abstention window fires on the same tick the posterior crosses 0.45 (first month with data is
`2200.04`). Seed-to-seed variation is zero in this synth, so the 10 seeds add little evidence.

## 3. `sae tune detector` wall time

Fixture: **not the study's exact one** (its 20x200-year clones are gone). Rebuilt with the T9 test builder
`tests/unit/t9/_synth_campaigns.py` (`build_campaigns(..., "live", 6, True, seed=1)`, `MONTHS=2400` = 200 years):
5 tune campaigns plus 1 sealed, 15 MB. Script: `/tmp/.../scratchpad/tunefix.py` (not kept). Run:
`uv run sae tune detector --set paths.sae_dir=<tf>/sae --runs-dir <tf>/runs` (default 180-point grid, memoised).

| Run | Wall | User CPU | RSS | Before (study, 5 campaigns) | Caveat |
|---|---|---|---|---|---|
| 1 | 20.1 s | 14.4 s | 159 MB | 54.0 and 57.6 s CPU, 219 MB; 12.4 s with memo prototype | load ~12; fixture lighter than the study's (observations, not 200-year bus) |
| 2 (policy already proposed) | 18.9 s | 14.1 s | 159 MB | | same policy sha256 `3233c6b5` both runs |

CPU 14.1 to 14.4 s is comparable to the study's 12.4 s prototype figure but not a like-for-like fixture.

## 4. Meta-only probe

Binary: `parser/target/release/sae-parse`, **rebuilt this session** from the current tree (`cargo build --release -j 1`;
the old binary dated Oct 19 predates the fast path). Save: the study's synthetic save (89,190,518 byte gamestate,
12.6 MB zip, `meta` has version and date), kept at scratchpad `data/big100.sav`. 25 interleaved runs, medians of the
parser's own `cost` fields. Full = default (all six sections); the debug-only `SAE_PARSE_FORCE_FULL` hook is not
available in release.

Commands: `parser/target/release/sae-parse parse --sections meta --save <sav>` vs
`parser/target/release/sae-parse parse --save <sav>`.

| Mode | Wall | CPU | Peak RSS | Before (study) | Caveat |
|---|---|---|---|---|---|
| `--sections meta` | 0.018 s | 0.010 s | 15.5 MB | 0.007 s, 14.9 MB | UNVERIFIED on real saves (A-47); load ~12 |
| full | 0.284 s | 0.265 s | 104.6 MB | 0.236 s, 99.9 MB | synthetic layout (A-19) |

Speed-up about 16x wall, 26x CPU. `sae bench spy` was not run (needs real autosaves).

## 5. One-year synth run, fsync count and time

Command: `uv run python <scratchpad>/fs.py synth --archetype turtle --years 1 --seed 0 --runs-dir <dir>`
(wrapper counts `os.fsync` calls, then runs `sae.cli.main`).

| Runs | fsync calls | Wall (loaded) | User + sys CPU | Before | Caveat |
|---|---|---|---|---|---|
| 5 | 3 in every run | 3.2 to 3.9 s (first two runs 11.8 and 3.2 s, cold) | about 0.6 s (0.37-0.46 user + 0.20-0.22 sys) | 40 fsyncs, 0.85 s | WSL2 ext4; NTFS fsync cost UNVERIFIED. 0.58 s in the earlier note matches CPU, not wall |

3 fsyncs confirms the earlier 40 to 3.

> Note (code review N7): under D1 the lag is measured from the first confident tick, so 0 months is the expected value by construction; the wrong-lock rate (0/40) is the informative number. The measurement script does not check the synth exit code.
