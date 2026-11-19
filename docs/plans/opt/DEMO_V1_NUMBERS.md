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

## Wave 1.5 before

Tree: HEAD `bf2e56b` plus WO-A contract-only changes (`LegalView.player_offensive_war_count`, default None; no engine
change). No source edits. Synth only; perturbation shapes are an approximation of fog artefacts (UNVERIFIED against
real saves; M0 would replay real saves with a dropped observer).

Commands (sequential, runs dirs empty):

1. `uv run python tools/bench/fog_adversarial_measure.py <empty-dir>` (4 archetypes x seeds 0-9 x 8 perturbations =
   320 replays, plus an unperturbed control `0`). Exit code of every `sae synth` is checked (N7); a failure aborts
   with rc 2. Perturbation starts at 0-6 months after the first observation (the first lock is about month 3, so an
   artefact later than that cannot change the first lock), lasts 12-24 months; (b), (d), (h) persist or span the run.
2. `uv run python tools/bench/demo_v1_measure.py 0.45 <empty-dir>`
3. `uv run pytest -q -o addopts= tests/e2e/test_ci1.py -s` (default seeds 1, 2) and the same with `T7_E2E_FULL=1`
   (seeds 1-4).

Wrong lock = first `table` strategy is not the archetype's counter (or none). "In window" = that lock fell inside the
perturbed window.

| Id | Perturbation | Wrong / 40 | In window | Improvement claim |
|---|---|---|---|---|
| 0 | control, unperturbed | 0 | 0 | n/a |
| a | one of several observers lost | 0 | 0 | not demonstrated |
| b | observer swapped | 0 | 0 | not demonstrated |
| c | one view cut to 1 frontier system | 0 | 0 | not demonstrated |
| d | war by a managed AI | **3** | 3 | meaningful (target 0) |
| e | save gap + (a) | 0 | 0 | not demonstrated |
| f | sole observer lost and regained | 0 | 0 | not demonstrated |
| g | one system bordering 3 observers | **4** | 4 | meaningful (target 0) |
| h | reporting set alternating each save | 0 | 0 | not demonstrated |

(h) fraction of evidence ticks that kept a categorical: **1.000** (control also 1.000). F2 starvation baseline is
zero; "after" is judged against this.

Note on (c) and (f): the plan requires 0 at the perturbed tick; before is already 0 so the rows prove nothing either
way. Synth views are uniform across observers, so the categorical artefacts of (a), (b), (e), (f), (h) do not move the
median; unit tests T-1..T-9 and the property test carry that proof, not this tool.

Baseline `demo_v1_measure.py 0.45`: TOTAL wrong 0 / 40, nodata 0; lag 0 months for turtle, tech_rush,
early_aggression; balanced 21 (artifact, its counter is baseline). All synth exit codes were 0 (the original script
does not check them; the new tool does).

CI-1 pooled switch-match rate before: **1.000 over 20 switches** (default seeds 1, 2); **1.000 over 40 switches**
(`T7_E2E_FULL=1`, seeds 1-4). Stop rule threshold 0.90 untouched.

## Wave 1.5 after (rev 3.1), 2026-11-17

Commands: `uv run python tools/bench/fog_adversarial_measure.py <empty-dir>`,
`uv run python tools/bench/demo_v1_measure.py 0.45 <empty-dir>`, `uv run pytest -q -o addopts= tests/e2e/test_ci1.py -s`
(default and `T7_E2E_FULL=1`). Synthetic data only; observer views are uniform; A-55 UNVERIFIED.

| Pert | Wrong before | Wrong after | Kept frac before | Kept frac after | Target | Status |
|---|---|---|---|---|---|---|
| 0 control | 0 | 0 | 1.000 | 1.000 | 0 | met |
| a observer lost | 0 | 0 | 1.000 | 1.000 | - | not demonstrated |
| b swapped | 0 | 0 | 1.000 | 1.000 | - | not demonstrated |
| c view cut to 1 frontier | 0 | 0 | 1.000 | 1.000 | - | not demonstrated |
| d AI war | 3 | **0** | 1.000 | 1.000 | 0 | **met** |
| e gap + (a) | 0 | 0 | 1.000 | 1.000 | - | not demonstrated |
| f sole observer lost | 0 | 0 | 1.000 | 0.957 | - | not demonstrated |
| g 3 observers x 1 system (rev 2 tool) | 4 | **0** | 1.000 | 1.000 | 0 | **met** |
| h partial flicker (first observer id every other save) | 0 | 0 | 1.000 | **1.000** | >= 0.5 | **met** |
| i every observer id swapped every other save | 0 | 0 | 1.000 | 0.042 | - | by design (see note) |

- `demo_v1_measure` at 0.45: 0/40 wrong; lag 0 for turtle, tech_rush, early_aggression (0 by construction under D1).
- CI-1 pooled: 1.000 over 20 switches (default) and 1.000 over 40 (`T7_E2E_FULL=1`); before 1.000 / 1.000.
- **(g) first measured 6 wrong, then diagnosed as a tool artefact:** the old (g) replaced the player's real views with
  3 views of 1 frontier system, so `border_fort_ratio` went 0.333 -> `None` (the `MIN_FRONTIER=3` rule, as designed)
  and the balanced posterior fell from 0.69 to 0.33, which let D1 lock on fleet and tech alone. The tool now keeps
  the real views and appends 3 observers that see the same single system: 0 wrong / 40, met. Not tested: how often a
  real balanced player has fewer than 3 frontier systems and so loses border evidence (open question for Wave 3).
- **(h) / (i):** the old (h) swapped every observer id (an empty intersection, which rule 4 drops by design) and is
  now row (i): 0.042. (h) is now a partial flicker: 1.000 kept, target >= 0.5, met. Synth views are uniform, so equal
  values only; the Fable probe measured 0.78-0.91 with differing values, which this tool does not exercise.
