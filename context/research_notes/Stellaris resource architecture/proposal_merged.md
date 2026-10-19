# SAE resource architecture: merged proposal

Author: Opus 5.5 (architect), merging Draft O (Opus 5.5), Draft F (Fable 5.1) and the two cross-reviews. Date: 2026-10-18. Builds on `reports/Adaptive Stellaris AI engine.md` (the "report"). Its decisions stand unless this proposal says otherwise.

Labels: **VERIFIED (url)** means one of the four source documents opened the page and checked the claim. **HYPOTHESIS** means M0 or M1 must measure it. **REPORT** means the claim is carried over from the context report and was not re-opened in this pass. When the two reviews still disagreed, the architect decided, and the reason is given in one line (marked **Decision**).

---

## 1. Summary

**GPU: no, not on the player's machine through M4. That is a design rule, not a gap.** The online workload is a forgetful Bayes update over 3 archetypes and 4–5 features, a table lookup, and a 2 KB `.ini` write, once per game month (REPORT). That is roughly 10² flops. A single kernel launch plus a host-to-device copy costs more than the whole computation. Creating a GPU context costs host RAM and VRAM that a modded game needs (HYPOTHESIS: hundreds of MB). Save parsing, the only real CPU cost, is a branchy serial tokenizer, which is not GPU-shaped. The vendor-neutral GPU path (ONNX Runtime DirectML) is in sustained engineering, and the NVIDIA path (CuPy) needs a ~100 MB wheel plus a CUDA toolkit (§5). The rule is enforced in the manifest: **zero GPU bytes allocated by any SAE process on the player's machine**, and no CUDA, DirectML or Vulkan dependency in the package. The GPU appears only in an **M5 parking-lot track**: a surrogate macro-simulator for batched rollouts. That track is gated on M4 data and runs on a second machine, never on the player's.

**The real headroom is elsewhere.** Spare cores exist *between* monthly ticks, not during them. Paused and closed-game wall time is large. A second machine is the only way to scale M4's paired benchmarks. **RAM, not CPU, is the binding constraint**, because paging the game's working set causes stutter that players will blame on us.

**The architecture in one paragraph.** The game is the host, and SAE is a guest. There are three processes. (1) A **lean Python daemon** with an import whitelist, ≤150 MB RSS, `BELOW_NORMAL` priority plus EcoQoS, and self-monitoring that only notifies, with no hard CPU cap. (2) A **Rust `sae-parse.exe`** that runs once per autosave, starts at least 8 s after the save is closed, puts itself into background mode, runs under a Job Object memory cap, writes JSON to stdout, and exits. (3) An optional **telemetry process** that streams the bus to a **remote** Rerun viewer. A **two-axis Resource Governor** (game state × health level) escalates on RAM and hard faults first, and on seconds-per-month (spm) only through a robust median test. In-game cost is controlled by `sae_emit_period` and `sae_quiet`. Pause-time work is opportunistic, can be killed at any point, and is never on the decision path. Heavy work (benchmarks, calibration, analysis, any LLM or GPU) runs with the game closed or on a second machine, which is fed by files and rsync. **In Ironman there is no live loop at all.** The companion only observes, and adaptation happens between sessions.

---

## 2. Resource profile and real headroom

**What the sources actually say.**

