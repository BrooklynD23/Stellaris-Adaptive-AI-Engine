# Wave B plan review (sae-adversary, 2026-11-14)

Verdict: **ACCEPT WITH CHANGES.** Item 1 accept with changes, item 2 split (2a/2b accept, 2c gated), item 6 accept
with changes. Scope: `docs/plans/opt/SYNTHESIS.md` rows 1, 2, 6; sources `daemon-io.md` r1, `parser.md` r1-r3.
Nothing in the repo changed except this file. Scratch sim: session scratchpad `review_B/sim.py` (drives the real
`ResourceGovernor.on_month`).

## 1. Item 1: spm sampling + ratio (C7 §4.3) -- ACCEPT WITH CHANGES

Bug is real, confirmed from code:
- `src/sae/daemon/loop.py:335-344` emits one `SpmSample` per observation, not per poll. The assembler emits one
  observation per HB, so several HBs in one poll give one ~2 s sample plus samples floored at 0.001 s.
- `src/sae/governor/core.py:116` adds every sample to the windows. C7 §4.3 item 4 says samples below 5 x `poll_s` are
  **not** added. The code does not conform today; the plan does not mention this.
- Sim, 600 months, today's code: 3 s/month -> 8 H1 fires; 4 s + 5 s flush -> 34; 7 s + 5 s flush -> 53.
  Steps of +15 % at 11 s and 23 s: 0 of 1 detected (median of 2 s-quantised samples gives ratio 1.087).

What the proposal really changes. The per-sample floor already exists in the contract. The real change is
(a) replace the per-sample floor with a median(24) >= 10 s floor and (b) use means instead of medians.
Contract-conformant per-sample floor alone: 0 false fires below 4 s, but 2 at 7 s with flush jitter and 0 % step
detection. So (b) is what makes detection work. The proposal is justified.

New findings:
1. **Means are pause-sensitive.** Short player pauses (2-10 s, 25 % of months) at 23 s/month gave 0-4 false H1 per
   600 months. At 12 s with 2-6 s pauses: 0-3. A pause under the 40 % step reset goes straight into mean6. A clamp
   at 1.25 x median(24) halves this but does not remove it. The cost of a false H1 is mild (parse every 2nd save for
   at least 6 months), so a budget is acceptable. It needs a test, not a claim of "0 %".
2. **Threshold dilution.** mean6 is part of mean24, so a sustained step r gives at most r / (0.75 + 0.25 r).
   `spm_h1_ratio` 1.10 therefore needs a slowdown of at least 14.3 %. A +12 % step is never detected (sim 0/8).
   The plan's "+15 % found 8/8" test sits 0.7 points above that edge, so it proves almost nothing.
3. **Silent disable.** Below a median of 10 s/month (likely early game and fast speeds; UNVERIFIED on the game PC)
   the spm trigger never arms. RAM and fault triggers remain, so this is acceptable only if it is visible.

Required changes:
1. **[blocking] Sample at the poll tick, not in `_on_obs`.** `loop.py` cannot see poll boundaries, because `now` is
   read once per observation. Count HB months in `_on_obs` (`pending_k += k`). In the poll path, if
   `pending_k > 0`, call `on_month(SpmSample(months=pending_k, wall_s=(now - last_hb_poll)/pending_k))`, then
   reset. This matches the C7 §4.3 wording.
2. **[blocking] The proposal text must replace C7 §4.3 item 4's sentence** "samples below 5 x `poll_s` are not added
   to the ratio windows" with: "all samples are windowed; the ratio = mean(last 6) / mean(last 24), computed only
   when median(24) >= 5 x `poll_s`; the step test is unchanged". State the 14.3 % effective threshold in the text.
3. **[blocking] Telemetry visibility.** Add C5-additive monthly fields `spm_ratio` (the number the trigger used, or
   null) and `spm_armed` (bool). Today's `spm_med6`/`spm_med24` can no longer reproduce the trigger.
4. Proposal file: `contracts/proposals/NNN-T5-spm-mean-ratio.md` (next free number; 029 is the highest seen), with
   T4 as co-owner for the sampler.

Corrected validation (replaces "1 s and 3 s stay H0", which passes trivially once the floor exists):
1. Unit: 2 HBs in one poll -> exactly one `SpmSample(months=2)`; 0 HB -> no sample.
2. Real governor, 600 months, seeds 0-7, constant 11/23/40 s with a 5 s flush jitter: 0 H1.
3. Same, with pauses (25 % of months, +2 to +10 s): <= 1 H1 per 100 months (budget; measured max 4/600).
4. +20 % step: H1 within 8 months, 8/8. +12 % step: no H1 (pins the documented dilution).
5. Floor boundary: median 9.9 s -> `spm_armed=false`; median 10.1 s -> true.

## 2. Item 2: thread background mode + bench flag (C4 §1, C7 §8) -- SPLIT

Microsoft primary docs (SetPriorityClass, SetThreadPriority, read 2026-11-14):
- Process mode: "lowers the resource scheduling priorities of the process (and its threads)". "If a process is in
  background processing mode, the new threads it creates will also be in background processing mode."
