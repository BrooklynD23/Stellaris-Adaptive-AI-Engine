# T5 plan — Governor and platform

Owner: T5 lead. Paths: `src/sae/governor/**`, `src/sae/platform/**`, tests in `tests/unit/t5/**`.
Contract: C7 v1 (FROZEN) as amended by signoff changes 1–6. Consumes C4 §2 (exit codes; classification
already lives in T4 `saveparser.py`), C5 health kinds (emitted by T4 from our decisions), C8 presets.

## 0. What T4 already does (do not duplicate)

- `wiring.py` calls `make_governor(config.governor, config.min_level, config.preset)`, `make_sensors()`,
  `make_platform()` with exactly these args.
- `loop.poll_once` samples, polls, emits `level_change`/`state_change`, kills the worker on `kill_worker`,
  stores `knob_request`, handles daemon RSS `budget_breach` and the re-exec on `daemon_should_exit`.
- `loop._spm` builds `SpmSample` per observation with `m` delta; `saveparser` builds `WorkerSpec`, owns
  the timeout, classifies `WorkerOutcome`, applies `parse_every_n`/window gating.
- T4 never calls `Platform.lower_self_priority()` → reported to T4 (one line in `run()` start).

So T5 = pure Governor + Sensors + Platform (priority, spawn, limits, kill). No bus I/O.

## 1. Work units (disjoint files, run sequentially: concurrency cap 1)

### U1 Governor (pure) — `src/sae/governor/__init__.py`, `src/sae/governor/core.py`, `tests/unit/t5/test_governor.py`, `tests/unit/t5/test_governor_props.py`

`make_governor(cfg, min_level="H0", preset="balanced", *, cpu_count=None, emit_period=None) -> Governor`.
`emit_period` default = `PRESETS[preset]["bridge"]["emit_period"]` else `BridgeConfig().emit_period`
(proposal 008 asks T4 to pass `config.bridge.emit_period`; additive kwarg).

State per poll (all times from `sample.t_mono_s`; dt < 0 or > 60 s (clock jump/suspend) → dt = 0 for
accumulators, never negative):
1. GameState: CLOSED if `not game_running` or `game_minimized_s >= closed_minimized_s`; PAUSED only if
   `pause_detection` and game CPU % (Δcpu_s/Δt·100) ≤ `paused_cpu_max_pct` continuously for
   `paused_debounce_s` and no HB for > 2 × median of last 6 spm; else PLAYING.
2. RAM level: `<h3`→H3, `<h2`→H2, `<h1`→H1, else H0. Fault level H2 when fault trigger active.
   Target above current → jump this poll (may skip levels), trigger `ram`/`faults`.
3. Fault baseline: rates in the first `fault_baseline_s` after the first sample; then median + MAD frozen.
   Active when rate > median + k·max(MAD, 1.0) for `fault_consecutive_polls` consecutive polls. `None`
   rate resets the consecutive count. MAD floor 1.0 page/s: a zero-MAD baseline (idle Linux) would
   otherwise trigger on any single page-in. (ponytail constant; proposal 008 adds `fault_mad_floor`.)
4. spm (`on_month`): append `wall_s` k times to the month window (deque maxlen 24). Step test vs previous
   sample: skip if either < 5·poll_s; if |new−prev|/prev > `spm_step_reset` → clear windows first.
   Ratio needs 24 samples. Ratio ≥ 1.10, current pressure H0, ≥ 6 game months since last spm
   escalation → pending H1 (trigger `spm`), applied on next poll. Never above H1.
5. Worker `budget_breach` → pending escalation to ≥ H2 (trigger `ram`; proposal 008 adds `budget_breach`
   trigger). Other outcomes: no level effect.
6. Sustained H2 for `h2_sustain_months_to_h3` game months → H3 (trigger `sustained_h2`).
7. Game months: += k on `on_month`; in CLOSED += dt / (last median spm or 30 s).
8. De-escalation: clean months counter; reset to 0 on any poll/month where a trigger for the current
   level is active (RAM < entry(level) + margin, fault active, spm ratio ≥ clear 1.05). 6 clean months →
   one level down (trigger `deescalate`), counter reset.
9. Output level = forced_level if set, else max(pressure, min_level); S overrides all while suspended
   (trigger `operator` on enter/leave). Trigger `preset` when the floor raises the first output.
10. Decision fields: parse_every_n H0 1, H1 2 (3 after 6 game months at H1), H2 1 + window, H3 0;
    kill_worker on the poll entering H2/H3 from below, or PAUSED→PLAYING with RAM < h1;
    knob_request {emit_period: h3_emit_period} on the poll entering H3, {emit_period: base} on the poll
    leaving H3; tier H0 T2 if preset dev else T1, H1 T1, H2/H3 T0; threads PLAYING 1, PAUSED
    min(N−2,4)≥1, CLOSED N−2 ≥1; mem cap closed cap only in CLOSED; daemon_should_exit = RSS > exit.
    In S: parse fields of the underlying level, no kill, no knob.

Tests (seeded, deterministic): every C7 §3/§4 row; CI-3 traces — drifting spm (+1 %/month for 120 months)
never above H1; 40 % step clears windows; RAM ramp 4 GiB→0.9 GiB over 30 s reaches H2 ≤ 10 s simulated
after crossing; fault spike after baseline → H2 after 3 polls; zero-MAD baseline + 1 page/s noise → no
trigger; Hypothesis property: for random RAM/fault/spm traces, no level decrease within 6 game months
of any increase (no oscillation), output ≥ min_level, never S without suspend, decision always validates.

