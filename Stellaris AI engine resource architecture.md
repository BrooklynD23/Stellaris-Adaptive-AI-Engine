# Stellaris AI engine resource architecture

Oct 18, 2026 · @NC

## Summary

No GPU on the player's machine through M4: the engine's per-month work is about 100 floating-point operations, less than one GPU kernel launch costs, and a GPU context would take RAM and VRAM the modded game needs. The real headroom is idle cores between monthly ticks, paused and closed-game time, and a second machine. RAM, not CPU, is the binding constraint, because paging the game's memory causes stutter players will blame on us.

This extends the Adaptive Stellaris AI engine report and keeps its decisions. It adds three changes:

- **Three processes, not one.** A lean Python daemon, a Rust save parser that runs once per autosave and exits, and an optional telemetry streamer.
- **A Resource Governor.** It tracks game state (playing, paused, closed) and machine pressure separately, and backs off on RAM and paging first.
- **Measured budgets.** Game slowdown at most 2% on your real 30+ mod playset, checked by release tests on a second machine.

Opus 5.5 and Fable 5.1 each drafted a proposal independently, cross-reviewed each other, then Opus merged and Fable signed off. Claims marked HYPOTHESIS need measuring in M0 or M1.

## Resource profile

Stellaris spends most of a late-game session on one serial simulation thread with parallel bursts at the monthly pulse, so spare cores exist between ticks, not during them. Paradox's 4.3 diary reports economic threading work to cut monthly stutter, with a 31% average speedup over a full run; the often-quoted 11.54 vs 30.52 seconds per game year applies only at the 2200 start ([Dev Diary #410](https://admin-forum.paradoxplaza.com/forum/developer-diary/stellaris-dev-diary-410-4-3-performance-comparisons.1899301/page-6)). Player reports of one thread near 100% with low GPU use are anecdotes, not engine facts ([Steam A](https://steamcommunity.com/app/281990/discussions/0/1741102632995174015), [Steam B](https://steamcommunity.com/app/281990/discussions/0/3825286438457734441)).

Reference machine (HYPOTHESIS): 6–8 cores, 16 GB RAM with the game using 8–14 GB late game, a 6–8 GB GPU, Windows 11. It must still work on a 4-core, 8 GB laptop.

| Resource | Used by | Real headroom | SAE budget |
| --- | --- | --- | --- |
| Main simulation thread | Stellaris tick plus our mod's script | None | Mod script ≤ 1% of seconds per game year; no external governor can reach it |
| Other cores | Game's threaded batches during the monthly pulse | Some between pulses (HYPOTHESIS) | Playing: 1 parse thread. Paused: ≤ min(N−2, 4). Daemon ≤ 2% of one core |
| RAM | Game 8–14 GB, OS, browser | Binding: 2–6 GB on 16 GB, shrinking late game | Daemon ≤ 150 MB; parser ≤ 1 GB peak, 0 after exit; local viewer off |
| Paging (hard faults) | Game working set under pressure | Must not increase | First alarm signal |
| Disk I/O | Autosave write bursts | Moderate outside saves | Read saves only ≥ 8 s after they close, at background priority |
| GPU and VRAM | Renderer; texture mods inflate VRAM | Compute idle, VRAM contested | 0 bytes through M4 |
| Paused or closed time | Player planning, overnight | Large (fraction unmeasured) | Opportunistic work queue |
| Second machine | Dev laptop or mini-PC | Largest | Benchmarks, viewer, analysis, any GPU work |

## Architecture

The game is the host and SAE is a guest: SAE never changes Stellaris's priority or affinity, and its only cost on the game's main thread is the mod script.

&#91;embedded content: SAE process topology · 4 processes, 3 game channels\]

The daemon is the only long-lived SAE process on the gaming PC; the parser exists only while reading a save, and viewing happens on the second machine.

| Process | Lifetime | Priority and limits | Job |
| --- | --- | --- | --- |
| `sae_bridge` mod | In game | Main thread; staggered monthly emits; applies knobs, writes acks | Data out via `game.log`; data in via `run sae_in.ini` |
| `sae-daemon` (Python ≥ 3.11) | Whole session | Below-normal priority + EcoQoS; ≤ 150 MB; self-monitored, no hard CPU cap; import whitelist (stdlib, psutil, NumPy, pydantic) | Log tail, belief model, strategy table, intent compiler, Governor, event-log writer |
| `sae-parse.exe` (Rust, jomini) | One autosave | Spawned at idle priority, then sets background mode and EcoQoS on itself; Job Object with a 1 GB commit limit and kill-on-close; a crash or missing JSON counts as a breach | Reads only needed save sections, prints JSON features plus its own cost, exits |
| `sae-telemetry` (optional) | When streaming | ≤ 150 MB; the only process that imports Rerun | Tails the event log, streams to a remote viewer |
| Second machine | Dev and bench | Not used for play | Rerun viewer, DuckDB, benchmark farm, M5 |

