# Review: Fable 5.1 on Draft O (Opus 5.5), resource architecture for SAE

Reviewer: Fable 5.1. Date: 2026-10-18. Both drafts read in full; three web lookups spent this pass (SetPriorityClass, JOBOBJECT_CPU_RATE_CONTROL_INFORMATION, Rerun limit-ram). Claims I checked are marked **VERIFIED**; claims I assert from knowledge and did not check this pass are marked **UNVERIFIED**.

## 1. Where we agree

The two drafts reach the same verdict independently: no GPU work exists in v0–M4; the main thread is the host and gets the strictest budget; RAM and paging, not CPU, are the first thing that hurts; the parse worker is a separate OS process with a hard memory cap; Rerun's viewer is off the game PC by default (its 75%-of-RAM default is **VERIFIED**); the daemon is BELOW_NORMAL, the worker is in background mode, EcoQoS on both; never touch the game's priority or affinity; a second machine is where benchmark throughput comes from, one Stellaris instance per box; the mod's own script cost is the one cost no governor can fix, so M0 must measure it before engine code is written. Opus's Windows citations on IDLE preemption, background mode's "will never be starved", and the CpuRate "percent × 100" unit are all **VERIFIED** verbatim.

## 2. Where Opus is better and I concede

- **Separate Rust executable, not PyO3.** Opus's reason is the right one: a parse is the only memory spike, and a child that exits returns every byte and never fragments the daemon's heap. My "PyO3 or standalone" hedge was weaker. Concede, and the daemon stays Python as the report says; its work is trivial and the risk is import bloat, which Opus names precisely.
- **Staggered per-country emission with `sae_emit_period`.** Flattening the monthly script cost instead of spiking it every N months is a genuine improvement over my "fewer countries, bucketed variables". One implementation note: countries have no stable small integer index in script, so the stagger needs a per-country variable assigned on first emit (a running counter), not `i`.
- **Pause detection from the Stellaris process's CPU-time rate.** My draft said "from the log or the save header", and neither is a real source: no monthly event fires while paused, and the save does not record pause. Opus's heuristic is a testable hypothesis; mine was a gap.
- **Section-skipping deserialisation and recording parse peak RSS in M0-11.** Cheap, and it settles my own "memory bites first" risk with a number.
- **Every job reports `rss_peak, cpu_s, wall_s`.** Costs nothing and gives the Governor a cost model. Keep.
- **AT-5 and AT-6 as replay tests needing no game.** Testing "back-off coarsens timing but not decisions" and "opportunistic results never enter the decision path" offline is better than my A3, which needs a live game.

## 3. Where I disagree, with proposed resolutions

**a. One ladder conflates two axes.** L0–L5 mixes game health (spm ratio, RAM) with opportunity (paused, closed). These are orthogonal: a healthy game that is paused should run opportunistic jobs; a sick game that is paused should run nothing heavy and still hold its RAM. Opus's §4 handles pause separately, which concedes the point. Resolution: state = (health level, activity state). Health levels can keep Opus's names but with fewer, robust triggers (below). Activity is PLAYING / PAUSED / CLOSED.

**b. The spm thresholds (+3%, +5%, +10%) are inside the noise.** Seconds per month late game swings far more than 3% month to month (wars, pop growth, fleet battles, the autosave write itself). A ratio against a rolling baseline with 2-month escalation will chatter between L0 and L2, and because the baseline drifts upward with game age the Governor will trend toward L3 on a healthy game. My 25% was too loose in the other direction. Resolution: escalate on hard faults and RAM first, which are unambiguous; use spm only as median-of-6 against median-of-24 with a ≥10% threshold and ≥6-month hysteresis both ways. Treat the in-situ self-A/B (3 months at L4 every N years) as a logged diagnostic only, as Opus says; 3 months is too short to be an estimate.

**c. Mod-script budget metric.** Opus: ≤1% added seconds per game year by A/B. Five runs per condition cannot resolve 1% against run-to-run variance; the test cannot fail meaningfully. Mine, ≤15 ms per tick, assumes a timer that may not exist. Resolution: a scaling test. Same save, three conditions: `sae_quiet`, `sae_emit_period=1`, and a deliberately heavy `period=1` with 5× the emits. If cost is linear in emits, the slope gives the per-emit cost with far more power than a 1% A/B. The whole-system 2–3% gate stays at the system level only.

