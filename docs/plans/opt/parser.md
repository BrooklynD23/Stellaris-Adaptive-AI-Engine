# Parser optimisation review (T3, `parser/`)

Reviewer: parser specialist, 2026-11-14. Read-only: no code, contract or other doc was changed. Builds on
`docs/plans/ARCH_OPTIMIZATION_REVIEW.md` ("the baseline"). Per the Fable gate, `sae-adversary` reviews this before
any wave.

## 1. Verdict

1. On Windows the shipped `--background` flag is the parser's largest cost. It caps the working set at 32 MiB, doubles
   page faults, adds 28 % CPU, and makes `rss_peak_bytes` report 33.6 MB for a parse that commits 103 MB.
2. Fix that first: 3 lines plus a contract proposal. All other parser work stays frozen until M0-11, as the
   baseline says.
3. If M0-11 then shows a large real save, build the streaming filter with zlib-rs: RSS -72 %, wall -27 % on the
   synthetic save, pure Rust, no contract change.

## 2. Ranked recommendations

Do now:

| # | Change | Path | Gain (method) | Effort (h) | Risk | Contract | Team |
|---|---|---|---|---|---|---|---|
| 1 | Replace `PROCESS_MODE_BACKGROUND_BEGIN` with `THREAD_MODE_BACKGROUND_BEGIN`; keep idle class and EcoQoS | `parser/src/background.rs:22` | Measured, Windows 11, n=15: CPU 0.431 to 0.336 s (-22 %), kernel 0.145 to 0.069 s, page faults 57,012 to 28,880, peak working set truthful again (33.6 to 107.0 MB; commit 103.0 MB). Wall unchanged (0.56 s) | 3 | Low. UNVERIFIED on the game PC (A-28). If rejected, switch `cost.rs:56` to `PeakPagefileUsage` instead | Proposal: C4 §1 (line 33), C7 §8 (line 132) | T3 + T5 |
| 2 | Make `sae bench parse` pass `--background`; do not size the memory cap from Windows `rss_peak_bytes` until 1 lands | `src/sae/bench/cli.py:59` | Correctness. Production always passes the flag (`src/sae/daemon/saveparser.py:142`); bench never does, so M0-11 would measure 0.53x the production CPU. Baseline rec 8 would set the cap to 67 MB, under the 103 MB commit: every parse would abort | 1 | None | None | T8 |
| 3 | Meta-only fast path: skip `gamestate` when the `meta` entry has date and version (baseline rec 7, now measured) | `parser/src/input.rs:217-231` | Measured. Linux: 0.236 to 0.007 s, RSS 99.9 to 14.9 MB. Windows shipped mode: wall 0.512 to 0.088 s, commit 102.9 to 13.5 MB. Also speeds `sae bench spy` (one probe per save) | 2 | Low: a corrupt `gamestate` no longer fails the probe | Proposal: C4 §1 (line 29) | T3 |

After M0-11, only if a real save commits over 250 MB or takes over 1 s in shipped mode:

| # | Change | Path | Gain (method) | Effort (h) | Risk | Contract | Team |
|---|---|---|---|---|---|---|---|
| 4 | Streaming filter: inflate through a 256 KiB window and keep only depth-0 bytes plus the blocks the extractor reads | `parser/src/input.rs:233-262`, new `filter.rs` (about 170 lines) | Measured with a prototype. Linux n=25: RSS 99.9 to 28.1 MB (-72 %), wall 0.271 to 0.238 s, page faults -73 %. Windows shipped mode: commit 103 to 40 MB, CPU -25 %, wall -21 %. `pathological_2x`: 265 to 3.5 MB | 12 | Medium: a second lexer must agree with `split.rs`. Needs a differential fuzz test. Share of needed sections in a real save is UNVERIFIED (16 % here) | None (C4 §6 still holds) | T3 |
| 5 | zlib-rs inflate backend | `parser/Cargo.toml:13` | Measured. Linux wall 0.264 to 0.239 s; with 4: 0.192 s. Output identical | 1 + Windows CI | Low | None | T3 |
| 6 | Skip taping countries without the bytes `sae_sid` (baseline rec 10) | `parser/src/extract.rs:607-611` | Measured: countries stage 0.0285 to 0.0147 s (-4 % CPU) | 1 | Low; see §5 | None | T3 |
| 7 | Lower `MAX_SPAN` to 16 MiB and correct the "10x" memory claim | `parser/src/extract.rs:23-25`, `parser/README.md:59` | Measured on Windows: a dense 32 MiB span commits 1,003 MB (30x). With a 399 MiB text: 1,388 MB, over the 1 GiB cap, so an abort instead of exit 4. At 16 MiB the worst case is about 920 MB | 1 | Low: hostile input only; real country block size is UNVERIFIED | None | T3 |

## 3. Evidence

### 3.1 Method

- Save: the baseline's synthetic one, 89 MB text, 12.6 MB zip. Layout UNVERIFIED against a real 4.4 autosave
  (A-18, A-19).
- Linux: WSL2, release build, `-j 1`, medians of 25 interleaved runs.
- Windows: the prototype cross-compiled for `x86_64-pc-windows-gnu` and run on this development machine's Windows 11
  host (build 26200, Core Ultra 7 258V) through WSL interop. It ran only my binary on the synthetic save. Stellaris
  was not touched. The game PC is still UNVERIFIED.