Why these choices:

- **Separate parser process.** The save parse is the only memory spike; a process that exits returns every byte to the OS.
- **Below-normal, not idle, for the daemon.** Idle threads are preempted by any normal thread, and the daemon must read log lines during the tick ([SetPriorityClass](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass)). Background mode can only be set by a process on itself, so the parser does it.
- **No Job Object on the daemon.** Child processes inherit the parent's Job, and a nested Job's memory limit cannot be looser than its parent's, so a daemon Job would cap the parser too. A hard CPU cap would also freeze all threads until the next interval and could miss an ack ([CPU rate control](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information)).
- **EcoQoS** steers our processes to efficient cores at low frequency; it is a hint, not a guarantee ([EcoQoS](https://devblogs.microsoft.com/performance-diagnostics/introducing-ecoqos/)).
- **Files only between machines.** rsync or robocopy; no network job protocol before M5.

### Resource Governor

The Governor lives inside the daemon and tracks two separate axes: what the game is doing, and how much pressure the machine is under.

| Game state | Detection | Allowed work |
| --- | --- | --- |
| Playing | Stellaris CPU-time rate above the paused band | Online loop; one single-threaded parse per eligible autosave |
| Paused | CPU-time rate in the paused band for 4–6 s (test M1-P) | Opportunistic queue on ≤ min(N−2, 4) cores, RAM checked before each job |
| Closed | No Stellaris process, or minimised > 10 min | Full budget minus 2 cores: calibration, replay suites, compaction |

| Health level | Entry trigger (any) | Behaviour |
| --- | --- | --- |
| H0 Normal | — | Parse every autosave; pause queue on; full telemetry |
| H1 Sparse | Available RAM < 2 GB, or slowdown ≥ 10% (median of last 6 months vs last 24) | Parse every 2nd, then 3rd autosave; queue off; scalar telemetry only |
| H2 Decision-only | Available RAM < 1.5 GB, or hard-fault rate above baseline (median + 3×MAD of the first 10 minutes, for 3 polls) | Parse only in the 3 months before a strategy commitment ends; kill any running parser |
| H3 Quiet | Available RAM < 1 GB, 6 months stuck at H2, or the Low impact preset | No parsing; log tail only; queue `sae_emit_period=12` or `sae_quiet=1` for the next hotkey press |
| Suspended | User command only | Engine idle; the mod keeps its last knobs |

Rules:

- **RAM and paging escalate first**, detected on the next 2 s poll and acted on within 10 s, skipping levels if needed.
- **Game slowdown alone never goes past H1.** It is confused by game-speed changes and normal late-game drift. A jump over 40% between months counts as a speed change and resets the baseline.
- **Back off one level** after 6 game months with no trigger.
- **Skip save parses before cutting threads.**
- **Backing off is safe** because strategies are 24-month commitments: lower levels only see the player less often.

In-game cost controls: `sae_emit_period` (months between emits per country, each country on its own slot so cost stays flat) and `sae_quiet` (acks plus a one-line monthly heartbeat, so the Governor can still measure slowdown and pause and can step back down from H3). Both change only through `run`.

**Ironman has no live loop.** The console and `run` are disabled, so the companion only parses autosaves and adapts between sessions.

## GPU and parallel processing

The GPU stays off the player's machine through M4, enforced by a manifest rule: zero GPU bytes allocated by any SAE process and no CUDA, DirectML or Vulkan dependency, checked in CI. The belief update, strategy table, validator, Governor and telemetry are tiny and serial. Save parsing is a branchy tokenizer, which is not GPU-shaped.

**Where a GPU could pay later (M5, second machine only).** A surrogate simulator fitted on M4 benchmark data could run 10³–10⁵ batched rollouts, moving strategy tuning from game time to simulated time. It opens only after several hundred validated M4 runs and a held-out test of the surrogate, with no stubs built before then. Any local LLM for later experiments also runs there.

| Framework | Verdict | Reason |
| --- | --- | --- |
| CuPy | Second machine only (Linux, NVIDIA) | NVIDIA-only; Windows wheel \~100 MB and needs a local CUDA 12.x toolkit ([PyPI](https://pypi.org/project/cupy-cuda12x/)) |
| PyTorch | Second machine only | Heavy import and CUDA context; ship any learned model as ONNX |
| JAX | Rejected for Windows players | No supported native-Windows GPU path; GPU only experimentally under WSL2 ([JAX install](https://docs.jax.dev/en/latest/installation.html)) |
| ONNX Runtime + DirectML | Rejected through M4 | DirectML is in sustained engineering with new work moved to WinML, and handles dynamic shapes poorly ([ORT DirectML](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)); the model is a table anyway |
| Numba | Deferred | Only for a profiled hot loop, with thread count capped; none exists before M4 |
| Rust rayon | Yes, in the parser from M2–M3 | Only with an explicit thread count set by the Governor, never all cores |

**Using spare cores.** The win from separate processes is isolation, not speed. While playing, the parser uses one thread. While paused it may use up to min(N−2, 4) threads; on unpause it is suspended if RAM is fine and killed if not, since a suspended process still holds its memory.

Pause-time jobs, in priority order: the deferred latest-save parse, checking log features against save data, event-log compaction, replay analysis, and parser re-runs after schema changes. No pause job ever writes `sae_in.ini` or feeds a decision; a CI test replays with the queue on and off and requires identical intents.

**Second machine.** In order of value: the M4 benchmark farm (one Stellaris instance per machine), the Rerun viewer and DuckDB, release-gate runs, then M5. It is needed for development and benchmarks, never for play.

## Frameworks and stack

The stack is Python for the daemon, Rust for the save parser, documented OS controls for priority and memory, and everything heavy kept off the player's machine.

| Component | Choice | Why | Rejected |
| --- | --- | --- | --- |
| Daemon | Python ≥ 3.11, psutil, NumPy, pydantic at boundaries, import whitelist | Trivial workload; dev loop and replay adapters already in Python | Rust daemon, unless the overhead test blames the daemon |
| Save parser | Standalone Rust exe, jomini + serde, JSON on stdout | Memory spike isolated and reclaimed on exit; can be memory-capped safely | PyO3 extension inside the daemon; Python parser |
| OS controls, Windows | SetPriorityClass, EcoQoS via SetProcessInformation, Job Object on the parser only, soft CPU sets | Documented Win32 APIs | Changing the game's priority or affinity; hard affinity masks |
| OS controls, Linux/Proton | nice, SCHED\_IDLE, ionice, cgroup v2 memory.max via systemd-run | Same semantics | — |
| Parallel CPU | Single thread, then rayon with an explicit pool (M2–M3) | Governor sets the thread count | Default all-core pool; Numba before profiling |
| Telemetry | Separate process, Rerun SDK, remote viewer by default | The viewer defaults to 75% of system RAM | Rerun inside the daemon; local resident viewer |
| Analysis | DuckDB CLI, offline only | SQL over the event log | DuckDB inside the daemon |
| Second machine sync | rsync or robocopy | Benchmark inputs and outputs are already files | Custom network job protocol (M5 at the earliest) |
| Stutter measurement | PresentMon, release tests only | Measures frame time directly | Runtime frame-time sensing |

## Monitoring under load

Every decision is always recorded to the event log, but watching it live happens on a second machine by default, because the Rerun viewer otherwise uses up to 75% of system RAM ([Rerun: limit RAM](https://rerun.io/docs/howto/limit-ram)).

- **Remote viewer by default.** `sae-telemetry` streams over gRPC to Rerun on another machine, with server buffering bounded by `--server-memory-limit` ([Rerun CLI](https://docs.rerun.io/dev/reference/cli/)). Pin the Rerun version and re-check every flag in M1.
- **Local viewer for developers only**, always with `--memory-limit 512MB`.
- **Recordings.** Write `.rrd` files to review after the session.

| Tier | Content | When |
| --- | --- | --- |
| T0 Always | Observations, belief updates, strategy choices, intents, acks, outcomes, control commands, and a monthly `health` event (Governor state and level, trigger, RAM, paging, slowdown, daemon and parser cost, skipped parses) | Every state, including Ironman |
| T1 Scalars | T0 streamed to Rerun as time series | H0–H1 with a viewer connected |
| T2 Full | God-view data, per-parse feature dumps | H0, dev preset only |

Every Governor transition, budget breach, intent, ack and skipped parse is recorded at any level, so you can always explain why SAE went quiet. Disk limits: event log ≤ 100 MB per 200-year campaign; god-view ≤ 300 MB, pruned by compaction.

## Budgets and acceptance tests

The headline budget is game slowdown of at most 2% on average (upper 95% bound 3%) on your real playset, with no increase in paging.

| Component | CPU (% of one core) | RAM | Other |
| --- | --- | --- | --- |
| Mod script | ≤ 1% of seconds per game year (about 10–40 ms per monthly tick late game) | Save growth ≤ 1% | Staggered emits |
| sae-daemon | ≤ 2% averaged over 60 s | ≤ 150 MB | Polls no faster than every 2 s; logs a breach above 150 MB, restarts itself only above 300 MB |
| sae-parse | Playing: 1 thread; paused: ≤ min(N−2, 4); closed: N−2 | ≤ 1 GB, killed on breach; 2 GB only when the game is closed | Starts ≥ 8 s after the autosave closes |
| sae-telemetry | ≤ 2% | ≤ 150 MB | Off for players by default |
| GPU / VRAM | — | 0 bytes | Manifest rule |
| Whole system | Slowdown ≤ 2% mean, ≤ 3% upper bound | No increase in hard faults | p99 frame time ≤ +5% |

CI tests need no game and run on Linux. Release gates run on the second machine against your 30+ mod playset at year 2350 or later.

| ID | Tests | Pass | Kind |
| --- | --- | --- | --- |
| CI-1 | Backing off changes timing, not decisions: replay at forced H0 vs H2 | Same commitment window in ≥ 90% of cases | CI |
| CI-2 | Pause work never affects decisions: replay with the queue on/off and random kills | Identical intents | CI |
| CI-3 | Governor logic on synthetic traces (drift, speed change, RAM ramp, fault spike) | Drift stays ≤ H1; RAM reaches H2 within 10 s; no oscillation under 6 months | CI |
| CI-4 | Footprint and manifest | No forbidden imports; daemon ≤ 150 MB; zero GPU packages | CI |
| CI-5 | Parser contract and memory cap on a pathological save | Valid JSON with cost fields; killed at cap; daemon falls back to log-only | CI |
| M1-P | Can pause be detected from the game's CPU-time rate? | Separable bands; else pause work runs only when closed | M1 experiment |
| R-1 | Overhead A/B: 10 game years, no SAE vs SAE, 5 interleaved runs each | Mean ≤ 2%, upper bound ≤ 3% | Release gate |
| R-2 | Stutter, measured with PresentMon during R-1 | p99 frame time ≤ +5% | Release gate |
| R-3 | Mod-script cost scaling test: quiet, emit every month, 5× emits | Shipped config ≤ 1% of seconds per game year | Release gate, first run in M0 |
| R-4 | Starved machine: a balloon process leaves \~1.2 GB free | No extra game paging; Governor reaches H2 within 10 s; acks keep flowing | Release gate |
| R-5 | Windows Job and pause behaviour | Parser killed at 1 GB; deferred parse finishes while paused; suspend or kill on unpause within 2 s | Release gate |
| R-6 | 4-core, 8 GB laptop with integrated GPU | Loop still runs; settles at ≤ H2 within R-2's stutter bound | Release gate |

## Review log

Opus 5.5 and Fable 5.1 agreed from the start on no GPU for players and on mod-script cost being the budget that matters; they disagreed on 15 implementation details, settled below.

| Issue | Opus 5.5 | Fable 5.1 | Resolution |
| --- | --- | --- | --- |
| GPU | Not through M4; design a surrogate seam now | Not through M4 | No GPU through M4; M5 on a second machine, no seam or stubs |
| Daemon language | Python | Rust binary | Python with import whitelist; Rust only if R-1 blames the daemon |
| Parser | Separate Rust exe | PyO3 or standalone | Separate exe (Fable conceded) |
| Parser priority | Background mode set by the daemon | Idle + EcoQoS | Idle at spawn; parser sets background mode on itself (Windows requires it) |
| Job Objects | Daemon and parser Jobs, hard CPU cap | Parser memory cap | Parser-only Job; daemon self-monitors |
| Governor shape | Six-level pressure ladder | Four modes | Game state × health level |
| Slowdown trigger | +3/5/10% steps | +25% vs 12-month baseline | Median of 6 vs 24 months ≥ 10%; can never push past H1 alone |
| Parse start | Save stable for 2 s | ≥ 8 s after close | Both |
| Back-off order | Skip saves and cut threads together | Skip saves first | Skip saves first |
| Pause detection | Missing log line + CPU drop | Read from log or save | Game CPU-time rate, tested in M1-P; Fable's sources don't exist |
| Pause compute | ≤ 2 cores | N−2 cores, 2 GB parser | min(N−2, 4) cores; 2 GB only when the game is closed |
| Mod-script metric | ≤ 1% by A/B | ≤ 15 ms per tick from the log | Scaling test reported as % of game year; no tick timer is known to exist |
| Emit staggering | Country index mod period | — | Per-country slot counter assigned on first emit |
| Second-machine link | msgpack over TCP with TLS | rsync or HTTP | Files and rsync; no network protocol before M5 |
| Ironman | Not addressed | Players mostly use Ironman | No live loop; adapt between sessions |

Factual fixes from the cross-review: the dev diary's 31% is a full-run average, not the 2200-start figure; background mode is self-set only; Fable's sign-off confirmed the Job nesting rule from Microsoft's docs, and Rerun's `--serve` flag remains unverified.

## Roadmap changes and open decisions

M0 gains one gate that can stop everything: if the bare mod already costs more than 1% of seconds per game year on your playset, redesign emission before writing engine code. Fable's bottom line: build it, expect H1 (sparse parsing) to be the normal late-game level on 16 GB — that is the design working — and add the monthly heartbeat before M1.

&#91;embedded content: roadmap deltas · M0 to M5 with gates\]

Each milestone keeps its scope from the earlier report; the bands list only what this resource work adds, and M5 stays dashed because it may never open.

Cuts a solo developer can take until data justifies them: the parser's CPU hard cap, CPU-set affinity tuning, the in-game self-A/B, the whole paused-state work queue (until M2 shows how much time is paused), the M1 telemetry process, and Linux/Proton equivalents.

### Open decisions

1. **Second machine.** Do you have one? Release gates and M4 need it, play does not. Without it, gates run overnight on the gaming PC and M4 runs one game at a time.
2. **Default player preset.** Balanced (full parsing, pause work) or Low impact (sparse parsing, no pause work, no fan noise while paused).
3. **Ironman posture.** If Ironman ships, its budget is parse-only and adaptation happens between sessions.
4. **M5.** Default is never opened unless M4 data justifies a surrogate simulator.

### Top risks

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Mod script cost is beyond any Governor's reach | Medium | High | Longer emit period, fewer managed countries; R-3 in M0 |
| RAM: a late-game modded save parse may reach 3–4 GB (HYPOTHESIS) | Medium | Medium | Section-selective parsing measured in M0-11; Job cap turns it into a skipped parse |
| Pause can't be detected on some machines | Medium | Low | Pause work only when the game is closed |
| Slowdown signal is noisy | Medium | Low | 6/24-month medians, hysteresis, H1 cap, CI-3 |
| Scope creep: "we have a GPU, use it" | High | Medium | Zero-GPU-bytes rule, CI-4, M5 gate |

### Sources

[Dev Diary #410](https://admin-forum.paradoxplaza.com/forum/developer-diary/stellaris-dev-diary-410-4-3-performance-comparisons.1899301/page-6) · [Steam A](https://steamcommunity.com/app/281990/discussions/0/1741102632995174015) · [Steam B](https://steamcommunity.com/app/281990/discussions/0/3825286438457734441) · [SetPriorityClass](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass) · [Job CPU rate control](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information) · [Windows QoS](https://github.com/MicrosoftDocs/win32/blob/docs/desktop-src/ProcThread/quality-of-service.md) · [EcoQoS](https://devblogs.microsoft.com/performance-diagnostics/introducing-ecoqos/) · [ORT DirectML](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html) · [JAX install](https://docs.jax.dev/en/latest/installation.html) · [cupy-cuda12x](https://pypi.org/project/cupy-cuda12x/) · [Rerun: limit RAM](https://rerun.io/docs/howto/limit-ram) · [Rerun CLI](https://docs.rerun.io/dev/reference/cli/)
