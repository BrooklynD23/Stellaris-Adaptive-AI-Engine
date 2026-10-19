# Draft O: Resource architecture for SAE on resource-starved machines

Author: Opus 5.5 (independent draft). Date: 2026-10-18. Builds on `reports/Adaptive Stellaris AI engine.md`. That report's decisions are kept unless this draft argues against them. The labels are **VERIFIED (url)** for a claim checked this pass, **REPORT** for a claim carried over from the prior report, and **HYPOTHESIS** for anything that M0 or M1 must measure.

## 0. The short answer to "can we use the GPU?"

**Not for v0 to M4, and that is not a compromise.** SAE's runtime math is a forgetful Bayes filter over 3 archetypes and 4–5 features, run once per game month or less often (REPORT). That is microseconds of work, where one GPU kernel launch plus the host-to-device copy costs about 10–50 µs. Creating a CUDA context alone takes hundreds of MB of host RAM and VRAM (HYPOTHESIS, an order of magnitude to measure). On a machine already short of RAM, importing a GPU framework is the most expensive thing the engine could do. The machine does have real headroom, but it is elsewhere:

1. **Spare cores.** Stellaris's simulation is bound by its main thread.
2. **Idle wall time** while the game is paused or closed.
3. **A second machine.** It is the only place where *parallel game instances*, the true bottleneck for M4 benchmarking, can run.

The GPU becomes worth using only if the project later builds a fast **surrogate simulator** and wants thousands of batched rollouts (§3). This draft designs the seam for that now and defers the work.

The rule that governs everything below: **the engine is a guest on the player's machine, and the host is the Stellaris main thread.** Every component is judged by whether it steals time, cache, memory bandwidth or RAM from that thread.

## 1. Resource profile of a 30+ mod late-game session

