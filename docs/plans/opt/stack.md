# Stack conversion review (opt/stack)

Reviewer: stack specialist, 2026-11-14. Read-only: no code, contract or other doc changed, no git command run.
Builds on `docs/plans/ARCH_OPTIMIZATION_REVIEW.md`. Needs a `sae-adversary` review before any wave (Fable gate).

Measured on WSL2 (6 cores, ext4 scratch, CPython 3.12.3, pydantic 2.14.3; the lock pins 2.14.1). Sizes are MiB
from `/proc`. Every Windows figure (working set, Defender, spawn cost) is UNVERIFIED. The "running game" was a dummy
`sleep` binary named `stellaris` (process name UNVERIFIED, A-43). Stellaris was never launched.

## 1. Verdict

1. **Don't convert.** Keep Python 3.12 + pydantic for the daemon and Rust for the parser. A full Rust daemon saves
   at most 39.6 MiB and under 0.1 % of one core, for an estimated 10 to 16 weeks.
2. **No partial conversion pays.** msgspec saves about 13 MiB (estimate), mypyc about 8 µs per belief update; Python
   3.13, 3.14, the JIT and free-threading save nothing or cost memory.
3. **Flip evidence:** a Windows M0-12 run showing daemon private working set above 100 MiB, sustained CPU above 1 %
   of a core, or engine work above 50 ms per game month. The first lever then is msgspec on the contracts, not Rust.

## 2. Options weighed

Effort is an estimate from line counts (live daemon graph: 63 modules, 8,999 lines; 68 of 112 test files import it).
A negative saving means the option is worse.

| Option | RSS saved | CPU / latency saved | Startup | Field kit impact | Contributors | Effort | Risk |
|---|---|---|---|---|---|---|---|
| **(a) Python 3.12 as-is (keep)** | 0 (41.6 MiB, 28 % of the 150 MiB limit) | 0 (0.07 to 0.11 % of a core idle) | 0.33 s | None | One language for engine and offline tools | 0 | None |
| (b) msgspec/orjson for contracts | About 13 MiB (estimate, same-shape proxy) | About 0.1 ms per game month | -0.19 s | One compiled wheel swapped for another | Schema export and mypy plugin rewritten | 2 to 3 weeks | High: all 10 frozen contracts |
| (c) Python 3.13 / 3.14 / JIT / free-threaded | 3.13: -1.1 MiB peak. 3.14: -3.3. Free-threaded: -10.4 | None. JIT slowed `belief.update` | 3.13: -28 ms | One version pin | None | 4 h | Low |
| (d) mypyc/Cython hot modules | 0 | 33 to 39 µs down to 27 to 29 µs per belief update | 0 | Wheel per Python and OS; CI needs a C compiler | C toolchain to test compiled mode | 1 week | Medium |
| (e) Daemon core in Rust via PyO3 | At most 14 MiB (estimate; the 24.4 MiB interpreter floor stays) | Microseconds | At most -0.19 s | One wheel per OS; Python still needed. "One binary" needs the parser in-process (settled: no) | Rust for every belief change; `tune` and `gate` use bindings | 6 to 8 weeks | High |
| (f) Full Rust daemon (Go not measured; third toolchain) | 39.6 MiB (skeleton: 2.0 MiB) | 0.05 to 0.09 % of a core, 1.2 wakeups/s | 0.33 s to about 3 ms | No install gain: 7,166 lines outside the live graph (`sae m0`, `profile`, `tune`, `gate`) stay Python | Engine exists twice, or offline tools bind to Rust | 10 to 16 weeks | High: stalls adaptiveness wave C; Windows UNVERIFIED |

## 3. Ranked changes

| # | Change | Path | Gain (method) | Effort (h) | Risk | Contract | Team |
|---|---|---|---|---|---|---|---|
| 1 | Record "no stack conversion" with the flip thresholds | this file | Avoids 10 to 16 weeks for 39.6 MiB (measured ceiling) | 0 | None | None | architect |
| 2 | Capture daemon working set and CPU on Windows at M0-12 from the bus `health` metrics | `docs/m0_checklist.md:39`; source `src/sae/daemon/loop.py:265` | The only evidence that can flip the verdict. No code | 1 | None | None | T8 |
| 3 | Correct the download host in the consent text | `tools/m0/field_kit/setup.ps1:58`, X2 plan D9 | uv 0.13.1 resolves the Windows CPython 3.12 archive from `releases.astral.sh` (HTTP 200, no redirect; resolved on Linux, Windows UNVERIFIED), not `github.com`. An allowlist built from the text would block it | 0.5 | Low | None | T8 |

Later, only on evidence:

| # | Change | Trigger | Gain | Effort (h) | Contract | Team |
|---|---|---|---|---|---|---|
| 4 | Offline kit: bundle uv, the CPython archive and wheels | Install failures in the M0 weekend | Removes 3 hosts and 40.2 MB of downloads (15.3 + 22.0 + 2.87; HEAD requests and `uv.lock`) | 6 to 8 | None | T8 + T7 |
| 5 | msgspec for the contract models | Windows working set above 100 MiB | About 13 MiB, 0.19 s (estimate) | 80 to 120 | Proposal, all contracts | architect |
| 6 | Low memory priority on the daemon's own process, as an R-5 experiment | Hard faults traced to the daemon | Unknown; may raise faults for a process waking every 2 s | 3 | Proposal (C7, additive) | T5 |

## 4. Evidence

### 4.1 Measurements

