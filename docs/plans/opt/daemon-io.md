# Daemon I/O optimisation review (`src/sae/daemon`, `src/sae/bus`, tailing, watching, spawn)

Reviewer: daemon-io specialist, 2026-11-14. Read-only: no code, contract or other doc was changed. Builds on
`docs/plans/ARCH_OPTIMIZATION_REVIEW.md` ("the baseline"). `sae-adversary` reviews this before any wave.

Measured on WSL2 (ext4, Python 3.12.3) and natively on one Windows 11 PC (build 26200, Python 3.13.7, no Stellaris
installed). LIVE numbers come from the real `sae daemon` fed from a synth recording. Month cadence, log flush
batching, autosave naming and folder counts are UNVERIFIED (A-07, A-24, A-34, A-46). Scripts:
`/tmp/claude-1000/-mnt-c-Users-DangT-Documents-GitHub-Stellaris-Adaptive-AI-Engine/d4efc1c5-69e5-4ddc-879f-1e378d064932/scratchpad/opt/daemon-io/`.

## 1. Verdict

1. **The biggest win is a correctness fix.** The seconds-per-month (spm) trigger fires on timing noise. The real
   daemon went to H1 at a constant 1 s and 3 s per month, then skipped 7 of 21 saves.
2. **The daemon is already cheap:** 1.0 ms CPU per second idle, 4.5 ms per game month, 42 MB. Two costs grow:
   `fsync` (offline runs) and the save watcher (per campaign folder).
3. **Concurrency:** agree with the baseline. One asyncio thread plus `to_thread`; a hop costs 61 µs on Windows.

## 2. Ranked recommendations

| # | Change | Path | Gain (method) | Effort (h) | Risk | Contract | Team |
|---|---|---|---|---|---|---|---|
| 1 | Sample spm once per poll (`months = k`), as C7 §4.3 says. Take the ratio from window means, only when median(24) ≥ 5 × `poll_s`. Ship both together | `src/sae/daemon/loop.py:335-344`, `src/sae/governor/core.py:169-174` | Measured, real daemon: false H1 at 1 s and 3 s per month; 14 save observations instead of 21. Simulated, real governor, constant speed: up to 96 % of months at H1 today, 0 % after | 4 | Medium: the sampler fix alone gives 95 % false H1 at 1.5 s/month | Sampler: none. Ratio: proposal (C7 §4.3) | T4 + T5 |
| 2 | Bus: `fsync` on a month change only if 30 s passed since the last, plus on close. `state.json`: no `fsync` in SYNTH/REPLAY | `src/sae/bus/writer.py:175-177`, `src/sae/daemon/statefile.py:23-30`, `src/sae/daemon/loop.py:368-369` | Measured, 30-year synth, 3 runs each: 12.2-15.9 s down to 7.2-7.7 s (-45 to -55 %); 1,084 `fsync` calls down to 3. LIVE: 3 device flushes per game month down to 1 | 3 | Low: a power cut loses up to 30 s of bus | None | T6 + T4 |
| 3 | Save watcher: poll only the hot campaign folder; rescan all folders every 15th poll | `src/sae/adapters/savewatch.py:65-121` | Measured: daemon idle CPU 1.0, 4.0, 18.3 ms/s at 1, 60, 300 folders. Prototype: -77 % at 20 folders, -83 % at 60 and 300 | 4 | Medium: after a campaign switch the first save arrives up to 36 s late (was 8 s) | None | T2 |
| 4 | `sae telemetry` lowers its own priority at start, like the daemon | `src/sae/telemetry/cli.py:44` (today only `src/sae/daemon/wiring.py:99`) | Not measured. The third process runs at normal priority next to the game | 1 | Low | None | T6 |
| 5 | Check `runs/` size at start only | `src/sae/daemon/loop.py:356` | Measured: 10-14 ms on the loop thread per game month at 200 runs | 0.5 | Low: the warning waits for the next start | None | T4 |

Owned by other reports, confirmed here:

- `--background` pins the worker's working set at 32 MiB (`docs/plans/opt/parser.md` row 1). A Python stand-in
  agrees: page faults 46,000 to 172,000.
- Synth keeps every fixture spec (`docs/plans/opt/engine-core.md` row 5). The LIVE daemon does not grow.

Validation:

1. Row 1: two HB lines in one poll give one sample with `months = 2`. The governor stays at H0 for 600 months at a
   constant 3 s/month, and reaches H1 within 8 months of a +15 % step at 31 s/month.
2. Row 2: `tests/unit/t6/test_bus_writer.py:169` with an injected clock.
3. Row 3: a save in a cold folder is reported once, within 36 s.
4. M0: count campaign folders, time `os.fsync`, record s/month on the game PC.

## 3. Evidence

### 3.1 Spm trigger (row 1)

