# Draft F — Resource architecture for the SAE companion on a resource-starved, heavily modded Stellaris PC

Author: Fable 5.1 (senior advisor). Date: 2026-10-18. Independent of the parallel draft. Builds on `/home/claude/reports/Adaptive Stellaris AI engine.md` and my earlier review. Eight web lookups were spent; each load-bearing claim below is tagged **VERIFIED** (I read the source this pass) or **HYPOTHESIS** (inference, or a claim I could not confirm).

## 0. Verdict in four sentences

The question "can we offload parallel processing to the GPU" has a short answer: **there is nothing in v0–v2 worth offloading.** The engine's online workload is a forgetful Bayes update over five features and a lookup table; it is microseconds of arithmetic per game month, dwarfed a thousandfold by the two things that actually cost CPU, which are parsing a 50–200 MB autosave and keeping the game's own simulation thread unburdened. The local machine's real spare resource is **idle CPU cores and idle wall-clock time** (pauses, the player reading an event, overnight), not the GPU, and the correct architecture is a **resource governor** that spends those cores politely and spends the GPU never, until M4 benchmarks produce a workload that is both parallel and heavy. Heavy compute belongs **offline**: the overnight tuning harness, replay analysis and any future simulator, where a GPU or a second machine can be used without any risk of stealing a frame from the game.

## 1. Verifying the assumptions about the player's machine

**"Stellaris is bottlenecked on one thread; many cores sit idle." — VERIFIED (in the direction that matters), with nuance.** Paradox's own 4.3 performance dev diary reports a 31% speedup over 3.14 in its video run (11.54 s/game-year at start versus 30.52), describes "economic threading changes" with new dependency tracking to "reduce CPU wait time and the monthly stutter", and says that in 3.14 "we had hit the limits on how far we could go with optimization" ([Dev Diary #410](https://admin-forum.paradoxplaza.com/forum/developer-diary/stellaris-dev-diary-410-4-3-performance-comparisons.1899301/page-6)). The diary does not give a main-thread/worker breakdown or per-core utilisation. Player reports are consistent with a hot main thread plus partially threaded batches: "one thread hits 100% utilization and everything dies", total CPU around 33% while running ([Steam discussion](https://steamcommunity.com/app/281990/discussions/0/1741102632995174015)). My reading: the game is **serially bound on one thread with bursts of batched parallel work on the monthly tick**. That means two things for us. First, on an 8-core/16-thread machine there really are 4–8 idle hardware threads most of the time. Second, those idle threads are **not idle during the monthly pulse**, which is exactly the moment when our mod's script runs and when the new autosave lands. A naive "use all spare cores" plan collides with the game precisely when the game is most sensitive.