| Test | Result |
|---|---|
| Live daemon, steady 60 s | 41.6 MiB RSS (26.3 anonymous), 2 threads, 1.7 wakeups/s. CPU 0.04 s with the dummy game (PLAYING), 0.06 s without (CLOSED) |
| RSS by layer (one process) | Interpreter 9.1, stdlib 13.8, asyncio 2.6, pydantic 6.5, psutil 0.9, 100 contract models 6.3, other SAE 1.4 = 40.6 MiB |
| Import of the daemon graph, median of 9 | 3.12: 247 ms, 39.3 MiB. 3.13: 219 ms, 39.3. 3.14: 259 ms, 41.6. Free-threaded 3.14 (2 warm runs): 270 to 289 ms, 48.1 |
| `defer_build=True` on the model base | 224 ms, 38.5 MiB (-23 ms, -0.8 MiB) |
| 30-year synth, `fsync` stubbed, 3 runs each, 1,591 events | 3.12: user 1.19 to 1.27 s, peak 45.8 to 46.0 MiB. 3.13: 1.17 to 1.29 s, 47.0. 3.14: 1.22 to 1.38 s, 49.0 to 49.4. JIT on: 1.22 to 1.34 s, 49.7. Free-threaded: 1.35 to 1.51 s, 56.3 to 57.2 |
| No-pydantic floor (stdlib imports + psutil) | 24.2 to 24.6 MiB; +1.2 MiB and +8 ms for msgspec with 100 synthetic structs, 694 fields |
| Per event, 1,591 real bus lines | pydantic validate 14.3 µs, dump 5.0 to 7.6 µs; msgspec envelope-only (lower bound) 2.6 + 1.2 µs; 4.4 events per game month |
| `belief.update` | 3.12: 38.8 µs. 3.13: 33.4 to 38.9 µs interpreted, 26.7 to 28.9 µs under mypyc. 3.14 JIT: 40.1 µs vs 32.5 |
| Rust skeleton (polling duties only, no engine) | 2.0 MiB RSS, 0.01 s CPU per window, 0.5 wakeups/s, 0.53 MB binary |
| Startup, median of 20 | `sae daemon --help` 333 ms, `sae --version` 26 ms, `sae-parse --version` 2.5 ms |

### 4.2 Code

- Contracts are the pydantic models: `contracts/README.md:3-5`, `src/sae/contracts/common.py:74`.
- Writer hot path: `src/sae/bus/writer.py:245` (validate), `:266` (dump).
- Offline tools import `sae.core`: `src/sae/profile`, `src/sae/tune`, `src/sae/bench` (4, 4, 9 import sites).
- RSS limit: `src/sae/contracts/c7_governor.py:84`. Own-process priority: `src/sae/platform/_win32.py:83-99`.
- Kit pins: `tools/m0/field_kit/setup.ps1:93-95`; pure wheel `.github/workflows/ci.yml:134`.

### 4.3 Sources

| Claim | Source |
|---|---|
| Free-threaded penalty "roughly 5-10%"; JIT "not recommended for production", "10% slower to 20% faster" | https://docs.python.org/3/whatsnew/3.14.html |
| mypyc: "1.5x to 5x faster"; "compiled for each major Python release" | https://mypyc.readthedocs.io/en/latest/introduction.html |
| PyO3: one abi3 build "for each OS / architecture" | https://pyo3.rs/latest/building-and-distribution.html |
| Memory priority is "a hint to the memory manager when it trims pages" | https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/ns-processthreadsapi-memory_priority_information |
| msgspec 0.22.0, Python >= 3.10 | https://pypi.org/pypi/msgspec/json |

### 4.4 Differences from the baseline review

- Import time is 0.25 s on ext4, not 0.88 s. The running daemon is 41.6 MiB (360 modules), not 38.
- pydantic is not a CPU cost (agreed), but it is 12.8 MiB of RSS (32 %) and 51 to 60 % of import time. Still not
  worth replacing at 28 % of the limit.

Limits: wall clock on this WSL2 ran about 6 % short (60 s sleeps read 56.5 s); CPU ticks are 10 ms. A first synth
batch failed config validation (my shim's env name) and was discarded. `-OO` gave an invalid result (uncached
bytecode). Go was not measured. Scratch files are not in the repo.

## 5. Do-not-do

| Idea | Why it loses |
|---|---|
| Rust or Go daemon rewrite | 39.6 MiB for 10 to 16 weeks; Python stays on the PC for the kit and offline tools |
| PyO3 core | The 24.4 MiB interpreter floor stays; every belief change needs Rust |
| mypyc or Cython | Microseconds per game month; ends the `py3-none-any` wheel |
| Python 3.13, 3.14, the JIT, free-threading | No CPU gain; +1.1 to +10.4 MiB peak; the JIT is experimental |
| `defer_build`, lazy-import trimming | Under 1 MiB each |
| PyInstaller or Nuitka one-file exe | Not measured; unsigned packed exes are an install risk |

## 6. Needs maintainer decision

| Topic | Settled decision touched | Benefit | Cost | Recommendation |
|---|---|---|---|---|
| "One binary" (parser inside the daemon) | Three processes; parser exits per autosave | One exe, no spawn | Parse memory spike (102 MB synthetic) stays in the long-lived process; loses the Job memory cap | Keep settled |
| Rust daemon as a strategic direction | None directly | 39.6 MiB; no Python in the daemon process | 10 to 16 weeks; two engine implementations | No. Revisit only after V-1 and V-3 pass and a section 1 threshold is crossed |

Next action: add the daemon working-set and CPU row to the M0-12 checklist entry (change 2).
