# MVP in 2 days: prototype plan

Status: ACCEPTED WITH CHANGES by the maintainer, 2026-10-19: **no scope cuts**. Everything in the original plan is expedited instead. The "Proposed scope cuts" section below is REJECTED and kept only as a record. Concurrency cap was raised from 6 to 8 agents to expedite, then set back to 6 on 2026-10-19 after a second usage-limit stop halted all agents at once (`cargo -j 1` unchanged).

## What "MVP online" can mean in 2 days

| Meaning | Possible in 2 days? | Why |
|---|---|---|
| Game-free prototype: the whole engine loop running on synthetic or recorded games, with a recording to show | **Yes. It already runs.** | Measured 2026-10-19, see below |
| Running inside a real Stellaris game | **No, not from this machine** | Stellaris is not installed here. Every mod, console and save behaviour is UNVERIFIED until the maintainer runs M0 on their own PC (one weekend, by design). |

The 2-day target is the game-free prototype plus a ready-to-run M0 kit. The in-game MVP follows M0.

## What runs today (measured, not claimed)

1. `sae synth --archetype <a> --years N --set paths.parser_exe=parser/target/debug/sae-parse` runs the full loop: synthetic save, then `sae-parse`, then the daemon (beliefs, strategy table, 24-month commitment, intents), then an atomic `sae_in.ini`, then the mod simulator applies it and ACKs, then `bus.jsonl`. A 5-year run takes about 6 s.
2. `sae telemetry --runs-dir <dir> --run <id> --rrd out.rrd` writes a Rerun `.rrd` recording (279 KB for 6 years).
3. `sae ctl` (pause, resume, force-strategy, status), `sae replay`, `sae daemon --adapter live` and `sae m0` all start and print usage.
4. The test suite is green: 696 passed, 2 xfailed (T4's M2 calibration cases).

Results per archetype (seed 2, 6 years):

| Player archetype | Strategy chosen | Correct counter? |
|---|---|---|
| turtle | `outscale_siege` | yes |
| tech_rush | `early_punish` | yes |
| balanced | `baseline` | yes |
| early_aggression | `outscale_siege` | **NO. It should be `hold_counterpunch`.** |

## Blockers for a demo (must fix, about 1 day in total)

1. **The early-aggression player is classified as turtle.** The posterior reaches turtle 0.61 against early_aggression 0.28 by 2205. The AI then attacks an aggressor instead of holding, which is the worst possible counter. Owner: T4, with T2 if the synth features are at fault. Estimate: 2–4 h. Accept when each of the 4 archetypes picks its counter in at least 3 of 4 seeds.
2. **The parser path has no working default.** `sae synth` exits with "sae-parse not found" unless `paths.parser_exe` is set. Owner: T2/architect (C8 default). Estimate: 30 min. Accept when `sae synth --archetype turtle` works with no `--set`.
3. **The demo is not scripted.** A single `tools/demo.sh` should run the 4 archetypes, write the `.rrd` files and print a one-line verdict per run. Owner: T7. Estimate: 1 h.
4. **No quickstart.** README needs a 10-line "run the prototype" section. Owner: T8. Estimate: 30 min.

## Proposed scope cuts for the 2-day window (REJECTED by the maintainer: nothing is cut)

| Deferred item | Owner | Why it is safe to defer |
|---|---|---|
| `sae bench` and the R-1..R-6 scripts | T7 | They need the game; they are release gates, not demo needs |
| Windows CI job and full GitHub Actions matrix | T7 | Linux tests already pass locally |
| Second and third adversary hardening rounds per team | all | One review round each is done; more rounds return in integration week |
| DuckDB console queries and the Rerun blueprint polish | T6 | `.rrd` output already works |
| Full user and developer docs | T8 | Quickstart plus M0 kit first |

**Kept:** the M0 kit and checklist (T8), because the in-game MVP depends on them. Also kept: the integer-only knob fix in the mod (T1, 15 min).

## Integration plan (after the demo, about 3 days)

1. **Day 3: e2e harness (T7).** Turn the demo runs into `tests/e2e/`. Assert every decision has observation, belief, strategy, intent and ack. Add CI-1..CI-5.
2. **Day 3: Wave 2 wiring (architect).** Live adapter plus Governor-managed parse worker plus control inbox in one `sae daemon` run against the replay of a recorded session. Route each failure to the owning lead.
3. **Day 4: hardening.** Run the deferred adversary rounds on T2, T6 and T7. Run the Windows CI job.
4. **Day 4: M0 on the maintainer's PC.** Run the M0 checklist and record pass or fail. Each failed assumption uses its documented fallback.
5. **Day 5: Wave 3 (final review).** The adversary lists release blockers, owners fix them, T8 merges the changelog and writes `docs/BUILD_REPORT.md`.