**"The GPU is barely used." — VERIFIED as a qualitative claim, no hard numbers.** Nobody in the threads quotes a GPU-utilisation figure; the evidence is indirect (disabling 8× MSAA gives +2–5 FPS; "it seems not GPU bottleneck") ([same Steam thread](https://steamcommunity.com/app/281990/discussions/0/1741102632995174015)). I accept that a mid-range GPU has large idle capacity during Stellaris. The important corollary is **not** that the GPU is free for us. It is that the GPU is idle because the workload does not have anything GPU-shaped in it, and neither does ours. The caveat that does bite: VRAM. 30+ mods with 4K portrait packs, ship sets and UI overhauls inflate VRAM residency; the game also renders the galaxy map continuously. Adding a CUDA/DirectML context (hundreds of MB of driver reservation plus our buffers) on a 6–8 GB card can push the game into VRAM eviction, which manifests as hitching on map zoom. I could not find a Stellaris-specific measurement of this, so: **HYPOTHESIS**, but a well-known failure mode of running two GPU contexts on a shared consumer card.

**"Typical player PC." — HYPOTHESIS, deliberately conservative.** I design for 6–8 physical cores, 16–32 GB RAM of which the modded game uses 8–14 GB late game, a 6–8 GB GPU, Windows 11, and the player on Ironman more often than not. Anything we ship must degrade gracefully on a 4-core laptop with an iGPU, because that machine exists in the user base and it is where lag complaints come from.

**"RAM is the scarce resource." — HYPOTHESIS, and I treat it as the binding constraint.** Big saves mean big parses. jomini's parse of a 150 MB uncompressed gamestate produces a DOM several times that size if we are careless. The companion process's RSS is the most likely thing to make a 16 GB machine page, and paging the game is far worse than any CPU contention. The budget in §6 therefore caps memory first.

## 2. Realistic headroom

Spare CPU: 4–8 hardware threads between monthly pulses on a mid-range desktop; roughly 0 during the pulse (the batched economic update fans out). Spare GPU: most of it, nearly always. Spare RAM: 2–6 GB on a 16 GB machine, often less than that late game with 30 mods, and it shrinks as the campaign ages. Spare wall-clock: enormous. A Stellaris session is 30–60% paused by wall-clock for most players (event pop-ups, planning, diplomacy screens) — **HYPOTHESIS** from my own play and common sense; it is cheap to measure from `game_paused` state in the bus and I propose we do (§7).

The honest comparison is between the engine's needs and that headroom. Per game month, v0 does: tail a few log lines (microseconds), update a 3-class belief from ≤ 5 features (microseconds), look up a template (nanoseconds), write a 2 KB `.ini` (milliseconds). Once per autosave interval it parses a save, which is the only real cost: the report's own M0-11 target is ≤ 3 s single-threaded. That is the whole online load. There is no online matrix in the design larger than 3×5. Nothing here saturates one core, let alone needs a second device.

## 3. Process topology and the resource governor

I propose three OS processes, not one, so that each can be given a different and enforceable budget.

```
 Stellaris.exe (untouched; we never set its priority or affinity)
     │ game.log tail, autosave_*.sav, sae_in.ini
     ▼
 sae-engine   (Rust binary via PyO3 or standalone; ~20 MB RSS; BELOW_NORMAL; no heavy deps)
   ├─ log tailer, belief filter, template table, intent compiler, bus writer
   └─ Governor: samples psutil every 2 s; owns the "mode" (PLAYING / PAUSED / IDLE / BACKOFF)
     │ spawns on demand, kills on pressure
     ▼
 sae-parse    (worker; one per autosave; IDLE priority + EcoQoS; hard RSS cap; killed if > budget)
     parses the save with jomini, emits a ~50 KB feature snapshot to the bus, exits
     │ optional, dev only, off by default
     ▼
 sae-viewer   (Rerun; remote by default: `rerun --serve` on the engine, viewer on another machine or opened on demand)
```

**Governor rules.** The governor is a tiny state machine with four inputs: wall-seconds per game month (measured from log timestamps), `game_paused` (from the log or the save header), system memory available (psutil `virtual_memory().available`), and whether the game window is foreground. Its outputs are the mode and the per-worker budgets.

- **PLAYING** (game unpaused, s/month under threshold): engine only; one parse worker allowed, started **≥ 8 s after** the autosave finishes writing so it does not overlap the pulse that produced it; worker pinned to one core via a CPU set and run at IDLE priority with EcoQoS.
- **PAUSED**: the opportunistic window (§5). Parse worker may use up to N−2 cores; replay-analysis and telemetry compaction may run; everything is pre-emptible and checkpointed because the player can unpause any second.
- **IDLE** (no game process, or game minimised > 10 min): full local budget for offline jobs (§5–6).
- **BACKOFF** (s/month rose > 25% versus the last 12-month rolling baseline while our worker was alive, or available RAM < 1.5 GB): kill the parse worker, degrade to log-only observation, re-enter after a cooldown. This is the report's "performance-aware back-off", made concrete, and it is the single most important protection we offer the user.

**Windows mechanics, verified.** EcoQoS is set via `SetProcessInformation(..., ProcessPowerThrottling, PROCESS_POWER_THROTTLING_STATE{ControlMask|StateMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED})`; Windows then "always selects most efficient CPU frequency and schedules to efficient cores" and treats it as a hint; Eco is listed as Windows 11 ([Microsoft QoS doc](https://github.com/MicrosoftDocs/win32/blob/docs/desktop-src/ProcThread/quality-of-service.md), [EcoQoS blog](https://devblogs.microsoft.com/performance-diagnostics/introducing-ecoqos/)). **VERIFIED.** Two warnings follow. EcoQoS on a hybrid Intel part really will push our worker to E-cores, which is good for the game and roughly halves our parse speed; the 3 s target becomes 5–6 s, and that is fine because the data is monthly. EcoQoS on a non-hybrid desktop Ryzen mostly just lowers clocks. Neither hurts. Separately, `PROCESS_MODE_BACKGROUND_BEGIN` (which also lowers I/O and memory priority) is the right flag for the parse worker, since the one thing worse than stealing CPU from Stellaris is stealing disk bandwidth while it writes the next autosave. CPU sets (`SetProcessDefaultCpuSetMasks`) are preferred over hard affinity because the scheduler may still borrow the core when the game is idle. On Linux the equivalents are `SCHED_IDLE`, `ionice -c3`, and a cgroup `memory.max`; the Proton/Steam-Deck case gets the same policy with lower numbers. **Never set the game's own priority or affinity.** It is tempting ("raise Stellaris to High") and it is the kind of thing that generates support tickets about audio crackle and launcher weirdness.

**Sensing.** psutil gives everything the governor needs on both OSes. NVML via `nvidia-ml-py` gives GPU utilisation and memory ([nvidia-ml-py](https://pythonhosted.org/nvidia-ml-py)) — **VERIFIED that the package and calls exist**, but I propose we **do not even import it in v0–v2**, because we are not going to use the GPU and a sensing dependency that only works on one vendor is a support cost with no consumer.

## 4. GPU versus multi-core CPU versus a second machine

**The GPU: a strong "no" for v0/v1, a conditional "maybe" for M4 offline.** Reasons in cost-of-ownership order.

1. *There is no parallel workload online.* A 3×5 Bayes update and a table lookup have no batch dimension. Save parsing is a serial tokenizer over a text (or binary) blob; it is branchy, memory-bound and not GPU-shaped. GPU kernels need thousands of independent lanes; we have one save.
2. *Dependency weight.* `cupy-cuda12x` is a ~99 MB Windows wheel and "you need to install CUDA Toolkit 12.x locally to use these packages" (or bundle the toolkit via the `[ctk]` extra, which is gigabytes) ([PyPI cupy-cuda12x](https://pypi.org/project/cupy-cuda12x/)). **VERIFIED.** NVIDIA-only. For a mod whose users install via the Steam Workshop and a small companion installer, this is disqualifying on its own.
3. *The vendor-neutral option is in maintenance mode.* ONNX Runtime's DirectML EP runs on any DX12 card (NVIDIA Kepler+, AMD GCN+, Intel Haswell+ iGPUs) but the page states DirectML is "in sustained engineering" with new development moved to WinML, requires sequential execution and disabled memory patterns, and performs poorly with dynamic shapes ([ORT DirectML EP](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)). **VERIFIED.** Building on it means building on a frozen API. It also only helps if we have an ONNX graph to run, and we do not: the model is a table.
4. *VRAM contention with a modded game* (§1, HYPOTHESIS). A second GPU context on a 6 GB card can cause the one symptom players will blame us for even when our CPU footprint is zero.
5. *Testability.* Every GPU path is a second code path, a second CI leg we cannot afford (the report already cut the Windows CI leg), and a class of bug reports ("works on my 4070, crashes on my RX 580") that a solo maintainer cannot reproduce.

Where a GPU could legitimately earn its place, all offline: (a) M4's paired-run analysis, if we end up fitting likelihood tables or a small per-archetype classifier across hundreds of labelled campaigns — still probably seconds on CPU; (b) a future simulator for template parameter search (CMA-ES over θ), where the simulator itself, not our code, is the heavy part — and that simulator is Stellaris in `observe` mode, which is a CPU process; (c) local LLM inference for the dev-loop "commentator" or condition D at M4, where llama.cpp with Vulkan/CUDA offload is a real win. Note that (c) is the only GPU use with a genuine payoff, and it is explicitly post-M4 and off the decision path.

**Multi-core CPU: yes, but only for the parse worker and only under the governor.** The win is isolation, not throughput. Running the parse in a separate process on a cordoned core at IDLE priority means the engine never stalls and the game never notices. Parallelising inside the parse (jomini can parse sections independently; the dashboard's Rust parser could split by top-level key) is a legitimate M2–M3 optimisation that turns 3 s into ~1 s on four E-cores during PAUSED mode. It is not worth doing before the governor exists.

**A second machine: the best use of real money and the only honest answer to "where does the heavy compute go".** A used mini-PC or the laptop that is already on the desk, running `sae-bench` (the M4 harness in `observe` mode at `game_speed 4`) and the Rerun viewer, costs nothing in the player's frame budget. The protocol is already designed for it: the bus is JSONL over a directory; Rerun supports `--serve` with a browser or native viewer elsewhere; saves are files. The one piece to add is a `sae-sync` that ships bus deltas and autosaves over the LAN (rsync or a 50-line HTTP push), so the gaming PC does **no** analysis beyond the online loop. For the solo developer this is also the test machine: it runs the benchmark harness overnight while the main PC is used for playing and development.

## 5. Idle and pause-time opportunistic compute

This is the resource the plans ignored and it is free. In PAUSED mode the game's main thread is nearly idle (rendering only), so our worker can take 4–6 cores without the player noticing, provided the work is **pre-emptible within 200 ms and checkpointed**. Candidate jobs, in priority order:

1. **Full save parse** for the latest autosave (if PLAYING-mode parse was deferred or killed).
2. **Reconciliation** of log-derived features against the save-derived ground truth and emission of `health` events (parse drift, missing acks).
3. **Telemetry compaction**: roll the per-tick bus into the per-month summary the viewer actually reads; delete raw god-view streams older than N months unless debug is on.
4. **Replay analysis** of the current campaign up to now ("when did the belief flip, what was the top evidence"), written as pre-computed Rerun blueprints so a later viewer connection is instant.
5. *Never* anything that writes `sae_in.ini` — intents are decided by the monthly loop, not by background jobs, so that pausing cannot change what the AI does.

The trigger is the governor seeing `game_paused` for > 3 s (debounced, because players tap space). On unpause, SIGSTOP/suspend the worker immediately (Windows: `NtSuspendProcess` via psutil's `Process.suspend()`), and resume at the next pause; kill only if memory pressure appears. IDLE mode (game closed, or minimised for > 10 min) widens the budget to the whole machine minus two cores and is where the overnight jobs run.

## 6. Offline versus online split, hard budgets and acceptance tests

**Online (while the game runs, on the game PC):** log tail, belief update, template selection, intent compile, ack tracking, one bounded save parse per autosave, governor. That is all.

**Offline (IDLE mode, or the second machine):** M4 paired-run harness; likelihood-table fitting and archetype calibration from labelled campaigns; template θ tuning; replay analysis; viewer blueprints; any LLM work; any GPU work. The rule of thumb: **if it would ever make sense to run on a GPU, it does not run while Stellaris runs.**

Hard budgets for the online tier, to be enforced by the governor and tested in CI with a synthetic load:

| Resource | Budget (PLAYING) | Budget (PAUSED) | Enforcement |
|---|---|---|---|
| Engine RSS | ≤ 150 MB | same | Rust binary; no pandas/duckdb loaded in the engine process |
| Parse worker RSS | ≤ 1.0 GB, exits after each parse | ≤ 2.0 GB | Job object memory limit (Windows) / cgroup `memory.max`; kill on breach |
| Engine CPU | ≤ 2% of one core averaged over 60 s | same | event-driven; no polling faster than 2 s |
| Parse worker CPU | 1 core at IDLE + EcoQoS, start ≥ 8 s after autosave close | up to N−2 cores | CPU set + priority class |
| Disk | bus ≤ 100 MB per 200-year campaign; god-view ≤ 300 MB, pruned | — | compaction job in PAUSED |
| Game impact | s/game-month with SAE ≤ 1.03 × without, median over 24 months, late game | — | **acceptance test A1** |
| GPU / VRAM | 0 bytes allocated by any SAE process | 0 | no CUDA/DirectML/Vulkan dependency in the manifest |

Acceptance tests, each on a 30-mod late-game (year 2400+) save from a real playset:

- **A1 Invisibility.** Run 24 months at speed 4 with and without the companion, three repetitions each; median s/month ratio ≤ 1.03, p95 frame time on the galaxy map not worse by more than 5%. Fail → the parse delay, priority or thread budget is wrong.
- **A2 Pressure response.** Simulate memory pressure (allocate until available < 1.5 GB); governor must enter BACKOFF within 10 s, kill the worker, and the engine must keep tailing the log and acking.
- **A3 Pause harvest.** With the game paused for 60 s, the deferred parse and compaction complete and the worker is suspended within 200 ms of unpause.
- **A4 Mod-script cost (from the prior report, now a hard number).** The `sae.1`/`sae.2` monthly events add ≤ 15 ms to the monthly tick with 5 managed countries, measured via the game's own timing in `game.log` or by A/B s/month. This is the budget that matters most and it is inside the game, where no governor can help.
- **A5 Low-end.** The whole thing runs on a 4-core/8 GB laptop with the parse worker degraded to one E-core and the save-interval doubled; the engine must still produce a decision each month.

## 7. What to cut

- **Any GPU dependency, sensing included**, from v0–v2. Revisit at M4 only for offline LLM or large-scale calibration.
- **DuckDB in the engine process.** Keep it, but only in the offline/analysis tool, never resident beside the game. The engine writes JSONL and nothing else.
- **A resident Rerun viewer on the game PC.** Default is `--serve`, viewer remote or on demand; the report already said this and I would make it non-negotiable.
- **Polling anything faster than 2 s**, including the log. Use file-change notifications with a 2 s fallback.
- **Autosave-driven parse on every save when s/month is already high.** The governor should widen the effective parse interval (parse every second or third autosave) before it ever touches thread count; the detector is designed around a 24-month commitment and does not need monthly ground truth.
- **In-process parallel parsing before M3.** The sequential 3 s target is fine inside the budgets above.

## 8. Roadmap deltas M0–M4

- **M0 (weekend):** add one measurement, not one test: record wall-seconds per game month with and without the minimal `sae` mod at year ~2350 of your own modded save, and record `psutil` CPU/RAM of Stellaris itself during the monthly pulse. This gives the baseline for A1/A4 and tells us how much headroom actually exists on the machine that matters.
- **M1 (Puppet):** parse worker is a separate process from day one, with priority class, EcoQoS and a job-object memory cap. Governor is a stub that only implements PLAYING and BACKOFF.
- **M2 (Detector):** add PAUSED mode and the pause-time job queue (parse, reconcile, compaction). Add pause-fraction telemetry so we learn how much free wall-clock a real session has.
- **M3 (First playable):** acceptance tests A1–A5 pass on the developer's own 30-mod save; the governor's thresholds become user settings with sane defaults ("Low impact / Balanced / Dev"). Ship the Rerun `--serve` default.
- **M4 (Evidence):** the benchmark harness is written to run on a second machine or in IDLE mode only; it uses the Stellaris `observe` mode at max speed, which is CPU-bound and benefits from fast single-thread performance rather than core count or GPU. Only here do we evaluate whether any analysis step (calibration over hundreds of runs, LLM condition D) merits a GPU, and the GPU stays on the benchmark machine.

## 9. Risks

1. **The mod's own script is the dominant cost and no governor can touch it.** If A4 fails, the only fixes are inside the mod: fewer managed countries, staggered emission across months, bucketed variables. Everything in this draft about the companion is secondary to that. Likelihood medium, impact high.
2. **EcoQoS/priority hints are hints.** On a saturated monthly pulse Windows may still schedule our IDLE worker on a P-core for a quantum; the 8 s start delay is the real protection. Likelihood low, impact low.
3. **Memory, not CPU, bites first.** A late-game modded save plus a careless DOM parse can hit 3–4 GB; the job-object cap turns that into a killed worker and a BACKOFF, which is the intended failure mode, but it also means the detector goes blind. Mitigation: streaming/section-wise parse by M2. Likelihood medium, impact medium.
4. **Pause-time harvesting feels like "the mod is doing something while I think".** Fan noise on a laptop when paused will be noticed. Mitigation: EcoQoS on the pause worker too, and a user setting. Likelihood medium, impact low.
5. **Scope creep toward "we have a GPU, let's use it."** The strongest social risk for an open-source project: contributors will propose CuPy or Torch. The manifest rule "zero GPU bytes online" and the acceptance test A1 give maintainers an objective reason to decline until M4 produces a workload.
6. **Second-machine assumption excludes some users.** It is only required for development and benchmarking, never for play; the shipped companion must run on a single mid-range PC. Keep that line bright in the docs.

## 10. Summary for the user

You asked whether to offload parallel work to the GPU. The verified picture is that Stellaris is bottlenecked on one hot thread with batched parallel bursts on the monthly tick, leaves the GPU mostly idle, and that our engine's online work is a few microseconds of Bayes plus one bounded save parse per autosave — nothing GPU-shaped. GPU libraries on Windows are either NVIDIA-only and ~100 MB plus a CUDA toolkit (CuPy) or vendor-neutral but in maintenance mode with threading restrictions (ONNX Runtime DirectML), and a second GPU context competes for VRAM with a modded game. So: zero GPU bytes online through M3. Spend the machine's real spare resources instead — idle cores via a separate IDLE/EcoQoS parse worker under a four-mode resource governor, and idle wall-clock via pause-time and overnight jobs — and put everything heavy (benchmarks, calibration, replay analysis, any LLM) on a second machine or in IDLE mode. Enforce it with hard budgets and five acceptance tests, the most important of which measures the mod's own script cost inside the game, because that is the one cost no amount of clever scheduling outside the game can fix.

### Sources
- Paradox Dev Diary #410, 4.3 performance comparisons: https://admin-forum.paradoxplaza.com/forum/developer-diary/stellaris-dev-diary-410-4-3-performance-comparisons.1899301/page-6
- Steam discussion on Stellaris CPU/GPU utilisation: https://steamcommunity.com/app/281990/discussions/0/1741102632995174015
- Microsoft Quality of Service levels (EcoQoS, ProcessPowerThrottling): https://github.com/MicrosoftDocs/win32/blob/docs/desktop-src/ProcThread/quality-of-service.md
- Microsoft "Introducing EcoQoS": https://devblogs.microsoft.com/performance-diagnostics/introducing-ecoqos/
- ONNX Runtime DirectML execution provider: https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html
- CuPy cupy-cuda12x on PyPI: https://pypi.org/project/cupy-cuda12x/
- nvidia-ml-py (NVML bindings): https://pythonhosted.org/nvidia-ml-py