- **The Paradox 4.3 performance diary** ([Dev Diary #410](https://admin-forum.paradoxplaza.com/forum/developer-diary/stellaris-dev-diary-410-4-3-performance-comparisons.1899301/page-6), **VERIFIED**). It describes "economic threading changes" with dependency tracking to "reduce CPU wait time and the monthly stutter". It also says that in 3.14 "we had hit the limits on how far we could go with optimization". *Corrected figures*: the **31% speedup is the full-run average** to about 2500. The often-quoted **11.54 vs 30.52 s per game year applies only at the 2200 start**, about 62% less time. By 2269, 4.3 had slowed to 3.14's *starting* pace. The diary gives **no main-thread or per-core breakdown**.
- **Player reports, which are anecdotes, not engine facts.** One thread at about 100% while total CPU sits near 33%, and "it seems not GPU bottleneck" ([Steam thread A](https://steamcommunity.com/app/281990/discussions/0/1741102632995174015), **VERIFIED as an anecdote**). Late-game lag tracking pop count even on an i9-13900K, with an RTX 3080 Ti owner reporting low overall resource use ([Steam thread B](https://steamcommunity.com/app/281990/discussions/0/3825286438457734441), **VERIFIED as an anecdote**). Taken together, these support "a serial tick with batched parallel bursts". They do **not** support per-core idle figures. Every "spare cores" number below is a **HYPOTHESIS** until M0-12 measures it.
- **Reference machine (HYPOTHESIS, conservative).** 6–8 cores, 16 GB RAM of which the modded game uses 8–14 GB late game, a 6–8 GB GPU, and Windows 11. The design must also degrade gracefully on a 4-core, 8 GB laptop with an iGPU.

| Resource | Who uses it in a late-game 30+ mod session | Real headroom | SAE budget |
|---|---|---|---|
| Main sim thread | Stellaris tick, **plus our mod's script** (REPORT) | None | Mod script ≤ 1% of s/game-year (scaling test, §8). The only cost no Governor can reach. |
| Other cores | Game's threaded batches **during the monthly pulse**; idle between pulses (HYPOTHESIS) | Bursty: about 0 during the pulse, some between pulses | PLAYING: 1 parse thread, ≥ 8 s after autosave close. PAUSED: ≤ min(N−2, 4) cores. Daemon ≤ 2% of one core. |
| RAM | Game 8–14 GB (HYPOTHESIS); OS; browser | **Binding constraint.** 2–6 GB on 16 GB, shrinking as the campaign ages | Daemon ≤ 150 MB; parse ≤ 1 GB peak and 0 after exit; telemetry ≤ 150 MB; local viewer off |
| Hard faults / paging | Game working set under pressure | Must not increase | Alarm signal: escalate first on this |
| Disk I/O | Autosave write bursts, mod load | Moderate outside autosave bursts | Read only after mtime and size are stable *and* ≥ 8 s have passed; background mode |
| Cache / memory bandwidth | Main thread (HYPOTHESIS: sensitive) | Small | Small working sets; parse never overlaps the pulse |
| GPU compute / VRAM | Renderer; mod texture packs inflate VRAM (HYPOTHESIS) | Compute idle, VRAM contested | **0 bytes** through M4 |
| Paused or closed wall time | Player planning; overnight (HYPOTHESIS: 30–60% of a session is paused, unmeasured) | Large | Opportunistic queue (§4); measure pause fraction in M2 |
| Second machine | Developer's laptop or mini-PC | Largest headroom | Bench farm, viewer, DuckDB, M5 |

---

## 3. Process topology

```
 Stellaris.exe  (never touch its priority or affinity)
   └─ sae_bridge mod: staggered SAE|v1| emits (sae_emit_period, per-country slot counter), sae_quiet
        │ game.log (tail)        │ autosave .sav               ▲ sae_in.ini (atomic; player hotkey `run`)
        ▼                        ▼                             │
 sae-daemon (Python, BELOW_NORMAL + EcoQoS, ≤150 MB, no Job, psutil self-monitor → health events)
   LiveAdapter · Engine core · Intent compiler · Governor (2 s poll) · bus writer
        │ spawn per autosave (args) / JSON on stdout          │ append
        ▼                                                     ▼
 sae-parse.exe (Rust/jomini; IDLE spawn → self BACKGROUND + EcoQoS;   runs/<id>/bus.jsonl
   Job: memory ≤1 GB, kill on breach; 1 thread PLAYING; exits)            │ tail          │ rsync
                                                                          ▼               ▼
                                         sae-telemetry (optional) ─gRPC─► second machine: Rerun viewer,
                                                                           DuckDB, sae-bench farm, (M5 parking lot)
```

**Component list for a diagram** (name — role):

- `Stellaris.exe` — host process; SAE never changes its priority or affinity.
- `sae_bridge` mod — in-game script on the main thread; staggered monthly emits; validates and applies knobs; ACKs.
- `game.log` — data-out channel (`SAE|v1|` lines).
- `autosave_*.sav` — ground-truth snapshot.
- `sae_in.ini` — data-in channel; executed by the player's `run` hotkey.
- `sae-daemon` — Python; LiveAdapter, Bayes filter, table, commitment, intent compiler, Governor, bus writer.
- `sae-parse.exe` — Rust; one run per autosave; section-selective extraction; JSON features plus cost fields on stdout.
- `bus.jsonl` — single source of truth, replay format and audit store.
- `sae-telemetry` — optional; tails the bus and streams to Rerun.
- `sae-sync` — rsync or robocopy of bus files and saves to the second machine.
- `second machine` — Rerun viewer, DuckDB, `sae-bench` farm, M5 surrogate track.

Arrows: `sae_bridge → game.log → sae-daemon`; `Stellaris → autosave → sae-parse → (stdout) → sae-daemon`; `sae-daemon → sae_in.ini → (player run) → sae_bridge`; `sae-daemon → bus.jsonl → sae-telemetry → Rerun (second machine)`; `bus.jsonl → sae-sync → second machine (DuckDB, bench)`; `sae-daemon → spawns → sae-parse`.

**Components, priorities and lifetimes.**

- **sae-daemon (Python ≥ 3.11).** It lives for the whole session. It uses `BELOW_NORMAL_PRIORITY_CLASS` and **not** IDLE, because IDLE threads are preempted by any normal-priority thread and the daemon must time log lines during the tick ([SetPriorityClass](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass), **VERIFIED**). On Windows 11 it also sets **EcoQoS** through `SetProcessInformation(ProcessPowerThrottling, EXECUTION_SPEED)`. EcoQoS "always selects most efficient CPU frequency and schedules to efficient cores" and is a hint ([MS QoS doc](https://github.com/MicrosoftDocs/win32/blob/docs/desktop-src/ProcThread/quality-of-service.md), [EcoQoS blog](https://devblogs.microsoft.com/performance-diagnostics/introducing-ecoqos/), **VERIFIED**). **The daemon is not placed in a Job Object.** A child inherits its parent's Job, and a nested Job's limits cannot be looser than the parent's. That would kill a 1 GB worker inside a 250 MB daemon Job (HYPOTHESIS for the nesting rule, which comes from knowledge of the Job docs and was not re-opened). The daemon instead watches its own RSS and CPU with psutil, emits `health` events, and exits cleanly if RSS stays above 150 MB. It has **no hard CPU cap**: a hard cap stops every thread until the next interval ([JOBOBJECT_CPU_RATE_CONTROL_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information), **VERIFIED**), which risks missing the ack window. **Import whitelist:** stdlib, psutil, NumPy, pydantic (at the bus boundary only), and the file-watch library. No torch, jax, cupy, rerun, duckdb, polars or pynvml. A CI test enforces this.
- **sae-parse.exe (Rust, jomini + serde, `windows` crate).** It lives for a single job. The daemon spawns it with `IDLE_PRIORITY_CLASS` (the class is inherited, which is fine for a worker that *should* be preempted). **Its first call puts itself into `PROCESS_MODE_BACKGROUND_BEGIN`**, because that value "can be specified only if hProcess is a handle to the current process" (same page, **VERIFIED**). The daemon passes a flag, not a handle. The worker then sets EcoQoS. Microsoft says background mode lowers "resource scheduling priorities" and that the thread "will never be starved". The specific claim that it lowers I/O and memory priority is a reasonable reading but **HYPOTHESIS**. The daemon places the worker in its **own Job**, with a process memory limit of 1 GB that kills on breach, and a CPU-rate hard cap matching its thread allowance. CpuRate is a share of *all* processor cycles in percent × 100, so one core on N logical processors is 10000/N (**VERIFIED** unit; budgets in this document are always stated as "% of one core"). The worker starts only when the save's mtime and size have been stable for 2 s **and** ≥ 8 s have passed since close. It deserialises only the needed sections (HYPOTHESIS that this lowers peak RSS; M0-11). It writes one JSON document to stdout, `{features…, rss_peak, cpu_s, wall_s, schema_version}`, and exits, so the OS reclaims every byte.
- **sae-telemetry (optional, Python plus the Rerun SDK).** It is the only process that imports Rerun. It tails `bus.jsonl` and streams to a viewer on another machine. If the viewer stalls, the engine is unaffected.
- **IPC.** Game ↔ daemon works as in the report: log tail, autosave, `run sae_in.ini`. Daemon → parse uses command-line arguments in and JSON on stdout. Daemon → telemetry uses only the bus file. Daemon → second machine uses files plus rsync. **There is no network job protocol before M5.** The file-change watch has a 2 s polling fallback, and nothing polls faster than every 2 s.
- **Linux/Proton equivalents.** Daemon: `nice 10`. Worker: `SCHED_IDLE`, `ionice -c3`, and a cgroup v2 `memory.max` via `systemd-run --user --scope -p MemoryMax=1G`.
- **Affinity.** Prefer soft CPU sets (`SetProcessDefaultCpuSets`) for our processes over hard masks (HYPOTHESIS: this degrades more gracefully).

**Ironman mode.** Ironman disables the console and with it `run` (REPORT), so the live loop does not exist. The companion parses autosaves (and the log, if it survives Ironman) and regenerates the between-session files. The online budget shrinks to "parse, or defer the parse until CLOSED". The Governor still runs, but no in-game cost knob can change mid-session.

---

## 4. Resource Governor

The Governor is a module inside the daemon, not a separate process. It keeps two independent axes.

**State axis (opportunity):**

| State | Detection | Allowed work |
|---|---|---|
| **PLAYING** | Stellaris process present, CPU-time rate above the paused band | Online loop; one parse per eligible autosave, single-threaded |
| **PAUSED** | Stellaris CPU-time rate (psutil `cpu_times` delta, 2 s samples) in the paused band for 4–6 s; confirmed by a missing monthly line | Opportunistic queue: ≤ min(N−2, 4) cores, EcoQoS, RAM checked before each job |
| **CLOSED** | No Stellaris process (or minimised for more than 10 min) | Full local budget minus 2 cores; calibration, replay suites, compaction |

**Pause detection is test M1-P.** It checks whether the CPU-time rate separates paused from running on the real playset. Neither `game.log` nor the save header records pause: `game_paused` is a console command, and no autosave is written while the game is paused. If M1-P fails, pause harvesting runs only in CLOSED and minimised states.

**Health ladder (pressure):**

| Level | Entry trigger (any) | Behaviour |
|---|---|---|
| **H0 Normal** | — | Parse every autosave; opportunistic queue on in PAUSED with full allowance; full telemetry |
| **H1 Sparse** | Available RAM < 2 GB; **or** spm ratio ≥ 1.10 (see below) | Parse every 2nd, then every 3rd autosave; queue off; telemetry streams scalars only. **Skip saves before cutting threads.** |
| **H2 Decision-only** | Available RAM < 1.5 GB; **or** hard-fault rate on the system or game above the baseline band | Parse only in the 3 months before a commitment boundary; PAUSED allowance 1 core; kill any running worker. This is acceptable because decisions are 24-month commitments (REPORT). |
| **H3 Quiet** | Available RAM < 1 GB; **or** sustained H2 breach for 6 months; **or** user "Low impact" preset | No parsing, log tail only. Queue `sae_emit_period=12` (or `sae_quiet=1`) into the next `sae_in.ini`. It takes effect when the player next presses the hotkey. |
| **S Suspended** | User `pause` command only | Engine idle; the mod keeps the last knobs, which stay inside the safety floor |

**Triggers and hysteresis (exact).**

- **RAM and hard faults escalate first and immediately.** Escalation happens within one 2 s poll and must be reached within 10 s. A RAM or fault trigger skips levels if needed. Hard faults come from the system-wide `\Memory\Pages Input/sec` PDH counter plus psutil page-fault deltas on the game. psutil's Windows count mixes soft and hard faults, so which source is reliable is a **HYPOTHESIS** for M0-12.
- **spm is only a trigger in the form median-of-6 months vs median-of-24 months ≥ 10%.** It escalates by **one level at most per 6 game months**. **Decision:** spm alone can never push past H1, because it is confounded by changes in game speed and by natural late-game drift. Levels H2 and above need RAM, fault or user evidence. A step change in spm above 40% between consecutive months is treated as a speed change, and the 24-month baseline is reset.
- **De-escalation is one level after 6 consecutive game months with no trigger.** For RAM, that means available memory stays at least 0.5 GB above the entry threshold. For spm, the ratio must stay below 1.05.
- **The in-situ self-A/B** drops to H3 for 3 months every N game years and compares spm against neighbouring months. It is logged as a diagnostic and **never acted on**. Three months is too short to be an estimate.

**Sensing sources** (2 s poll, cheap probes only): wall-clock gaps between monthly `SAE|v1|` lines (spm); Stellaris process CPU-time rate (state); `virtual_memory().available`; hard-fault counters; the daemon's own RSS and CPU; the parse worker's self-reported `rss_peak, cpu_s, wall_s`. **No NVML or VRAM sensing before M5.** VRAM sensing would live only in a future compute worker, never in the daemon, and "no reading" means "GPU unavailable".

**In-game cost controls** (the only levers on the main thread).

- `sae_emit_period` sets the months between emits per country. Each country gets a **slot from a per-country counter variable assigned on first emit**, because countries have no stable small index in script. A country emits when `(month_index + slot) mod period == 0`, which keeps cost flat instead of spiking.
- `sae_quiet` suppresses all emits except ACK.
- Both change only through `run`. In Ironman they are fixed per session.

**Safety property.** Correctness never depends on throughput. Lower levels only coarsen the timing of observations. Replay test CI-1 checks this.

---

## 5. GPU and parallelism

**Never on the GPU** (any milestone, player machine): the belief update, the table lookup, the intent compile, the validator; save parsing (serial and branchy); the Governor; telemetry. **Nothing GPU-related on the player's machine through M4**, including sensing.

**Could go on a GPU later (M5 parking lot, second machine only).** Batched rollouts of a **surrogate macro-simulator** fitted on M4 bus data, at 10³–10⁵ rollouts × hundreds of steps. This is the one wide, uniform, batched shape where a GPU pays, and it would move CMA-ES over template θ from game time to surrogate time. **Gate:** M4 has produced at least a few hundred validated runs, *and* the surrogate predicts held-out runs. No interface stubs and no M1 work go toward it. Also post-M4 and off the decision path: local LLM inference for condition D or a commentator, on the second machine (llama.cpp with explicit `-ngl` offload, REPORT).

| Framework | Verdict | Reason |
|---|---|---|
| **CuPy** | Rejected for users; allowed only on a Linux NVIDIA second machine for M5 | `cupy-cuda12x` Windows wheels are about 99–100 MB, and the page says "You need to install CUDA Toolkit 12.x locally" (or use the `[ctk]` extra, whose size is unverified) ([PyPI](https://pypi.org/project/cupy-cuda12x/), **VERIFIED**). NVIDIA-only. |
| **PyTorch** | Second machine only (training, M5 surrogate fitting) | Heavy import and CUDA context. Ship any learned artefact as ONNX. |
| **JAX** | Rejected on Windows player machines; acceptable on a Linux second machine | Native Windows is CPU-only and experimental; NVIDIA GPU support is experimental under WSL2 only ([JAX install](https://docs.jax.dev/en/latest/installation.html), **VERIFIED**) |
| **ONNX Runtime + DirectML** | Rejected through M4; CPU EP if a learned model ever ships | DirectML is in "sustained engineering", with new work moved to WinML. It requires sequential execution and disabled memory patterns, and it handles dynamic shapes poorly. It runs on DX12 devices (Kepler, GCN and Haswell iGPU onward) ([ORT DirectML EP](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html), **VERIFIED**). There is no ONNX graph to run anyway: the model is a table. |
| **Numba** | Deferred; only for a profiled hot loop in a worker, with `NUMBA_NUM_THREADS` capped | No hot loop exists before M4 |
| **Rust + rayon** | Yes, inside `sae-parse` from M2–M3, **only** with an explicit `ThreadPoolBuilder::num_threads(n)` set by the Governor; single-threaded until then | Never the default of all cores. Whether jomini can split by section is a HYPOTHESIS, because its text parser works over a whole buffer. |

**Multi-core use.** The gain from a separate process is isolation, not throughput. In PLAYING mode the worker uses 1 thread at IDLE plus background priority. In PAUSED mode it uses ≤ min(N−2, 4) threads, with EcoQoS. On unpause the worker is **suspended** if RAM is above the floor and **killed** if it is below. A suspended worker still holds its RAM, so suspension saves CPU but not memory. Pause-time jobs, in priority order: the deferred latest-save parse; reconciliation of log features against save ground truth; bus compaction and god-view pruning; replay analysis and pre-built Rerun blueprints; parser re-runs after schema changes. Every job checkpoints and can be killed at any point. **Invariant:** no opportunistic job ever writes `sae_in.ini` or produces an input the decision path waits for. A result is used only if it is already complete. CI-2 verifies this by replaying with the queue on and off and requiring identical intents.

**Second machine (file-based).** In order of value: (1) the **M4 `sae-bench` farm**, one Stellaris instance per machine (HYPOTHESIS: several per box is RAM-prohibitive with 30+ mods), taking `{start_save, playset hash, mod hash, engine config, seed, condition}` in as files and returning bus files; (2) the Rerun viewer and DuckDB; (3) release-gate runs; (4) the M5 track. Transport is `sae-sync` (rsync or robocopy over SMB). The daemon and the intent path **always stay local**. The second machine is required for development and benchmarking, **never for play**.

---

## 6. Framework and stack

| Component | Choice | Why | Rejected alternatives |
|---|---|---|---|
| Daemon | Python ≥ 3.11, psutil, NumPy, pydantic (boundary), import whitelist | Report decision; trivial workload; replay and synth adapters and dev loop are in Python | Rust daemon (Fable's first draft): rewrite only if R-1 attributes overhead to the daemon |
| Save parse | Standalone Rust exe, jomini + serde, JSON stdout | Peak memory is isolated and fully reclaimed on exit; Job-cappable without risking the daemon | PyO3 extension in the daemon (heap fragmentation, spike inside the long-lived process); Python parser |
| OS controls (Windows) | `SetPriorityClass`, `SetProcessInformation` (EcoQoS), Job Objects (worker only), CPU sets; `windows` crate in Rust, ctypes or pywin32 in the daemon | All documented Win32 APIs | Touching the game's priority or affinity; hard affinity masks; a Job on the daemon |
| OS controls (Linux) | nice, SCHED_IDLE, ionice, cgroup v2 via systemd-run | Equivalent semantics | — |
| Parallel CPU | Single thread → rayon with an explicit pool (M2–M3) | Governor-controlled thread count | Default rayon pool; Numba before profiling |
| Telemetry | Separate `sae-telemetry` process, Rerun SDK, **remote viewer by default** | Viewer defaults to 75% of system RAM | Rerun inside the daemon; local resident viewer; TensorBoard |
| Analysis | DuckDB CLI, offline only (CLOSED state or second machine) | Columnar SQL over JSONL | DuckDB resident in the daemon; SQLite at v0 |
| Second machine I/O | rsync or robocopy file sync | Benchmark inputs and outputs are files already | msgpack-over-TCP job protocol with tokens and TLS (deferred to M5 at the earliest) |
| GPU | None through M4 | §5 | CuPy, PyTorch, JAX, ORT DirectML on the player's machine |
| Frame-time capture | PresentMon, release gates only | Measures stutter directly | Runtime frame-time sensing |

---

## 7. Monitoring under load

**Rerun policy.** The viewer defaults to using **up to 75% of system RAM** ([Rerun: limit RAM](https://rerun.io/docs/howto/limit-ram), **VERIFIED**). This is hostile on a machine short of RAM. The default is: the viewer runs **on the second machine**, and `sae-telemetry` streams to it over gRPC (`rerun+http://HOST:9876/proxy`). The server's buffering is bounded with `--server-memory-limit` (documented default 1 GiB) ([Rerun CLI](https://docs.rerun.io/dev/reference/cli/), **VERIFIED** by one reader only). The second option is to write `.rrd` files for later viewing. A local viewer is for developers only, always with `--memory-limit 512MB`. *Correction:* Fable's `rerun --serve` may be stale, because recent CLIs split gRPC and web serving. **Pin the Rerun version and re-check every flag against that version's CLI reference in M1** before writing user documentation.

**Telemetry tiers.**

| Tier | Content | When |
|---|---|---|
| T0 Always (bus) | `observation`, `belief_update`, `strategy_selected`, `intent_emitted`, `intent_ack`, `outcome`, `control`; **`health`** at every level change and every month: Governor state and level, trigger, available RAM, fault rate, spm and the 6/24-month medians, daemon RSS and CPU, last parse `rss_peak, cpu_s, wall_s`, skipped saves, killed and suspended jobs, budget breaches, pause fraction | Every state, including Ironman |
| T1 Scalars | T0 streamed to Rerun as time series | H0–H1, viewer connected |
| T2 Full | God-view stream, per-parse feature dumps, blueprints | H0 only, dev preset |

**Always recorded, at any level:** every Governor transition with its trigger, every budget breach, every intent and ack, and every skipped parse. The bus must stay explainable even when SAE is quiet. Disk limits: bus ≤ 100 MB per 200-year campaign; god-view ≤ 300 MB, pruned by compaction.

---

## 8. Budgets and acceptance tests

| Component | CPU (% of one core) | RAM | Other |
|---|---|---|---|
| Mod script | ≤ 1% of s/game-year at the shipped config (derived: about 10–40 ms per monthly tick when a late-game month takes about 1–4 s) | Save growth ≤ 1% | Staggered emits |
| sae-daemon | ≤ 2% averaged over 60 s; self-monitored, no hard cap | ≤ 150 MB RSS steady | Import whitelist; poll ≥ 2 s |
| sae-parse | PLAYING: 1 thread; PAUSED: ≤ min(N−2, 4); CLOSED: N−2 | ≤ 1 GB (Job, kill); **Decision:** 2 GB only in CLOSED. The game holds its RAM while paused. | ≥ 8 s after close; ≤ 6 s wall at H0 under EcoQoS (HYPOTHESIS) |
| sae-telemetry | ≤ 2% | ≤ 150 MB | Off for players by default |
| Local Rerun viewer | — | `--memory-limit 512MB` | Developers only |
| GPU / VRAM | — | **0 bytes** | Manifest rule |
| Whole system | Game slowdown ≤ 2% mean, upper 95% CI ≤ 3% s/game-year | No increase in hard faults | p99 frame time ≤ +5%; monthly hitch duration within noise |

**Acceptance tests.** CI tests need no game and run on Linux. Release gates run on the second machine against the user's real 30+ mod playset at year ≥ 2350.

| ID | What | How | Pass | Kind |
|---|---|---|---|---|
| CI-1 | Ladder coarsens timing, not decisions | Replay a recorded campaign at forced H0 vs forced H2 | Switches in the same commitment window in ≥ 90% of cases (HYPOTHESIS threshold) | CI |
| CI-2 | Opportunistic independence | Replay with the queue on/off, with random kills and suspends injected | Identical intents | CI |
| CI-3 | Governor logic | Synthetic sensor traces: drifting healthy spm, speed-change step, RAM ramp, fault spike | Drift never exceeds H1; step resets baseline; RAM reaches H2 within 10 s (simulated); no oscillation under 6 months | CI |
| CI-4 | Footprint and manifest | Start the daemon; inspect its import graph and RSS; scan dependencies | No forbidden imports; RSS ≤ 150 MB; zero GPU packages | CI |
| CI-5 | Parse contract and memory cap | Run `sae-parse` on fixture saves plus a 2× pathological save under a cgroup | Valid JSON with cost fields; killed at the cap; daemon logs `health:budget_breach`, falls back to log-only, keeps running | CI |
| M1-P | Pause detectability | CPU-time rate of the game, paused vs running, on the real playset | Separable bands with a 4–6 s debounce; otherwise pause harvesting is limited to CLOSED | M1 experiment |
| R-1 | Overhead A/B | 10 game years at fastest speed; (a) no SAE vs (c) engine at H0, 5 runs each, interleaved; (b) mod only and (d) plus telemetry, once each; bootstrap CI | Mean ≤ 2%, upper CI ≤ 3% | Release gate |
| R-2 | Stutter | PresentMon during R-1 | p99 frame time ≤ +5%; monthly hitch within the noise band of (a) | Release gate |
| R-3 | Mod-script cost | Scaling test, same save: `sae_quiet`, `period=1`, `period=1` with 5× emits; slope gives the per-emit cost | Projected cost at the shipped config ≤ 1% of s/year; also reported as ms per monthly tick | Release gate (first run in M0) |
| R-4 | Starved machine | A balloon process leaves about 1.5 GB available; (a) vs (c) | Game hard faults within noise of (a); Governor reaches H2 within 10 s on its own; daemon keeps tailing and acking | Release gate |
| R-5 | Windows Job and pause behaviour | Pathological save on real Windows; 60 s pause, then unpause with RAM above and below the floor | Job kills at 1 GB; deferred parse completes while paused; on unpause the worker is suspended (RAM OK) or killed (RAM low) within one 2 s poll | Release gate |
| R-6 | Low end | 4-core / 8 GB laptop, iGPU | Monthly loop still runs; Governor settles at ≤ H2 with no stutter regression beyond R-2's bounds | Release gate |

---

## 9. Disagreement log

| Issue | Opus position | Fable position | Resolution |
|---|---|---|---|
| GPU | Not through M4; surrogate seam designed now | Not through M3–M4; only offline LLM at M4 | No GPU on the player's machine through M4; M5 parking lot on the second machine, **no seam or stubs** |
| Daemon language | Python | Rust binary (~20 MB) | Python with whitelist, ≤ 150 MB; Rust only if R-1 blames the daemon |
| Parse worker | Separate Rust exe | "PyO3 or standalone" | Separate exe (Fable conceded) |
| Worker priority | Background mode set "after start" | IDLE + EcoQoS | IDLE at spawn; worker sets background mode **on itself**; EcoQoS |
| Job Objects | Daemon Job 250 MB + worker Job 1 GB + hard CPU cap | Worker memory cap | Worker-only Job; daemon self-monitors (nesting limit HYPOTHESIS) |
| Daemon CPU cap | Hard cap at 5% | Not specified | NOTIFY-style self-monitoring, no hard cap; budgets in % of one core |
| Governor shape | L0–L5 pressure ladder | 4 modes incl. BACKOFF | State × health level |
| spm triggers | +3/+5/+10% with 2-month escalation | +25% vs 12-month baseline | Median-of-6 vs median-of-24 ≥ 10%, 6-month hysteresis; **Decision:** spm alone caps at H1, because it is confounded by speed changes |
| Parse start | mtime stable 2 s | ≥ 8 s after close | Both |
| Back-off order | Skip saves and cut threads together | Skip saves first | Skip saves first |
| Pause detection | Missing line + CPU-rate drop | `game_paused` from log or save | CPU-time rate, tested in M1-P; Fable's sources do not exist |
| Pause compute | ≤ 2 cores, RAM > 2 GB | N−2 cores, 2 GB worker, suspend on unpause | min(N−2, 4) cores; RAM check per job; suspend, but kill under memory pressure; **Decision:** 2 GB worker only in CLOSED |
| Mod-script metric | ≤ 1% s/year by A/B | ≤ 15 ms per tick from `game.log` | Scaling test, reported as % of s/year with derived ms per tick (no tick timer is known) |
| Stagger index | `i mod period` | — | Per-country slot counter assigned on first emit |
| LAN protocol | msgpack over TCP, tokens, TLS | rsync or HTTP push | File and rsync based; no network protocol before M5 |
| NVML | Only when the GPU worker is enabled | Never in v0–v2 | None before M5; daemon never |
| Rerun | Local `--memory-limit`, remote preferred | `--serve` remote default | Remote default, memory limit locally; pin the version and re-check flags |
| DuckDB | Over synced files | Offline tool | Offline only (CLOSED or second machine) |
| Test cadence | AT-1/2/3 release gates, reboot-fresh | A1–A5 "in CI" | CI (game-free) vs release gates on the second machine; drop reboot-fresh |
| Ironman | Not addressed | "Players mostly Ironman" | No live loop; observe and adapt between sessions |
| Dev diary numbers | — | "31%" quoted with 11.54 vs 30.52 | 31% is the full-run average; 11.54/30.52 is the 2200 start only |

---

## 10. Roadmap deltas, open decisions, risks

| Milestone | Delta | Gate |
|---|---|---|
| **M0** (+½ day) | M0-12 baseline: s/month for (a) no SAE vs (b) the HELLO mod on the real save; the game's RSS and available RAM; which hard-fault counter works; first R-3 scaling run. M0-11 also records parse **peak RSS** and tests section skipping. Probe for a script profiler. | If (b) already costs > 1% s/year, redesign emission before writing engine code |
| **M1** | `sae-parse.exe` as a separate process (args in, JSON out, cost fields); Governor v1 (PLAYING, CLOSED, H0/H2/H3 on RAM and faults); priorities, EcoQoS, worker Job; `sae_emit_period` and `sae_quiet` with slot counters; telemetry process with remote Rerun; M1-P; CI-4 and CI-5 | Daemon ≤ 150 MB; M1-P result recorded |
| **M2** | PAUSED state and opportunistic queue (if M1-P passes); full ladder with spm medians; calibration fits as CLOSED jobs; pause-fraction telemetry; CI-1 to CI-3; optional rayon pool in the parser | CI suite green |
| **M3** | Presets "Low impact / Balanced / Dev"; mod page states the measured overhead; Ironman observe-only path | **R-1 to R-6 pass** on the second machine |
| **M4** | `sae-bench` farm on one or more second machines, file-based; DuckDB and Rerun there; any LLM condition D runs there | ≥ 10 paired runs per archetype (REPORT) |
| **M5** (parking lot) | Surrogate simulator; GPU batched rollouts on the second machine | Several hundred validated M4 runs **and** held-out surrogate validation |

**Open decisions for the user.**

1. **A second machine.** Is there one? It is required for R-gates and M4 (not for play). Without it, release gates run on the gaming PC overnight with the game as the only load, and M4 throughput drops to one run at a time.
2. **Default preset for players.** Balanced (H0 with pause harvesting) or Low impact (sparse parsing, no pause jobs, no fan noise while paused).
3. **Ironman release posture** (report decision 4). If Ironman ships, its online budget is parse-only and adaptation happens per session.
4. **Whether M5 is ever opened.** The default is no, unless M4 data justifies it.

**Top risks.**

1. **The mod's own script cost** is beyond any Governor's reach. If R-3 fails, the only fixes are a longer `sae_emit_period`, fewer managed countries, or bucketed variables. Likelihood medium, impact high.
2. **RAM bites first.** A careless parse of a late-game modded save can reach 3–4 GB (HYPOTHESIS). The Job cap turns that into a killed worker and H2, which is safe but leaves the detector blind. Mitigation: section-selective parsing measured in M0-11. Likelihood medium, impact medium.
3. **Pause detection fails** on some machines (M1-P), so pause-time headroom is lost. The fallback is CLOSED-only harvesting. Likelihood medium, impact low.
4. **The spm signal is noisy.** The Governor might chatter or drift upward. Mitigations are the 6/24 medians, 6-month hysteresis, the H1 cap on spm, and CI-3. Likelihood medium, impact low.
5. **Scope creep: "we have a GPU, let's use it."** The zero-GPU-bytes manifest rule, CI-4 and the M5 gate give maintainers an objective reason to decline. Likelihood high, impact medium.
6. **OS hints are hints.** EcoQoS and priority may still put our worker on a P-core for a quantum during the pulse. The ≥ 8 s start delay is the real protection. Likelihood low, impact low.

---

## Sources

Opened and checked by at least one of the four source documents:

- Paradox Dev Diary #410, 4.3 performance comparisons: https://admin-forum.paradoxplaza.com/forum/developer-diary/stellaris-dev-diary-410-4-3-performance-comparisons.1899301/page-6
- Steam discussion A (CPU and GPU utilisation): https://steamcommunity.com/app/281990/discussions/0/1741102632995174015
- Steam discussion B (late-game lag, core use): https://steamcommunity.com/app/281990/discussions/0/3825286438457734441
- SetPriorityClass: https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass
- JOBOBJECT_CPU_RATE_CONTROL_INFORMATION: https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information
- Microsoft Quality of Service / EcoQoS: https://github.com/MicrosoftDocs/win32/blob/docs/desktop-src/ProcThread/quality-of-service.md
- Introducing EcoQoS: https://devblogs.microsoft.com/performance-diagnostics/introducing-ecoqos/
- ONNX Runtime DirectML EP: https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html
- JAX installation: https://docs.jax.dev/en/latest/installation.html
- PyPI cupy-cuda12x: https://pypi.org/project/cupy-cuda12x/
- nvidia-ml-py: https://pythonhosted.org/nvidia-ml-py
- Rerun, limit RAM: https://rerun.io/docs/howto/limit-ram
- Rerun CLI reference: https://docs.rerun.io/dev/reference/cli/

Carried from the context report and not re-opened in this pass (REPORT): jomini (https://github.com/rakaly/jomini), llama.cpp (https://github.com/ggml-org/llama.cpp), Stellaris wiki Console (https://stellaris.paradoxwikis.com/Console).
