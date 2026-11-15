# Architecture optimisation review

Reviewer: lead architecture consultant, 2026-11-14. Scope: speed, latency, memory, CPU share and adaptiveness of the
daemon, parser and bus. Read-only review: no code, contract or other doc was changed. Per the Fable review gate, this
plan needs a `sae-adversary` review before any wave starts.

Measured on WSL2 (6 cores, ext4 scratch disk, Python 3.12.3, rustc release build). No real Stellaris save exists in
the repo, so every parser number comes from a synthetic save whose layout is UNVERIFIED against a real 4.4 autosave
(A-18, A-19). Windows-specific costs (fsync, process spawn, EcoQoS) are UNVERIFIED and labelled where used.

## 1. Verdict

1. **Biggest wins:** the engine is not CPU-bound anywhere. The one real cost is `fsync` (3 per game month, 69 % of a
   synth run's wall time). After that, the gains are in adaptiveness (an unused feature, weak switch signal), not speed.
2. **Concurrency stance:** keep one asyncio thread plus `to_thread` for blocking file I/O, and one single-threaded
   parser process per autosave. Add no threads, no process pool and no rayon while the game is running.
3. **Risk:** the main risk is over-engineering the parser (rayon, SIMD, binary output) for a 0.23 s job. Freeze parser
   speed work until M0-11 measures a real save.

## 2. Ranked recommendations

Effort is implementation plus tests. "Proposal" means a file in `contracts/proposals/`.

| # | Change | Component / path | Expected gain | Effort (h) | Risk | Contract impact | Team |
|---|---|---|---|---|---|---|---|
| 1 | Rate-limit `fsync` by wall clock (at most one per file per 30 s, plus on close); no per-month `fsync` in REPLAY/SYNTH | `src/sae/bus/writer.py:173-176`, `src/sae/daemon/statefile.py:23-30` | Measured: 30-year synth run 18.1 s to 5.6 s (-69 %). Live: 3 device flushes per game month become at most 3 per 30 s. Also speeds every replay-based test, gate and tune run about 3x | 3 | Low: a power cut loses up to 30 s of bus; a process crash still loses nothing (writes are unbuffered) | None. C5 §1 says "`fsync` at most once per game month" | T6 + T4 |
| 2 | Add per-stage timings to the parser's stderr (`level=debug`) and run `sae bench parse` on a real save at M0-11. Do no other parser speed work before that | `parser/src/main.rs:76-110`, `parser/src/input.rs`, M0 kit | Decision evidence. Avoids an estimated 40+ h of rayon/SIMD work that the synthetic numbers do not justify | 2 | None | None (stderr is free-form, C4 §2) | T3 + T8 |
| 3 | Feed `economy_cat` into the belief. The parser computes it, the bus records it, only the offline profile reads it | `src/sae/core/features.py:12-18`, `src/sae/core/belief.py:76-90`; evidence `src/sae/profile/summary.py:103` | One more fog-legal feature (5 to 6) at zero parse cost. Size of the gain is unknown until V-1 (ECE ≤ 0.10) | 8 | Medium: likelihoods are HYP until fitted on labelled campaigns | Proposal (C10 likelihood tables, new PolicyVersion) | T4 + T9 |
| 4 | Add trend features (change of `fleet_power_cat`, `tech_cat`, border ratio between saves) and widen the outcome reward's range | `src/sae/core/features.py:27-37` (`FeatureMemory` keeps only system counts today) | Targets V-3: today 1 switch and 99 rewards within ±0.02 in a 200-year synth run. Gain unproven until V-3 runs on real recordings | 14 | Medium | Proposal (C10; C5 if payload keys change) | T4 + T9 |
| 5 | Cache per-run summaries in `sae profile update` (key: bus size + mtime) | `src/sae/profile/` | Update cost goes from linear in all runs to linear in new runs. Review 3 already flags the full rescan | 5 | Low | None | T9 |
| 6 | Throttle the `runs/` size scan to once per 10 min of wall time and run it in `to_thread` | `src/sae/daemon/loop.py:356,383-397`, `src/sae/daemon/disk.py:13-28` | Removes a recursive scan that blocks the loop once per game month. 0.12 ms for one run today; grows with every run and recording | 1 | Low | None | T4 |
| 7 | Ironman meta probe: inflate `gamestate` only when the `meta` entry lacks date or version | `parser/src/input.rs:217-231` | Measured meta-only parse: 0.29 s and 102 MB RSS, down to an estimated 0.01 s and about 15 MB. Once per session | 2 | Low | Proposal (C4 §1 says the probe still inflates `gamestate`) | T3 |
| 8 | After M0-11, set `worker_mem_cap_bytes` to 2x the measured real peak instead of 1 GiB | governor config | Tighter worst case next to the game. Synthetic peak is 102 MB for an 89 MB save | 1 | Low: too tight a cap means skipped parses | None (C7 §10: numbers are config) | T5 |

Later, only if M0-11 shows a real parse above 1 s or 500 MB:

| # | Change | Measured or expected gain | Effort (h) | Contract | Team |
|---|---|---|---|---|---|
| 9 | Switch inflate to zlib-rs (`flate2` feature) | Measured: inflate 0.16 s to 0.13 s, wall 0.23 s to 0.20 s (-13 %) | 1 + Windows build check | None | T3 |
| 10 | Skip taping countries whose span has no `sae_sid` bytes | Countries stage is 0.026 s of 0.23 s on synthetic data; real country blocks are larger (UNVERIFIED) | 2 | None | T3 |

## 3. Concurrency vs parallelism matrix

| Component | Today | Should use | Why | Where parallelism would hurt |
|---|---|---|---|---|
| Log tailer | 2 s poll, incremental read in `to_thread` (`adapters/live.py:189`) | Same | I/O-bound, at most 1 MiB per poll. asyncio has no file watching on Windows | A dedicated thread or `ReadDirectoryChangesW` adds wakeups and failure modes for no latency the engine can use |
| Save watcher | 2 s poll: `listdir` + `stat` (`adapters/savewatch.py:65-96`) | Same | The 8 s age rule dominates latency by design; event-driven watching cannot beat it | Same as above |
| Parser | One process per autosave, one thread, idle priority + background mode + Job cap | Same. Keep `--threads 1` while PLAYING | 0.23 s for 89 MB. Process exit returns all memory | rayon while PLAYING: N threads land on the cores the game uses for its monthly pulse, and each thread holds its own token tape |
| Model update | Synchronous on the consumer task (`daemon/loop.py:346-369`) | Nothing | About 100 float operations per month; whole daemon is under 5 ms CPU per game month | Any thread or process costs more than the work |
| Policy / Governor | 2 s asyncio task (`daemon/loop.py:190-196`) | Same | `sensors.sample()` measured at 0.08 ms on Linux (Windows PDH path UNVERIFIED) | - |
| Intent writer | Atomic write in `to_thread` with retry (`adapters/ini_writer.py`) | Same | A few writes per 24 months | - |
| Bus writer | Synchronous write per event, `fsync` on the loop thread | Synchronous write; rate-limited `fsync` (rec 1) | Validation and serialisation cost 0.2 ms per event | A writer thread adds ordering risk to a gapless-seq log for no gain once `fsync` is throttled |
| Telemetry | Separate optional process tailing the bus | Same | Keeps Rerun out of the daemon (settled) | - |
| Offline tools (`profile`, `tune`, `gate`) | Single process, game closed | Single thread first; processes only if a tune run exceeds its 30 min target | Runs only in CLOSED, so cores are free | Never run them while the game is up (already refused without `--force`) |

Free-threaded Python and subinterpreters buy nothing: the daemon has no CPU-bound Python work to spread.

## 4. Evidence

### 4.1 Top 5 costs

| Rank | Cost | Number | Evidence |
|---|---|---|---|
| 1 | Deliberate wait before a parse | 8 to 12 s per autosave | `adapters/savewatch.py:15-16,113` (2 s stable, 8 s age) plus 2 s poll and up to 2 s result pickup (`adapters/live.py:186-205`). By design (C7 §7); not a defect |
| 2 | `fsync` on the event-loop thread | 3 per game month, 7.6 to 10 ms each here; 8.2 s of a 15.7 s profiled run | cProfile: 1,084 `posix.fsync` calls. Callers: `bus/writer.py:173-176` (bus and god bus on month change), `daemon/statefile.py:29` via `daemon/loop.py:368-369`. Windows cost UNVERIFIED |
| 3 | Inflate | 0.16 s of 0.23 s (70 % of the parse), about 560 MB/s | Stage timers in a scratch copy of the parser. Backend is miniz_oxide (`parser/Cargo.lock`: `flate2` depends on `miniz_oxide`; zip 2.6.1 `deflate` = `flate2/rust_backend`) |
| 4 | Parser peak memory | 102 MB RSS for 89 MB text + 12.6 MB zip | Compressed buffer and inflated text coexist (`parser/src/input.rs:103-128,250-254`). Ten times under the 1 GiB cap |
| 5 | Split + extract | 0.04 s top-level split, 0.03 s extraction | `parser/src/split.rs:58-88`, `parser/src/extract.rs:577-619`. The parser already tapes only needed spans |

Not a cost: pydantic. 16,478 validations took 0.26 s in total; output JSON from the parser is 7 KB.

### 4.2 Measurements

| Test | Result |
|---|---|
| Parse, 89 MB synthetic save (12.6 MB zip), 3 runs | wall 0.23 to 0.24 s, user 0.15 to 0.18 s, RSS 102 MB |
| Parse, 32 MB synthetic save | wall 0.10 s, RSS 39 MB |
| Same save as plain text (no inflate) | wall 0.11 s, RSS 91 MB |
| Meta-only parse (`--sections meta`) | wall 0.29 s, RSS 102 MB |
| zlib-rs variant | inflate 0.12 to 0.14 s, wall 0.19 to 0.21 s |
| `sae synth --archetype turtle --years 30 --seed 2`, 2 runs | wall 18.9 s and 17.3 s; user 1.8 s; RSS 45 MB |
| Same with `os.fsync` replaced by a no-op, 2 runs | wall 4.4 s and 6.7 s |
| Daemon import graph | 0.88 s (inflated by the `/mnt/c` mount), RSS 38 MB |
| Bus size, 30 synth years | 1,591 events, 1.71 MB; `observation` 2,360 B, `belief_update` 877 B |
| `fsync` microbenchmark, 50 calls | median 10.3 ms, p90 12.7 ms |

Limits of these numbers: the synthetic save has 120 countries, 190,000 pops and 60,000 ships with invented keys. Real
saves may have larger country blocks and different compression ratios. Output equality between the miniz and zlib-rs
builds was not checked. Scratch scripts and builds lived in the session scratchpad and are not in the repo.

### 4.3 Web sources

| Claim | Source |
|---|---|
| Background mode lowers CPU, I/O and memory priority and can only be set by a process on itself; an idle-priority process can still hurt responsiveness through disk and memory | https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass |
| EcoQoS exists from Windows 11; earlier versions label the process LowQoS | https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessinformation |
| Job `ProcessMemoryLimit` caps committed memory; CPU hard cap stops all job threads until the next interval | https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information , https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information |
| `ReadDirectoryChangesW` discards the whole buffer on overflow and the caller must re-enumerate | https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-readdirectorychangesw |
| asyncio on Windows cannot monitor file I/O with `add_reader` | https://docs.python.org/3/library/asyncio-platforms.html |
| Free-threaded build costs roughly 5 to 10 % single-threaded | https://docs.python.org/3/whatsnew/3.14.html |
| Prefer `model_validate_json`; reuse `TypeAdapter` instances | https://pydantic.dev/docs/validation/latest/concepts/performance/ |
| jomini: "Parse data at over 1 GB/s" (no workload given) | https://docs.rs/crate/jomini/latest/source/README.md |
| memmap2: undefined behaviour if the file is modified while mapped | https://docs.rs/memmap2 |
| LTO can improve speed "by 10-20% or more" (no workload given) | https://nnethercote.github.io/perf-book/build-configuration.html |

Not found: a measured modern `CreateProcess` latency, and a native throughput comparison of miniz_oxide, zlib-rs,
zlib-ng and libdeflate on one machine.

## 5. Do-not-do list

| Idea | Why it loses here |
|---|---|
| rayon in the parser while PLAYING | The parse is 0.15 s of CPU. More threads compete with the game's monthly pulse and multiply tape memory |
| Binary or columnar output across C4 | Output is 7 KB of JSON validated in under 1 ms. A v2 contract for nothing |
| SIMD or `memchr` rewrite of the splitter | The split already runs at about 2 GB/s (0.04 s for 89 MB) |
| Streaming inflate into the splitter | Would cut RSS from about 100 MB to about 35 MB against a 1 GiB cap, at the cost of the simple stat-read-stat stability check (C4 §6) |
| Memory-mapping the save | The game rewrites autosaves; a modified mapping is undefined behaviour, and the file is only 13 MB compressed |
| Persistent parser process or PyO3 extension | Breaks "parser exits per autosave"; saves one process spawn per autosave and keeps the memory spike resident |
| `ReadDirectoryChangesW` or a watchdog thread | Cannot beat the 8 s age rule; adds overflow handling and a directory handle |
| Replacing pydantic (msgspec, dataclasses) on the bus | 0.2 ms per event, about 5 events per game month |
| Free-threaded Python, subinterpreters, multiprocessing in the daemon | No CPU-bound Python work exists; each adds memory against the 150 MiB budget |
| LTO, `codegen-units = 1`, mimalloc | At best 10 to 20 % of 0.07 s of non-inflate work. Revisit only with a real save above 1 s |
| Faster result pickup (event instead of 2 s pump tick) | Saves up to 2 s on a decision that holds for 24 months |
| Incremental recompute of the live player model | It is already incremental (`FeatureMemory`, at most 48 samples) |

## 6. Needs maintainer decision

| Topic | Settled decision it touches | Benefit | Cost | Recommendation |
|---|---|---|---|---|
| CUSUM-triggered early commitment break | CUSUM is diagnostic only; 24-month commitments | Faster reaction when a player changes style mid-window (V-3) | Risk of oscillation; reopens two settled rules | Do not reopen now. Decide after V-3 runs on real recordings and recs 3 and 4 are measured |
| Bandit across campaigns (condition C) | v0/v1 selection is a belief to counter table | Learns which counter works for this player | Needs a reward with real spread first (rec 4) | Keep in backlog until V-3 passes |
| Multi-threaded parse in PAUSED or CLOSED | None directly (rayon is already T3 backlog for M2-M3) | Faster bulk re-parse of recorded saves | 40+ h for a job that takes 0.23 s per save on synthetic data | Drop from the backlog unless M0-11 shows a real save above 1 s |

## 7. Proposed waves

Each wave gets a `sae-adversary` plan review before code and an architecture review after.

| Wave | Recommendations | When | Validation test |
|---|---|---|---|
| A: etiquette and evidence | 1, 6, 2 | Now, game-free | (a) `sae synth --years 30 --seed 2` wall ≤ 8 s on this machine (was 18.1 s). (b) A test counting `os.fsync` calls: ≤ 1 per file per 30 s of injected clock, 0 extra in SYNTH/REPLAY. (c) T6 crash-truncation and CI-1 determinism tests unchanged. (d) Parser stderr shows `read`, `inflate`, `split`, `extract` timings; stdout byte-identical on all fixtures |
| B: real-save gate | 8, then 7, 9, 10 only if triggered | M0 weekend (needs a real autosave) | `sae bench parse <real save> --repeat 5` recorded in `docs/status/`. Triggers: wall > 1 s or RSS > 500 MB opens 9 and 10; otherwise they close as not needed. 7 ships with a C4 proposal and a test that meta-only output is unchanged |
| C: adaptiveness | 3, 4 | M2 (needs labelled campaigns) | V-1: ECE ≤ 0.10 per held-out campaign and correct counter on ≥ 4 of 5, with and without the new features. V-3: more than 1 switch where the player changes style; rewards not all within ±0.02. Both through `sae tune` and `sae gate stage1`, shipped as a new PolicyVersion |
| D: offline throughput | 5 | With wave C | `sae profile update` on a 50-run fixture: second run reads 0 unchanged buses and writes a byte-identical `profile.json` (F3 still green) |

Next action: send this plan to `sae-adversary` for the pre-implementation review, starting with wave A.
