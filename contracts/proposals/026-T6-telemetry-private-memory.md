# 026-T6 — Telemetry cap measures private memory, not RSS

Status: **ACCEPTED** (maintainer decision, 2026-10-20). Reporter: T6. Owner: architect (C8 §2 wording, done in
parallel by the architect; T6 does not edit `contracts/C8_config_cli.md` or `contracts/CHANGES.md`).
Contracts: C8 §2 (telemetry budget). Consumers: T6 (implements), T7 (CI tests), T8 (docs).

## Problem
`sae telemetry` exited 1 on the GitHub ubuntu-24.04 runner: "telemetry RSS 162-172 MiB exceeds limit". CI run
39442249597 printed the split: RSS 152-165 MiB = anon 59-60 MiB + file-backed 92-106 MiB. The file-backed part is
shared library pages (rerun_bindings 207 MB .so, libarrow, numpy): page cache, shared and reclaimable, and its size
depends on the kernel's fault-around/readahead, not on what telemetry allocates. WSL2 counts 56-61 MiB of it.

## Decision
- The cap stays **150 MiB**, a fixed budget, no softer tier (losing telemetry costs no decision).
- It measures **private memory**: Linux `RssAnon` (`/proc/self/status`), Windows `psutil memory_info().private`,
  any other platform full RSS as the fallback.
- On a breach the log line prints the private figure, total RSS and the anon/file split; exit 1 as before.
- The daemon budget (C7 §4.6, `daemon_rss_bytes`) is unchanged.

## Implementation (T6, landed 2026-10-20)
- `src/sae/telemetry/stream.py`: `private_bytes()` (replaces `rss_bytes()`), `MEM_LIMIT` (replaces `RSS_LIMIT`),
  `run_stream(..., mem=private_bytes)`. Cost of the Linux measure: ~12 us per poll (`/proc/self/status`);
  `psutil.memory_full_info()` was rejected at ~375 us per call (reads smaps).
- `tests/unit/t6/test_telemetry_rss.py`: child process samples RssAnon every 5 ms on a ~3 MiB / ~20k-record bus,
  asserts peak private <= 150 MiB, prints peak private, VmHWM and the anon/file split on every run.

## Measured (WSL2, 2026-10-20)
| Run | Peak private | Peak RSS |
|---|---|---|
| 6-year synth (9p venv / ext4 venv) | 59 / 60 MiB | 115 / 121 MiB |
| ~3 MiB bus, T2 | 61-62 MiB | 119 MiB |

CI result with the private measure: pending.
