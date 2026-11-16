# Opt Waves 1-2: post-implementation architecture review (wave gate)

2026-11-16, `sae-adversary`. Tree: uncommitted on `feat/opt-wave-a1`, base `9dcee50`. Read-only review; probes
ran in a scratch directory. The unit suite was started but did not finish inside this review: test status is
not confirmed here.

## Verdict: DONE WITH FOLLOW-UPS, after 3 blocking items

| Area | Rating | Why |
|---|---|---|
| 1. Performance and memory | ON TARGET | Meta fast path, fsync gating, tuner memo. No new resident state beyond 2 small `FeatureMemory` fields |
| 2. Telemetry | ON TARGET, one caveat | 4 new keys reach bus, mapping and views. `engine_ms` is wall time including `await adapter.emit` |
| 3. Properly adaptive engine | AT RISK | D1 without a guard, and almost any single feature is confident alone in one tick |
| 4. Learning the player from saves | AT RISK | `sae play` now updates last; re-exec and game-running refusals mean many sessions never update |

## Blocking

1. **Expansion-rate gap hole, reproduced.** `src/sae/core/features.py:135` keys the visibility run on in-contact
   observers, not on observers that report `systems_owned`. Observers 1 (20 systems) and 2 (12): observer 1 stays
   in contact but reports `systems_owned=None` for two saves, then returns with 21. Rate at month 60 = 3.0, which
   alone gives P(early_aggression) 0.566 >= 0.45 and locks `hard_counter` for 24 months.
   Fix: `vis = frozenset(v.observer_sid for v in views if v.systems_owned is not None)`, plus that test in
   `tests/unit/t4/test_features.py`.
2. **W1-9 was not recorded.** No file holds the wrong-lock rate, lag per archetype, or the W1-2 "lost at 12, back
   at 24" posterior. Plan risk 1 and section 9 rest on them. Fix: run W1-9 (seeds 0-9 x 4 archetypes), record
   numbers and commands in `docs/status/T7.md` before any Demo v1 claim.
3. **`sae play` + daemon re-exec is undocumented and wrong on both platforms.** POSIX `os.execv`
   (`src/sae/daemon/runs.py:169`) replaces the launcher, so no update ever runs. Windows: `daemon_main` returns 0
   while the child daemon keeps running, so the update runs beside a live run or prints the wrong hint.
   Fix: add consequence 4 to C8 section 5 (proposal 033 amendment); in `src/sae/profile/launcher.py` skip the
   update when the daemon exited by re-exec.

## Single-tick false-confidence paths that remain (D1 exposure)

Measured: one evidence tick from a uniform prior, builtin policy (clip 3.0, threshold 0.45).

| Feature alone | Top posterior | Artefact route |
|---|---|---|
| `fleet_power_cat` = +2 / -2 / -1 | 0.685 / 0.638 / 0.467 | Category is relative to each observer; the lower median moves when the observer set changes |
| `border_fort_ratio` = 0.0 / >= 0.85 | 0.681 / 0.66 | No minimum denominator: one frontier system seen decides it |
| `at_war` = True | 0.65 | Any war counts, including a defensive one and one the managed AI declared (self-induced loop) |
| `tech_cat` = +2 | 0.625 | Same observer-relative route as fleet |
| `expansion_rate` = 0 / >= 3 | 0.539 / 0.566 | Same observer's view grows (sensor range, intel): 5 -> 18 visible systems reads as 4.3 per year. Not detectable from the observer set |

After a gap `k` is large, so the prior has decayed toward uniform and the first tick back has full weight.
D1 widens the exposure from window boundaries (1 tick in 24) to every tick of an abstention window.
The guard question is the maintainer's (D1 is final); the feature-level fixes below do not reopen it.

## Answers to the gate questions

- **Governor end to end:** yes for the false-H1 bug. HBs are counted in `_count_hb`, one sample per poll,
  window means, armed only at median >= 10 s. Not proven on a real daemon run in this tree.
- **Stack-flip metrics:** measurable. Caveats: `engine_ms` includes emit I/O and other coroutines' time, and the
  triggering observation is booked to the next month; Windows `private` is commit, not working set (A-49).
- **Meta-only parser path:** safe for all 3 callers (Ironman probe, `bench spy`, M0 check). `ironman` comes from
  zip `meta` on both paths. Rests on A-47.
- **`sae play` / `sae gate`:** documented in C8 v1.7 except blocking item 3. Exit 5 is checked before config.
- **Wave 3 readiness:** no Wave 1-2 change invalidates W3_DESIGN rev 2.1. `vis` and `run_start_mi` round-trip
  through `state.json` and old state loads. Land blocking item 1 before W3-0 captures its golden.

## Follow-ups for later waves

1. Minimum `frontier_systems` (e.g. >= 3) before `border_fort_ratio` is evidence.
2. Drop observer-relative categoricals on the evidence tick where the observer set changed.
3. `at_war`: exclude wars where a managed AI is the attacker.
4. Pauses longer than 40 % of spm clear the window and disarm the trigger for 24 months; fast speeds
   (median < 10 s) never arm. Report the `spm_armed` fraction at M0.
5. Split `engine_ms` into CPU time, or exclude the emit await, before using the 50 ms flip rule.

## Honest demo claims

1. "The engine reacts inside a baseline window instead of waiting up to 24 months." Caveat: synth only, circular
   likelihoods, wrong-lock rate not yet measured.
2. "A constant game speed no longer raises a false H1, and the daemon reports its own cost." Caveat: simulated
   governor tests; Windows figures unverified.
3. "A meta-only probe no longer inflates the save." Caveat: real saves' `meta` content is unverified (A-47).