| Resource | Typical state in a late-game, 30+ mod session | Headroom for SAE |
|---|---|---|
| **Main sim thread (1 core)** | Saturated. Players report one core near 100% while the others stay lower, and late-game lag that tracks pop count and galaxy size even on an i9-13900K ([Steam thread](https://steamcommunity.com/app/281990/discussions/0/3825286438457734441), community reports, **VERIFIED as reports**, not as engine fact). Paradox's 4.3 dev diary describes threaded economic batches and work on "monthly stutter". So some work is parallel, but the monthly tick still serialises on dependencies ([Dev Diary #410, 4.3 performance](https://admin-forum.paradoxplaza.com/forum/developer-diary/stellaris-dev-diary-410-4-3-performance-comparisons.1899301/page-6), **VERIFIED**). | **None.** Our **in-game script runs on this thread** (REPORT). It is the scarcest resource and gets the strictest budget. |
| **Other cores** | Partly idle between monthly bursts. Busy *during* the monthly tick (threaded batches). | **Real, but bursty.** Use them *between* ticks and at low priority. Never compete with the tick burst. |
| **Shared L3 / memory bandwidth** | The main thread is latency- and cache-sensitive (HYPOTHESIS: it walks large pop and modifier graphs). | **Small.** Large streaming jobs, such as save parse and big NumPy arrays, can evict the game's cache even on "idle" cores. Keep working sets small, run bursts *between* ticks. |
| **RAM** | Heavy. A big playset plus a late-game save can push the game to many GB (HYPOTHESIS, measure in M0). Users often have 16 GB. | **Tight.** The danger is not OOM but **paging the game's working set**, which turns into stutter. SAE gets a hard cap. |
| **Disk I/O** | Autosave write bursts. Mod load at start. | **Moderate.** Do not read a save while it is being written (wait until mtime and size are stable). Read with background I/O priority. |
| **GPU compute** | Mostly idle on a strategy map. The original poster in the Steam thread, with an RTX 3080 Ti, reported low resource use (**VERIFIED as an anecdote**). No systematic data. | **Real but awkward.** VRAM is shared with the game's renderer. Vendor and driver are unknown on user machines. Little SAE work fits it (§3). |
| **Wall time while paused or closed** | Players pause often. Dev and benchmark machines sit idle overnight. | **Large and free.** Good for anytime work (§4). |
| **Second machine** | Many devs have a laptop, an old desktop or a home server. | **The largest headroom.** It is the only way to scale M4's paired runs (§5). |

Conclusion: on a starved box, **budget the guest, don't hunt for compute.** Headroom exists in idle cores between ticks, in pause and closed-game time, and on other machines. It does not exist on the main thread or in RAM.

## 2. Architecture

### 2.1 Process topology

```
                         ┌──────────── Stellaris (normal priority, untouched) ─────────────┐
                         │ sae_bridge mod: staggered emit, cadence var sae_emit_period,    │
                         │ quiet flag sae_quiet (set via run file) — no galaxy-wide loops  │
                         └──────┬──────────────────┬──────────────────────────▲────────────┘
                     game.log (tail)       autosave .sav (mtime-stable)     sae_in.ini (atomic)
                                │                  │                          │
┌───────────────────────────────▼──────────────────▼──────────────────────────┴───────────┐
│ sae-daemon (Python, BELOW_NORMAL + EcoQoS, Job: ≤250 MB, CPU hard cap 5%)                 │
│   LiveAdapter · Engine core (Bayes, table, commitment) · Intent compiler                   │
│   Resource Governor  ── senses spm, CPU/RAM/VRAM, pause; sets ladder level L0..L5          │
│   Work Scheduler     ── priority queue: critical (parse latest save) / opportunistic       │
│   writes bus.jsonl (append, buffered)                                                      │
└──────┬───────────────────────────┬───────────────────────────────┬────────────────────────┘
       │ spawn per job, stdin/stdout│ spawn on demand (optional)     │ file tail (bus.jsonl)
┌──────▼───────────────────┐ ┌─────▼──────────────────────────┐ ┌──▼─────────────────────────┐
│ sae-parse (Rust exe,     │ │ sae-compute (Python/Rust,       │ │ sae-telemetry (optional)   │
│ jomini, ≤2 rayon threads,│ │ off by default; CPU first,      │ │ bus → Rerun: .rrd file or  │
│ BACKGROUND mode, Job ≤1GB│ │ GPU optional; idle-only jobs)   │ │ gRPC to remote viewer      │
│ exits after each save)   │ └──────────────┬──────────────────┘ └──┬─────────────────────────┘
└──────────────────────────┘                │ same job protocol      │ rerun+http://host:9876/proxy
                                            ▼                        ▼
                                ┌──────── LAN worker (optional) ───────────────┐
                                │ sae-worker: parse / compute / benchmark farm │
                                │ Rerun viewer · DuckDB analysis               │
                                └───────────────────────────────────────────────┘
```

**Decisions and their reasons:**

- **The daemon stays Python and stays small.** Its workload is trivial (REPORT). The risk is *import bloat*, not compute. The daemon imports no torch, jax, rerun or polars. NumPy is allowed. Pydantic stays at the bus boundary.
- **The parse worker is a separate Rust executable, not a PyO3 extension inside the daemon.** This deliberately departs from the earlier "Rust via PyO3" line, for this component only. A save parse is the one memory spike in the system (decompress, then tokenize, then extract). In a child process that exits after each job, the OS reclaims every byte, allocator fragmentation never builds up, and a Job Object can cap the spike *without* risking the daemon. PyO3 remains the right choice for small hot kernels inside a process that lives a long time, if any appear. The worker deserialises **only the needed sections** into typed structs and skips the rest. jomini supports serde-style deserialisation of Paradox formats ([GitHub rakaly/jomini](https://github.com/rakaly/jomini), REPORT). Whether skipping keeps peak RSS down on 4.4 saves is a **HYPOTHESIS** for M0-11.
- **Telemetry is a separate consumer of the bus file, not a sink inside the daemon.** If the viewer stalls, the engine is unaffected. Rerun's viewer defaults to using **up to 75% of system RAM** ([Rerun: limit RAM](https://rerun.io/docs/howto/limit-ram), **VERIFIED**). On a starved box that default is hostile. Policy: local viewer **off** by default. When it is on, launch it with `--memory-limit 512MB`. The preferred mode is to stream to a viewer on another machine via `--connect rerun+http://HOST:9876/proxy` (default gRPC port 9876; `--server-memory-limit` defaults to 1 GiB for the buffering proxy) ([Rerun CLI](https://docs.rerun.io/dev/reference/cli/), **VERIFIED**), or to write `.rrd` files for later viewing.
- **The compute worker does not exist until there is a job for it** (§3). Its interface, a job protocol, is defined now so that local CPU, local GPU and LAN execution are interchangeable.

### 2.2 IPC

- **Game ↔ daemon:** unchanged from the report. Log tail, autosave, `run sae_in.ini`.
- **Daemon ↔ workers:** one **job protocol**, length-prefixed msgpack over stdin/stdout for local children and the same frames over TCP for LAN workers. Each job carries `{job_id, kind, priority, deadline_wall, inputs (path or bytes), budget}`, and each result carries `{job_id, status, outputs, rss_peak, cpu_s, wall_s}`. Every job reports its own cost, so the Governor learns cost models from real data.
- **Daemon → telemetry:** `bus.jsonl` only. There is no second channel to keep consistent, and the bus stays the single source of truth (REPORT).

### 2.3 The Resource Governor

The Governor is a module inside the daemon, not a separate process, so the sensor itself costs almost nothing.

**Sensing, at 1 Hz at most, and only the cheap probes:**

| Signal | How | Notes |
|---|---|---|
| **Game seconds per month (spm)** | Wall-clock delta between consecutive monthly `SAE|v1|` lines, with save mtime as a fallback | **The primary health metric.** It confounds game speed setting with lag, so the Governor uses *ratios against its own rolling baseline*, never absolute values. |
| **Monthly stutter** | Spread of the gap between the first and last SAE lines within a month, and spm variance | A proxy. Frame-time capture with PresentMon belongs in the acceptance tests only, not at runtime. |
| **Pause** | No monthly line for more than 3× the rolling spm median, *and* the Stellaris process's CPU time rate drops (psutil `cpu_times` delta) | HYPOTHESIS: CPU use drops markedly when paused. Verify in M1. |
| **System RAM** | psutil `virtual_memory().available` and the Stellaris process's page-fault rate | Hard faults on the game process are the real alarm. |
| **Own cost** | psutil on the daemon and its children. Each job reports `rss_peak` and `cpu_s` | Feeds the budget ledger in the `health` bus event. |
| **VRAM** (only if the compute worker is GPU-enabled) | NVML (`pynvml`) on NVIDIA. Skip on other vendors (HYPOTHESIS: no cheap vendor-neutral VRAM query from Python) | Without a reading, the GPU counts as **unavailable**. |
| **Self A/B** | Every N game years, the Governor drops to L4 for 3 months and compares spm against neighbouring months | A cheap in-situ estimate of SAE's own overhead. Logged, never acted on alone. |

**Back-off ladder** (with hysteresis: escalate after 2 bad months, de-escalate after 6 good months):

| Level | Trigger, examples | Behaviour |
|---|---|---|
| **L0 Normal** | spm within 3% of baseline, RAM available > 2 GB | Parse every autosave, opportunistic queue enabled while paused |
| **L1 Trim** | spm +3–5%, or RAM < 2 GB | Opportunistic queue off. Telemetry streams scalars only. |
| **L2 Sparse** | spm +5–10%, or hard faults on the game | Parse every other autosave. Parse worker limited to 1 thread. |
| **L3 Decision-only** | spm +10%, or RAM < 1 GB | Parse only at commitment boundaries, plus 3 months before them. **Acceptable because decisions are 24-month commitments** (REPORT): observations sampled every 6–12 months lose almost nothing. |
| **L4 Quiet** | User toggle, or a sustained L3 breach | No parsing, log tail only. On the next run-file hotkey, set `sae_emit_period=12` in the mod. |
| **L5 Suspended** | RAM critical, or the user sends `pause` | Engine idle. The mod keeps the last applied knobs, which stay within the safety floor. |

The ladder is safe because **correctness never depends on throughput**. Lower levels only make adaptation coarser in time. That property lets SAE back off without any risk.

**Priorities, affinity, caps (Windows primary, Linux equivalents):**

- **Daemon:** `BELOW_NORMAL_PRIORITY_CLASS` ([SetPriorityClass](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass), **VERIFIED**). On Windows 11, also **EcoQoS** through `SetProcessInformation(ProcessPowerThrottling, EXECUTION_SPEED)`. EcoQoS "always selects most efficient CPU frequency and schedules to efficient cores" ([MS QoS doc](https://github.com/MicrosoftDocs/win32/blob/docs/desktop-src/ProcThread/quality-of-service.md), **VERIFIED**). On hybrid Intel CPUs this keeps SAE on E-cores and away from the P-core running the game's main thread. Linux: `nice 10`, `SCHED_IDLE` for workers.
- **Parse and compute workers:** start, then call `SetPriorityClass(PROCESS_MODE_BACKGROUND_BEGIN)`. Microsoft documents that CPU priority alone is insufficient for background work because "even an idle CPU priority process can easily interfere … when it uses the disk and memory", and background mode lowers resource scheduling priorities beyond CPU (same page, **VERIFIED**). This matters directly for save reads during autosave bursts. Linux: `ionice -c3`.
- **Avoid `IDLE_PRIORITY_CLASS` for the daemon.** It is preempted by any normal-priority thread (**VERIFIED**, same page). The game's threaded batches could starve log tailing during exactly the monthly tick we want to time. Background mode still guarantees a thread "will never be starved" (**VERIFIED**).
- **Affinity:** never touch the game's affinity. For our processes, prefer **soft CPU sets** (`SetProcessDefaultCpuSets`) on the last physical cores or E-cores over hard affinity masks. HYPOTHESIS: soft sets degrade more gracefully when the game's thread pool needs those cores.
- **Job Objects:** the daemon and each worker run in a Job with a **process memory limit** and **CPU rate control**. Hard cap mode means that "once the job uses its cycles for the scheduling interval, none of its threads run until the next interval". The rate is given as percent × 100, and it is available since Windows 8 ([JOBOBJECT_CPU_RATE_CONTROL_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information), **VERIFIED**). Use `JOB_OBJECT_CPU_RATE_CONTROL_NOTIFY` so that budget breaches turn into bus `health` events instead of silent throttling. Linux: a cgroup v2 user slice (`systemd-run --user --scope -p MemoryMax= -p CPUQuota=`).

**In-game cost control.** This is where the real budget sits, because only the mod touches the main thread:

- Two variables, `sae_emit_period` (months between emits per country) and `sae_quiet`, are read by the monthly event. Per-country emission is **staggered**, with country *i* emitting when `month mod period == i mod period`. Work per tick stays flat instead of spiking every N months.
- The cost of the mod's monthly events is measured offline in M0 (§6). HYPOTHESIS: Stellaris may expose a script or event profiler through debug console commands. Check this in M0. If none exists, measure the difference in seconds per year.

## 3. GPU and parallelism: what is worth offloading

| Workload | Size | Where it runs | Why |
|---|---|---|---|
| v0 belief update, table lookup, intent compile | about 10² flops per month | **Daemon, plain Python/NumPy** | GPU launch and transfer overhead exceeds the work by 3–4 orders of magnitude. Importing a framework costs more RAM than the engine's whole budget. |
| Save parse | 10–100s of MB of text or binary, once per autosave (HYPOTHESIS) | **Rust worker, CPU, ≤ 2 threads** | Parsing is branchy and sequential tokenising, with no GPU fit. rayon over independent sections at most. |
| Particle or multi-hypothesis belief (later: per-player profile, 10³–10⁵ hypotheses × 5 features) | ≤ 10⁶ flops | **CPU NumPy, or Numba if profiled hot** | Still sub-millisecond on CPU. GPU only above roughly 10⁷–10⁸ elements (HYPOTHESIS, a rule of thumb). |
| **Batched forward simulation / scenario rollouts** on a *surrogate* model of Stellaris macro dynamics | 10³–10⁵ rollouts × 100s of steps | **Compute worker. GPU optional, CPU vectorised fallback.** | This is the one shape where a GPU pays: wide, uniform, batched. It **requires a surrogate that does not exist yet**, and the real game cannot be stepped faster (REPORT). |
| Offline CMA-ES or population search over template parameters | Population 10–50, each evaluation a game run | **Benchmark farm (other machines)** | The cost is *game time*, not math. pycma's own overhead is negligible. Parallelism means more Stellaris instances, not GPU work. |
| Offline training of a learned likelihood or surrogate | Variable | **Dev or LAN machine with PyTorch** | Never on the player's box during play. Ship the result as ONNX. |
| Local LLM (only if M4 condition D ever wins) | GBs of weights | **LAN machine preferred.** Local only when the game is closed, or CPU-only small models with a hard thread cap. | VRAM contention with the renderer, plus RAM pressure from weights (HYPOTHESIS: a 4B Q4 model needs about 3 GB). llama.cpp's partial layer offload (`-ngl`) lets VRAM use be set explicitly ([llama.cpp](https://github.com/ggml-org/llama.cpp), REPORT for grammars). Never resident during play on a starved box. |

**Framework ladder, vendor-neutral and Windows-safe:**

1. **NumPy**, the default everywhere.
2. **Numba** (CPU `@njit`, `parallel=True` with a capped `NUMBA_NUM_THREADS`) for profiled hot loops in Python workers.
3. **Rust + rayon**, with an *explicit* `ThreadPoolBuilder::num_threads(n)` set by the Governor and never the default of all cores, for the parse worker and any surrogate CPU kernel.
4. **ONNX Runtime** to run any learned model. CPU execution provider by default. The optional GPU path is vendor-neutral: **DirectML is now in "sustained engineering", and new feature work has moved to WinML** ([ORT DirectML EP](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html), **VERIFIED**). DirectML needs a DX12 device (Kepler, GCN1 or Haswell iGPU and newer, **VERIFIED**). Plan to target the WinML/ORT EP-selection path and treat DirectML as the fallback.
5. **PyTorch (CUDA)** on dev and training machines only.
6. **JAX: not on Windows player machines.** Native Windows has CPU support only, marked experimental, and NVIDIA GPU is "no" natively and experimental under WSL2 ([JAX install](https://docs.jax.dev/en/latest/installation.html), **VERIFIED**). Fine on a Linux LAN worker.
7. **CuPy:** NVIDIA-only. Use only if a Linux LAN worker with an NVIDIA card wants drop-in NumPy, never as a user dependency.

**When the GPU must be optional:** always. Conditions for a GPU job: the compute worker is enabled, a device is present, VRAM free exceeds the job's declared need plus 1 GB of margin (NVML) or the game is **closed**, and the Governor is at L0. Every GPU kernel has a CPU twin and a parity test. On a VRAM shortfall, the job falls back to CPU, or to LAN, or it is skipped.

## 4. Idle-time exploitation

- **Pause detection** as in §2.3, plus a "game closed" state (no Stellaris process).
- **The opportunistic queue** holds *anytime* jobs. Each one checkpoints at least every 100 ms of CPU time and can be preempted within one Governor poll after unpause. Candidates:
  - re-scoring the belief history with alternative ρ or clip L (calibration diagnostics),
  - precomputing the *next* decision's what-if table (posterior sensitivity: "how much evidence would flip us"),
  - rendering `.rrd` files from the bus for later viewing,
  - compressing old runs,
  - re-running the parser on archived saves when the schema changes,
  - with the game closed: calibration fits (M2), replay regression suites, surrogate training (post-M4).
- **The invariant:** the opportunistic queue **never** produces an input the decision path depends on in time. A decision uses the opportunistic result only if it is already complete and valid, and otherwise uses the baseline computation. This is a tested invariant: run a replay with the queue disabled and require identical intents.
- **Paused does not mean free.** The game still renders and holds its RAM. The RAM caps stay in force during pause. Only CPU-time budgets relax: up to 2 cores in background mode, and only with RAM available > 2 GB.

## 5. Distributed option: a second machine on the LAN

What goes off-box, in order of value:

1. **The M4 benchmark farm.** Paired `observe` runs are the project's real compute sink. Each Stellaris instance is main-thread bound (§1), so throughput scales with *machines*, at about one instance per machine. HYPOTHESIS: multiple instances per box are unreliable and RAM-prohibitive with 30+ mods. Each farm node runs a `sae-bench` agent that receives `{start_save, playset hash, mod hash, engine config, seed, condition}` and returns bus files. This uses the same job protocol.
2. **The viewer and analysis.** Rerun over gRPC (§2.1, **VERIFIED** flags) and DuckDB over synced bus files. This takes the largest optional RAM user off the player's box.
3. **Save parse (optional).** Push the compressed `.sav` (HYPOTHESIS: about 10–60 MB late-game) over 1 GbE, which takes under 1 s. The worker returns features of about 1 KB. Worth it only when the Governor sits at L2 or above locally. The local worker stays the fallback.
4. **Training, surrogate rollouts, LLM.** These are where GPU work would happen at all.

What stays local: the **daemon and the intent path**. They are tiny, and adding a network hop to the only path that writes into the game buys nothing and adds failure modes.

**Protocol hygiene:** discovery is static (a host list in `local.toml`). Authentication is a shared token with TLS optional. Every job has a deadline. A missed deadline falls back to local execution or a skip, never a block. Workers are stateless, and the daemon is the authority. Results carry `rss_peak, cpu_s, wall_s` so that remote costs enter the same ledger.

## 6. Budgets and acceptance tests

**Budgets.** Defaults, configurable, enforced by Job Objects and cgroups where possible:

| Component | CPU | RAM | Other |
|---|---|---|---|
| **In-game mod script** | ≤ **1%** added seconds per game year on the reference save | n/a (save growth ≤ 1%) | No new stutter. Stagger per-country emits. |
| **sae-daemon** | ≤ **2% of one core** average, ≤ 10% p99 per second; hard cap 5% | ≤ **200 MB** RSS steady, Job limit 250 MB | No GPU, no heavy imports |
| **sae-parse** (per autosave) | ≤ 2 threads in background mode; ≤ 5 s wall at L0 | Peak ≤ **1 GB** (Job limit); 0 after exit | Starts only after the save's mtime and size are stable for 2 s |
| **sae-telemetry** | ≤ 2% of one core | ≤ 150 MB | Off by default for players |
| **Local Rerun viewer** | n/a | `--memory-limit 512MB` | Dev only. Prefer remote. |
| **sae-compute** | Idle-only; ≤ 2 cores | ≤ 1 GB; VRAM need + 1 GB margin | Off by default |
| **Whole system** | **Game slowdown ≤ 2% mean, ≤ 3% p95 seconds per year** vs the same playset without SAE | No hard-fault increase on the game process | |

**Acceptance tests**, a release gate from M3 onward:

- **AT-1 Overhead A/B.** Use a reference late-game save from the user's **actual 30+ mod playset** (year ≥ 2350). Run `observe` at `game_speed` fastest (REPORT: verified console commands) for 10 game years per run, under four conditions: (a) playset without SAE, (b) plus the SAE mod and no engine, (c) plus the engine at L0, (d) (c) plus local telemetry. Do 5 runs per condition, interleaved ABCD order, same machine, reboot-fresh. Report seconds per year with bootstrap confidence intervals. Pass if (c) vs (a) is ≤ 2% mean and the upper CI bound is ≤ 3%. Determinism is unknown (REPORT), so the comparison is between distributions.
- **AT-2 Stutter.** During AT-1, capture frame times with PresentMon. Pass if p99 frame time and the duration of monthly-tick hitches do not regress beyond the noise band of (a).
- **AT-3 Starved machine.** Repeat (a) and (c) with a balloon process holding RAM so that only about 1.5 GB is available. Pass if the game's hard-fault rate under (c) is within noise of (a) and the Governor reaches L2 or L3 by itself.
- **AT-4 Budget enforcement.** Synthetic: feed the parse worker a pathological 2× size save. Pass if the Job limit kills the worker, the daemon logs `health:budget_breach`, falls back to log-only for that month, and keeps running.
- **AT-5 Ladder correctness.** Replay a recorded campaign through the ReplayAdapter at forced L0 and forced L3. Pass if strategy switches happen in the same commitment windows in at least 90% of cases (HYPOTHESIS threshold). This shows that back-off coarsens timing without changing decisions.
- **AT-6 Opportunistic independence.** Replay with the opportunistic queue on and off. Intents must be identical.
- **AT-7 GPU optionality** (only once compute exists). The same job runs on CPU, on GPU and on LAN, with numeric parity within tolerance. With no GPU present there must be no import errors.

## 7. Roadmap deltas

- **M0 (+½ day).** Add **M0-12 baseline cost**: AT-1 conditions (a) vs (b) on the real playset, using only the HELLO-emit mod. Record RAM headroom and the game's RSS. Extend **M0-11** to record parse **peak RSS** as well as time, and to test section-skipping deserialisation. Probe for a script or event profiler. These numbers set every budget above. If (b) already costs more than 1%, redesign emission before writing any engine code.
- **M1.** Ship `sae-parse` as a **separate Rust executable** with the job protocol. Add Governor v1: spm sensing, priority, background mode, EcoQoS, Job caps, `health` events. Turn Rerun off by default and document remote viewing. Add the `sae_emit_period` and `sae_quiet` variables to the mod.
- **M2.** Run the calibration fits as opportunistic or game-closed jobs. Add AT-4, AT-5 and AT-6 to the replay test suite, which needs no game.
- **M3.** Add the full back-off ladder and the in-situ self-A/B. **AT-1, AT-2 and AT-3 become release gates.** The mod page states SAE's measured overhead.
- **M4.** Add the `sae-bench` farm agent on a second machine (or several). Paired runs scale by machine. Remote Rerun and DuckDB run there.
- **M5 (new, conditional research track).** Build a **surrogate macro-simulator** fitted on M4 bus data. Only if it validates against held-out runs does the compute worker gain GPU and batched rollouts, and CMA-ES move from game time to surrogate time. This is the first point at which "use the GPU" has a real job.

## 8. Open hypotheses to settle first

1. How much of the monthly tick is parallel in 4.4 with a large playset, and how sensitive the main thread is to cache and bandwidth pressure from our bursts. Measure with AT-1 plus a deliberately cache-heavy synthetic worker.
2. The cost of the mod's monthly emit on the main thread, and whether a profiler exists.
3. Peak RSS of jomini-based extraction on a 4.4 late-game save.
4. Whether pause is reliably detectable from process CPU rate.
5. Whether multiple Stellaris instances per machine are viable for benchmarking. The default assumption is no.

## Sources checked this pass

- [Steam discussion: Stellaris late-game lag and core use](https://steamcommunity.com/app/281990/discussions/0/3825286438457734441)
- [Stellaris Dev Diary #410: 4.3 performance comparisons](https://admin-forum.paradoxplaza.com/forum/developer-diary/stellaris-dev-diary-410-4-3-performance-comparisons.1899301/page-6)
- [SetPriorityClass (background mode, idle, below normal)](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass)
- [JOBOBJECT_CPU_RATE_CONTROL_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information)
- [Windows Quality of Service / EcoQoS](https://github.com/MicrosoftDocs/win32/blob/docs/desktop-src/ProcThread/quality-of-service.md)
- [ONNX Runtime DirectML EP](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)
- [JAX installation matrix](https://docs.jax.dev/en/latest/installation.html)
- [Rerun: limit viewer RAM](https://rerun.io/docs/howto/limit-ram)
- [Rerun CLI reference](https://docs.rerun.io/dev/reference/cli/)