- Prototype: session scratchpad `opt/parser/proto/` (`src/filter.rs`). Not in the repo.

### 3.2 Windows, 89 MB save, n=15 interleaved

| Mode | CPU s | Kernel s | Page faults | Peak working set MB | Commit MB | Wall s |
|---|---|---|---|---|---|---|
| No flag | 0.227 | 0.061 | 29,836 | 107.0 | 103.0 | 0.319 |
| Process background, no EcoQoS | 0.295 | 0.109 | 56,372 | 33.6 | 102.9 | 0.380 |
| Shipped: process background + EcoQoS | 0.431 | 0.145 | 57,012 | 33.6 | 102.9 | 0.564 |
| Thread background + idle + EcoQoS | 0.336 | 0.069 | 28,880 | 107.0 | 103.0 | 0.559 |
| Streaming filter, shipped mode | 0.324 | 0.046 | 14,144 | 33.6 | 40.1 | 0.448 |

- 33.6 MB is 32 MiB: the cap is real on this build. `parser/src/cost.rs:56` reads `PeakWorkingSetSize`, so the
  reported peak is 3.1x too low.
- EcoQoS alone adds about 0.10 s CPU: the intended trade.
- No page-file writes seen: 0 pages output per second across 14 shipped-mode parses, with 3.9 GB available.

### 3.3 Linux, 89 MB save

| Variant | Wall s | CPU s | RSS MB | Minor faults |
|---|---|---|---|---|
| Today | 0.271 | 0.254 | 99.9 | 25,160 |
| Streaming filter | 0.238 | 0.219 | 28.1 | 6,745 |
| zlib-rs + streaming filter + `sae_sid` prefilter | 0.179 | 0.171 | 28.2 | 6,737 |
| LTO + `codegen-units = 1` | 0.249 against 0.253 | no gain | 99.9 | - |

- Inflate alone, best of 7: miniz_oxide 578 MB/s into a fresh 89 MB buffer and 884 MB/s through a reused window;
  zlib-rs 747 and 1,355 MB/s; libdeflate 1,194 to 1,281 MB/s (whole buffer only).
- Streaming prototype: output identical on the 89 MB save; all 83 parser tests pass with it forced on.
- Cost of a future feature: a field inside `country` is free (the block is already taped). A new top-level section
  costs about 2.2 ms per MB (13.1 MB of countries in 0.0285 s) plus its size in RSS.
- jomini reserves one 24-byte token per 5 text bytes (`jomini-0.37.2/src/text/tape.rs:132`); the synthetic country
  block uses 3.8 tape bytes per text byte, so real blocks should not regrow (UNVERIFIED).

### 3.4 Where I disagree with the baseline

1. §5 "streaming inflate costs the stat-read-stat check": it does not. The zip is still read whole and checked
   (`parser/src/input.rs:88-116`); only the inflate is streamed.
2. §4.1 "inflate is 70 % of the parse": about 0.05 s of that 0.16 s is first-touch page faults on the output buffer.
3. Rec 8 and the wave B gate use numbers that are wrong on Windows today (row 2).
4. Rec 9: zlib-rs output equality is now checked.

### 3.5 Sources

| Claim | Source |
|---|---|
| Process background mode caps the working set at 32 MiB (undocumented); Chrome removed it | https://randomascii.wordpress.com/2023/10/01/32-mib-working-sets-on-a-64-gib-machine/ |
| Firefox declined the mode for the same reason | https://bugzilla.mozilla.org/show_bug.cgi?id=1476365 |
| Background threads "should use" `THREAD_MODE_BACKGROUND_BEGIN`; idle CPU priority alone is not enough | https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setthreadpriority |
| `PeakPagefileUsage` is the peak commit charge | https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters |

## 4. Do-not-do list

| Idea | Why it loses |
|---|---|
| libdeflate | Slower than zlib-rs through a window, needs a C compiler in the Windows build, cannot stream |
| LTO, `codegen-units = 1` | Measured: no gain |
| Removing EcoQoS | Saves 0.10 s CPU per parse but puts the work back on the cores the game uses |
| rayon, binary C4 output, mmap, persistent parser, SIMD splitter | Agree with baseline §5. Output is 7.8 KB |
| A deeper filter inside `country` | Wait for M0-11; the one-level filter already gives -72 % |

## 5. Needs maintainer decision

| Topic | Touches | Benefit | Cost | Recommendation |
|---|---|---|---|---|
| Thread background mode (row 1) | Frozen C4 §1 and C7 §8 | -22 % CPU, half the page faults, truthful memory figure | One proposal; a Windows check in M0 R-5 | Approve |
| Several parser processes for bulk re-parse of recorded saves | "Three processes", only while the game is CLOSED; replaces the rayon backlog item (`docs/TEAM_PLAN.md:107`) | Linear speed-up for learning the player profile from saves, with no parser code | 40 to 103 MB per process while CLOSED | Decide when the profile work needs bulk re-parse |
| `sae_sid` prefilter (row 6) | Exit-code behaviour | -4 % CPU | A malformed country without `sae_sid` stops being exit 5 | Only if M0-11 shows the country block dominates |

Next action: send row 1 to `sae-adversary` with the Windows table in §3.2.