- Thread mode: "lowers the resource scheduling priorities of the thread", only on the current thread. New threads do
  not inherit it (the docs state inheritance for process mode only). Do not mix the two modes: "Do not call
  SetPriorityClass to enter background processing mode after a thread ... called SetThreadPriority".
- Neither page documents the 32 MiB working-set cap, or the exact memory and I/O priority values. The cap is
  third-party evidence (randomascii) plus the T3 measurement. Label it UNVERIFIED in the contract.

What "truthful RSS" means. Commit is 103 MB in **both** modes. Thread mode does not make the parser use less
memory. It lets the parser keep 107 MB resident instead of 33.6 MB. The metric was wrong
(`parser/src/cost.rs:56` reads `PeakWorkingSetSize`, but the Job limit in C7 §8 caps commit). The behaviour change
is a separate trade: -0.1 s idle-class CPU per autosave for +73 MB resident next to the game. On a RAM-starved
machine that may be the wrong direction. Wall time is unchanged (0.56 s). No data under memory pressure exists.

- **2a [blocking, do now] Fix the metric.** On Windows, `cost.rs` reports `PeakPagefileUsage` (peak commit). This
  matches the Job limit unit and precedent 026 (private memory). Keep the field name `rss_peak_bytes` (a rename
  would be breaking). The C4 §2 text says "Windows: peak commit". This alone removes the "67 MB cap under a 103 MB
  commit" hazard.
- **2b ACCEPT.** `sae bench parse` and `spy` pass `--background` (`src/sae/bench/cli.py:59`). Note that bench
  still lacks `IDLE_PRIORITY_CLASS` and the Job (`src/sae/platform/spawn.py:176`). Validation: bench `cpu_s`
  within 10 % of the daemon's `parse_cpu_s` on the same save. Do not claim "matches production".
- **2c GATED (game PC, M0 R-5 / A-28).** Switch to thread mode only after an A/B on the game PC with available RAM
  below 2 GiB, n >= 10 parses per mode. Measure the game process's hard faults/s and frame-time p99 during the
  parse, plus pagefile writes. Accept thread mode only if the game metrics are no worse. If adopted:
  - call it on the main thread before any other thread exists;
  - add a code comment that `--threads > 1` workers must each enter background mode;
  - keep EcoQoS;
  - keep the idle class from the spawner.
- Settled rule: all calls use `GetCurrentProcess()` / `GetCurrentThread()` pseudo-handles of SAE's own worker. They
  never take a PID, so they comply. Keep it that way; add a test or grep check that no priority API takes a PID.

Corrected validation: replace "working set matches commit (~103 MB)". A bigger resident set is not a success
metric. Use: `rss_peak_bytes` within 5 % of externally measured peak commit in both modes, plus the 2c A/B.

## 3. Item 6: meta-only fast path (C4 §1) -- ACCEPT WITH CHANGES

The fast path is output-identical by construction. `extract.rs:492-497` already lets the zip `meta` override the
gamestate `version`/`date`. Skipping the gamestate when meta has both cannot change `meta.*`. Callers:
`src/sae/adapters/live.py:269` (Ironman probe) and `src/sae/bench/cli.py:85` (spy). The whole zip is still read and
stat-checked, so torn writes are still caught by the central-directory open.

Two differences to accept explicitly:
1. A corrupt gamestate no longer fails the probe. Its CRC is never read.
2. A zip with no `gamestate` entry would return ok unless that check is kept.

Required changes:
1. **[blocking]** Keep the `gamestate` presence check (`archive.by_name("gamestate")` without reading). Exit codes
   for "zip has no gamestate entry" stay identical.
2. **[blocking]** Differential test over every parser fixture and the synthetic save: with `--sections meta`, the
   fast path and the forced full path give identical JSON except `cost`. Add cases: meta without `date`, and
   meta without `version` (both -> full path, same output); meta with a malformed date (same error code both ways).
3. Change the C4 §1 text "still inflates and top-level-scans `gamestate`" to "inflates `gamestate` only when the
   `meta` entry lacks `date` or `version`; a corrupt `gamestate` is not detected by a meta-only call". The meta
   layout stays UNVERIFIED (A-20).

## 4. Architecture ratings

| Area | Rating | Why |
|---|---|---|
| Performance and memory | AT RISK | 2c trades +73 MB resident on the game PC for CPU, with no pressure data; 2a fixes the real hazard |
| Telemetry | AT RISK | `rss_peak_bytes` meaning changes; the spm trigger is not reproducible without `spm_ratio`/`spm_armed` |
| Adaptive engine | ON TARGET | Fewer false H1 means more parsed saves (14 -> 21 observations in the daemon-io run) |
| Learning from saves | ON TARGET | More saves parsed, cheaper spy probe; no fairness path touched |

Next action: T5 drafts the spm proposal with required changes 1.2-1.3, and T3 lands 2a first.
