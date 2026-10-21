# SAE release gates R-1..R-6 and the demo

The CI tests (CI-1..CI-5) run without the game. The release gates below **require the game** and a human at the
keyboard. None of them has been run, and nothing here claims a pass. Source: resource proposal §8 as amended by
`signoff_fable.md` (R-4 at about 1.2 GB available).

No script in this folder launches Stellaris, edits its install folder or writes to the user `mod` folder. You start
the game yourself. `sae bench …` only reads the files you give it, and prints `PASS`, `FAIL` or `INSUFFICIENT`
(exit 0 / 1 / 1, usage error 2).

## Game-free

| Command | What |
|---|---|
| `bench/demo.sh [--years 6] [--seed 2] [--out DIR]` | `sae synth` for turtle, tech_rush, early_aggression and balanced, writes a `.rrd` per run (needs `uv sync --extra telemetry`), and prints one line per run: archetype, final strategy, expected counter, match, acks APPLIED, rrd path. It reports and does not judge. |
| `uv run sae bench parse <save>... [--repeat N]` | `sae-parse` cost (wall, CPU, peak RSS) over saves. |

## Setup (both machines)

1. The maintainer's PC plus a second machine. R-6 also needs a 4-core / 8 GB laptop with an iGPU.
2. Use the real 30+ mod playset and a save at year ≥ 2350. Copy the save so each run starts from the same file.
3. Fix the game speed at fastest and turn autosave to monthly. Close the browser and other heavy apps. Run on AC power.
4. `uv sync --extra telemetry` and `cargo build --release -j 1` in `parser/`.
5. Conditions:
   - (a) no SAE: mod disabled, no daemon.
   - (b) mod only.
   - (c) mod + `sae daemon` at H0 (default `balanced` preset).
   - (d) (c) + `sae telemetry`.

## Gates

| Gate | Procedure | Record | Judge |
|---|---|---|---|
| R-1 overhead | 10 game years from the same save. 5 runs each of (a) and (c), interleaved a c a c …. (b) and (d) once each. | Seconds per game year per run, from `uv run sae bench spy "<save games>/<campaign>"` (autosave mtimes; A-34 UNVERIFIED) or a stopwatch. Fill `r1_runs_template.csv`. | `uv run sae bench overhead runs.csv`: mean ≤ 2 % and upper bootstrap 95 % ≤ 3 %. Needs ≥ 5 runs each of (a) and (c). |
| R-2 stutter | PresentMon capture during an (a) run and a (c) run of R-1. | Two PresentMon CSVs (column `MsBetweenPresents`, UNVERIFIED for your PresentMon version). | `uv run sae bench stutter a.csv c.csv`: p99 frame time ≤ +5 %. Check the monthly hitch against (a) by eye. |
| R-3 mod-script cost | Same save, (b) only, three configs: `sae_quiet`, `period=1`, `period=1` with 5× emits (M0 kit). | `emits_per_month,seconds_per_year` per config in `r3_scaling_template.csv`. | `uv run sae bench script-cost --emits <shipped emits/month> scaling.csv`: projected cost ≤ 1 % of s/year. Shipped emits per month = 1 HB + 1 SNAP per managed AI (+1 PLYR) at `emit_period 1`. |
| R-4 starved machine | Start the game and (c). Then `uv run python3 bench/balloon.py --leave-gb 1.2` until available RAM is about 1.2 GB. Play 2 game years. Repeat with (a). | That run's `runs/<id>/` and the Windows hard-fault counter (PerfMon `Memory\Pages Input/sec`) for (a) and (c). | `uv run sae bench bus runs/<id> --gate R-4`: H2 within 10 s of the RAM trigger, and acks continue. Compare hard faults (a) vs (c) by hand. |
| R-5 Job + pause | Windows. Set `paths.save_dir` to a folder holding the pathological save (`parser/fixtures/pathological_2x`, or a real one scaled ×2). Run (c), pause 60 s, unpause with RAM above and then below the floor. | `runs/<id>/` | `uv run sae bench bus runs/<id> --gate R-5`: a `budget_breach` and the daemon kept running. This is a proxy. Pause and suspend behaviour is checked by eye; PAUSED is cut in v1 (C7 D-13). |
| R-6 low end | The 4-core / 8 GB laptop, condition (c), 2 game years. | `runs/<id>/` and PresentMon as in R-2. | `uv run sae bench bus runs/<id> --gate R-6`: final level ≤ H2 and no monthly gap. R-2's bounds apply to stutter. |

Write results into `docs/status/` (T8's M0/M3 results), never into this folder's templates.