`_spm` makes one sample per observation; the assembler emits one observation per HB line
(`src/sae/adapters/assembler.py:126-128`). Two months in one poll give a second sample near 0.02 s. C7 §4.3 and
`src/sae/contracts/c7_governor.py:150-155` require one sample per poll. The governor then takes medians of
2 s-quantised samples.

| Real daemon run | Result |
|---|---|
| 1 s/month, 60 months | `spm` alternates 0.02 and 2.0 s; H1 (`spm`) at 2202.11; 7 saves skipped (`every_n`) |
| Same, per-poll sampler | `spm` 1.03-1.06 s; no H1; 21 save observations |
| 3 s/month, one month per poll | `spm` alternates 2.0 and 4.0 s; H1 at 2203.05; 3 saves skipped |

Simulation: real `ResourceGovernor`, constant speed, 600 months, 8 phases. Cells are the share of months at H1.

| s/month, log flush | Today | Per-poll sampler only | Per-poll, means, 10 s floor |
|---|---|---|---|
| 1.5, none | 0 % | 95 % | 0 % |
| 3.0, none | 6 % | 6 % | 0 % |
| 4.0, 5 s | 95 % | 25 % | 0 % |
| 7.0, 5 s | 96 % | 96 % | 0 % |
| 11 to 45, any | ≤ 0.1 % | ≤ 0.1 % | 0 % |

A real +15 % slowdown: today 0 of 8 phases see it at 11 and 23 s/month; the fix sees it in 8 of 8 at every speed
tested. H1 sets `parse_every_n = 2`, then 3 (C7 §3).

### 3.2 Other measurements

| Test | Result |
|---|---|
| LIVE daemon, 1 folder | Idle 1.0 ms CPU/s; +4.5 ms CPU per game month; 42.3 MB; 2-3 threads |
| Save watcher | 0.1 ms CPU per folder per poll; 300 folders add 5 MB |
| `fsync`, ext4 | 5.2-8.5 ms each, 3 per game month, on the loop thread |
| Windows: SAE's `spawn_worker` (first native run) | Create plus Job 2.4 ms; create to exit 34 ms median (`hostname.exe`) |
| Windows: `SystemSensors.sample()`, `lower_self_priority()` | 1.2 ms median; class `0x4000` and EcoQoS confirmed set |
| Windows NTFS | `os.stat` 5.6 µs; `os.listdir` 38 µs |

### 3.3 Where I differ from the baseline

- Its rule 1 ("one `fsync` per 30 s") can sync twice in one game month while paused; C5 §1 allows one. Keep the
  month test and add the 30 s test.
- It rate-limits `state.json` too. I keep that `fsync` in LIVE: it protects the rename. SYNTH and REPLAY cannot
  resume (`src/sae/daemon/cli.py:110-111`), so skip it there.
- I measured -45 to -55 %, not -69 %.
- Its row 6 adds a timer and a thread; dropping the monthly call is smaller.

### 3.4 Sources

| Claim | Source |
|---|---|
| Process background mode trims the working set to 32 MiB | https://randomascii.wordpress.com/2023/10/01/32-mib-working-sets-on-a-64-gib-machine/ |
| `FlushFileBuffers` "can be inefficient when used after every write" | https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers |
| A directory listing shows a stale size for a file still open for writing | https://devblogs.microsoft.com/oldnewthing/20111226-00/?p=8813 |
| ext4 forces data to disk on replace-by-rename | https://www.kernel.org/doc/Documentation/filesystems/ext4.txt |

Not found: NTFS behaviour of an unflushed rename after power loss; when a directory's last-write time changes.

## 4. Do-not-do list

Measured, no gain:

| Idea | Why it loses |
|---|---|
| Tailer and watcher in one `to_thread` hop | Idle CPU 1.00 ms/s before and after |
| pydantic `defer_build` or lazy imports | -0.6 MB of 39.6 MB |
| `stat` before opening `game.log`; byte filter for noise | Idle poll 9.6 µs; 1 MiB of noise costs 3.9 ms |

Would break something:

| Idea | Why it loses |
|---|---|
| `os.scandir` sizes in the save watcher | Stale for a file being written; breaks the stability rule |
| Skip folders by directory time | No primary source; an in-place rewrite would be missed |
| Rate-limit the whole `state.json` save | A stale state would reuse intent `seq` numbers after `--resume` |

## 5. Needs maintainer decision

| Topic | Settled decision it touches | Benefit | Cost | Recommendation |
|---|---|---|---|---|
| No spm trigger below 10 s/month | Architect rule in C7 §4.3 | 0 false H1 | No spm protection at fast speed; RAM and fault triggers stay | Approve by proposal |
| Drop the `state.json` `fsync` in LIVE | None; `--resume` durability | The last per-month device flush goes | After a power cut on NTFS, resume may fail | Keep until M0 times `os.fsync` |
| Persistent parser process | Parser exits per autosave | Saves a 34 ms spawn per autosave | The memory spike stays resident | Do not reopen |

Next action: send row 1 to `sae-adversary` with the two tables in 3.1.