**d. Job protocol for LAN and GPU workers is over-engineering for M1.** Length-prefixed msgpack frames, TCP transport, shared tokens, optional TLS, deadlines with fallback, for a worker that in M1 is one Rust exe reading one file. The report already says the plans are three times too big. Resolution: M1 = `sae-parse <save> --sections …` writing one JSON document to stdout, with the cost fields in it. The M4 farm is file-based anyway (start save in, bus files out; rsync suffices). Add a network protocol only when a second workload exists, which is M5 at the earliest.

**e. Job Object layout as drawn is unlikely to work.** The daemon runs in a Job with a 250 MB per-process limit and spawns the worker, which should get a 1 GB limit. A child inherits its parent's Job; nested Jobs are allowed since Windows 8 but a nested Job's limits cannot be looser than the parent's (**UNVERIFIED** this pass, from my knowledge of the Job Object docs). So the worker either dies at 250 MB or needs `CREATE_BREAKAWAY_FROM_JOB` with the daemon's Job marked breakaway-OK. Resolution: don't put the daemon in a Job at all (watch its own RSS with psutil and exit cleanly on breach); Job-cap the worker only.

**f. Hard CPU cap on the daemon.** `JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP` stops all the Job's threads until the next interval (**VERIFIED**). The daemon's one latency-sensitive moment, writing `sae_in.ini` and acking, coincides with the log burst; a hard cap plus Python GC is a way to miss it. Also, CpuRate is a share of system cycles, not of one core (**UNVERIFIED** detail, but the doc says "portion of processor cycles"), so "5%" on a 16-thread box is ~80% of a core and the two budget numbers in §6 are in different units. Resolution: NOTIFY-only on the daemon, hard cap on the worker, and state budgets as "% of one core".

**g. NVML sensing.** Opus imports `pynvml` only when the compute worker is GPU-enabled; I said never in v0–v2. Same outcome in practice. Resolution: VRAM sensing lives in the compute worker, never in the daemon, and "no reading" means "GPU unavailable", as Opus writes.

**h. M5 surrogate track and "design the seam now".** Agree that a surrogate is the only shape where a GPU pays. Disagree that it should shape v0: a seam is free only when it is a one-paragraph interface, not a protocol with transport and auth. Keep M5 as written, delete the implied M1 work.

**i. AT-1/AT-2 are release gates, not CI.** 4 conditions × 5 runs × 10 late-game years at fastest speed, reboot-fresh, is hours per gate. Fine at M3 and before each release, on the second machine; drop "reboot-fresh" and run (b) and (d) once, (a) vs (c) five times. PresentMon is the right tool for AT-2 and belongs only there, as Opus says.

**j. IDLE vs BELOW_NORMAL.** Opus argues against IDLE for the daemon; my draft already had the daemon at BELOW_NORMAL and only the worker at IDLE, so there is no real disagreement. Note that `PROCESS_MODE_BACKGROUND_BEGIN` sets the worker to idle-class CPU priority plus low I/O and memory priority anyway. Resolution: daemon BELOW_NORMAL + EcoQoS; worker background mode + EcoQoS; neither pinned with hard affinity.

## 4. Factual claims to correct or mark unverified

1. **Background mode must be entered from inside the worker.** "Start, then call `SetPriorityClass(PROCESS_MODE_BACKGROUND_BEGIN)`" reads as if the daemon does it. The doc says the value "can be specified only if hProcess is a handle to the current process" (**VERIFIED**). The Rust exe must make the call itself (the `windows` crate), so the Governor passes a flag, not a handle. Opus's quotes from this page are otherwise accurate.
2. **Job nesting / CPU-rate units**, as in 3e and 3f: the design as drawn is inconsistent; the cited facts are right, the composition is not (**UNVERIFIED** composition, from knowledge).
3. **Rerun `--server-memory-limit` default 1 GiB and port 9876** are cited to the CLI reference, which I did not fetch; the limit-ram page I did fetch mentions neither (**UNVERIFIED** by me, plausible). The 75% viewer default is **VERIFIED**.
4. Minor: the "spare cores" claim leans on one Steam anecdote; both drafts share that weakness. The 4.3 dev diary supports "batched threading plus a serial tick", not per-core figures. Keep it labelled as a report of reports, as Opus does.

## Bottom line

Adopt Opus's topology (Python daemon, Rust parse exe, bus-fed telemetry, staggered emits, cost-reporting jobs, replay-based ladder tests). Simplify its Governor to two axes with robust triggers, drop the network job protocol until M5, fix the Job Object composition and the CPU-cap units, measure script cost by scaling rather than by 1% A/B, and keep the GPU answer exactly as both drafts have it: none until a surrogate exists.