### U2 Platform — `src/sae/platform/__init__.py`, `sensors.py`, `spawn.py`, `_win32.py`, `tests/unit/t5/test_sensors.py`, `test_spawn.py`, `test_no_foreign_pid.py`

- `make_sensors(game_names=None) -> Sensors`: psutil `available`; faults: Linux `/proc/vmstat`
  `pgmajfault` Δ/Δt, Windows PDH `\Memory\Pages Input/sec` via ctypes (mocked), else None; game by
  name (cached pid, read-only `cpu_times`), minimised time Windows-only via `IsIconic` on its top-level
  window (mocked), else None→0? no: None; daemon RSS + `cpu_percent`. Any probe exception → None (RAM:
  last good value, else 0? → RAM failure returns last good, never raises). NaN/inf → None.
- `make_platform() -> Platform`: `lower_self_priority()`: Windows BELOW_NORMAL + EcoQoS on
  `GetCurrentProcess()`; POSIX `os.setpriority(PRIO_PROCESS, 0, max(cur, 10))`. Idempotent.
- `spawn_worker(spec)`: Linux: argv = [`ionice -c3` if found] + [`systemd-run --user --scope --quiet
  -p MemoryMax=<cap>` if usable] + [exe, *args]; `preexec_fn`: `SCHED_IDLE` + `RLIMIT_AS = cap` (only when
  no systemd scope). Windows: `CREATE_SUSPENDED | IDLE_PRIORITY_CLASS`, Job with `PROCESS_MEMORY` +
  `KILL_ON_JOB_CLOSE`, assign, `NtResumeProcess`; job handle closed on wait-complete/kill (no leak).
  stdin DEVNULL, stdout/stderr PIPE. `WorkerHandle.kill()` idempotent, swallows ProcessLookupError.
- Safety: no function accepts a PID parameter; every pid used is `0`/self or our own child. Test greps
  the AST of `src/sae/platform` and `src/sae/governor` for psutil/os/ctypes calls that set priority,
  affinity, nice, ionice, rlimit or job assignment with any target other than self/child, and asserts
  that `Process(pid).nice`/`cpu_affinity`/`ionice` setters are never called with arguments.

Tests: Linux real spawn of `python3 -c` children (exit codes, stdout, kill, RLIMIT_AS breach →
non-zero exit, timeout path via kill); Windows path with ctypes fakes (all calls recorded, job closed
exactly once); sensors with fake psutil + fake `/proc/vmstat` text.

## 2. Acceptance

`uv run pytest -x -q` green; `uv run mypy --strict src/sae/governor src/sae/platform` clean; `uv run ruff check`
clean; `wiring.default_deps` resolves T5 factories (T6 may still be missing).

## 3. Risks

1. RLIMIT_AS caps virtual memory, not RSS: Rust thread stacks/mmap may hit it early. Mitigation: systemd
   scope preferred; documented as approximate (C7 §7).
2. `preexec_fn` with threads is unsafe in theory; we keep it minimal (two syscalls). UNVERIFIED on Proton.
3. Windows code is not executed on this machine (A-27/A-28 UNVERIFIED → T8 M0 list).
4. PDH counter name localisation: use `PdhAddEnglishCounterW`.

## 4. Backlog

PAUSED tuning after M1-P; pause-time queue (M2); suspend-on-unpause; Proton path detection;
`fault_mad_floor`/`budget_breach` trigger once proposal 008 is accepted.

## 5. Adversary review log

Fable review 1 (12 findings) — all accepted; changes applied to the build orders:
1. spm step test (must): built contract-exact (skip when EITHER < 5·poll_s); the false-H1 path (tiny
   startup/fast-speed samples poisoning the window) goes to proposal 008 (skip only when BOTH small; never
   append samples < 5·poll_s). H1 is the cheap reversible level, so contract-exact is safe meanwhile.
2. sustained_h2 counts only months with an H2 trigger active (RAM < h2 or faults); reset on a clean month.
   Stops H2↔H3 flapping at steady RAM between h3+margin and h2. Recorded in 008 as a clarification.
3. kill/knob computed from pressure-level edges, latched while suspended, emitted on first poll after resume.
4. systemd-run probed once in make_platform() (`systemd-run --user --scope -q true`, 2 s timeout); else RLIMIT_AS.
5. No hidden MAD floor: contract-exact median + k·MAD; `fault_mad_floor` proposed in 008.
6. emit_period: kwarg only, default `BridgeConfig().emit_period`; 008 asks T4 to pass `config.bridge.emit_period`.
7. Sensors: RAM probed in make_sensors() (raise there); later failures return last good. First fault sample → None.
8. No-foreign-PID test: record every `psutil.Process(pid)` / pid-taking os call; assert pid ∈ {None, self,
   own children}; grep for affinity setters empty.
9. RLIMIT_AS test: cap 512 MiB, child allocates 1 GiB.
10. Cut CREATE_SUSPENDED/NtResumeProcess: assign Job right after spawn; on assign failure run without Job, log once.
11. Cut PAUSED: `pause_detection=true` behaves as PLAYING (ponytail + UNVERIFIED A-25 until M1-P). No traces.py.
12. Tests: add PID-reuse check (create_time) on the cached game pid; property tests count CLOSED months the
    same way the governor does.
