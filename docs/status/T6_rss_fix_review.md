# T6 RSS fix — Fable gate review (2026-10-20)

Reviewed commit `85b51cb` "fix: keep sae telemetry under its 150 MiB cap on CI" (sae-adversary, Fable 5.1).

## Verdict: ACCEPT WITH CHANGES

| Axis | Verdict | Why |
|---|---|---|
| (1) Performance / memory | AT RISK | Local peak 117-119 MiB (3 runs, WSL2, THP `madvise`). CI offset (+35-45 MiB) still unexplained and unmeasured post-fix; the commit title claims a CI result nobody has seen. |
| (2) Telemetry | ON TARGET | .rrd readback (`rerun rrd stats`): 20,440 rows, 79 entities, up to 3 indexes (seq, t_wall, game_month), 1 static (blueprint). Losing SDK `log_time` is fine: it was the telemetry process's clock, not the game's; `t_wall` carries the real one. Live latency unchanged (one batch per 2 s poll, same as before). |
| (3) Adaptive engine | ON TARGET | No engine code touched; `BusTail` default path (daemon/engine callers, `batch_bytes=None`) is byte-for-byte the old behaviour. |
| (4) Learn from saves | ON TARGET | Untouched. |

Tests: `uv run pytest -q tests/unit/t6 tests/e2e/test_synth_chain.py` — 152 passed, 0 failed.

## Platform / edge checks
- Windows / macOS: `limit_process_memory()` returns after `os.environ.setdefault`; no `ctypes.CDLL(None)` reached. No import-time work (function is called from `cli.main`). Safe.
- Linux musl / seccomp: `prctl` -1 and `mallopt` 0 both log a warning and continue. Safe.
- Import order verified: `numpy`, `pyarrow`, `rerun` not in `sys.modules` after `import sae.telemetry.cli`, so the env defaults are set before they load.
- 256 KiB batch vs 1 MiB C5 line: `reader.py:122-128` extends the read to `MAX_LINE_BYTES + 1` when the chunk has no LF; a 1 MiB line + LF (`end == limit`) is accepted; `MAX_LINE_BYTES+1` is skipped (tested). Partial trailing line (EOF before LF) leaves the offset in place. Correct.
- Error handling: a `send_columns` exception now loses one poll's batch instead of one record, then propagates (as before). Acceptable: telemetry is optional (C8 §2).

## Must fix now
1. `tests/unit/t6/test_telemetry_rss.py:21` `PEAK_BOUND_MIB = 135` is calibrated on one host with 16 MiB headroom while CI is known to sit 35-45 MiB higher. If THP is not the cause, this test fails on every CI run and so does `test_telemetry_rrd`. Fix: assert against `stream_mod.RSS_LIMIT >> 20` (150, the contract) and `print()` the peak plus `/proc/self/status` RssAnon/RssFile from the child so CI output calibrates it; tighten to 135 only after 3 green ubuntu runs. (Smallest diff: change one constant and add one print.)
2. `docs/status/T6.md` / CHANGELOG: state that the CI result is pending; commit title "on CI" is an unverified claim until a green run exists. One sentence.

## Tracked risks (not blocking)
- `LOW_MEMORY_ENV` docstring states as fact that bundled mimalloc in `rerun_bindings` honours `MIMALLOC_*` env; unverified for a statically linked Rust build. Label UNVERIFIED.
- THP: WSL2 runs `madvise` (so `PR_SET_THP_DISABLE` changes nothing locally); the fix only matters if the runner is `always`. Confirm with the anon/file split from the next CI run; if anon is not the delta, look at glibc arenas of the 4 parallel `run_sae` telemetry processes in `test_synth_chain.py::rrds` (each is its own process, so it is not that; but check the runner's `nproc`-sized rerun tokio pool).
- `tests/e2e/test_synth_chain.py:56` `test_telemetry_rrd` asserts only `size > 0`; a .rrd of blueprint-only would pass. Add `rerun rrd stats` row count > 0 (or `num_entity_paths > 10`) when convenient.
- `_rss_detail()` is Linux-only by design; fine, but the cap message loses its diagnostic on Windows where RSS is also measured. Low value, note only.

## Next action
Edit `tests/unit/t6/test_telemetry_rss.py:21` to `PEAK_BOUND_MIB = stream_mod.RSS_LIMIT >> 20` and print the child's peak; re-run CI.
