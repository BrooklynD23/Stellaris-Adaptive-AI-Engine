# C7 — Resource Governor

| | |
|---|---|
| Version | 1.1 |
| Status | FROZEN v1.1 (2026-10-19; v1 + proposal 008-T5) |
| Owner | architect (contract); T5 implements Governor, Sensors, Platform |
| Producers | Governor (T5): `GovernorDecision`, `health` events |
| Consumers | Daemon loop (T4) — parse scheduling, worker spawn, knob requests; bus (T6); CI-1/CI-3/CI-4/CI-5 (T7) |
| Code | `src/sae/contracts/c7_governor.py` — `GovernorConfig`, `SensorSample`, `GovernorDecision`, `SpmSample`, `WorkerOutcome`, `WorkerSpec`, protocols `Governor`, `Sensors`, `Platform`, `WorkerHandle` |
| Sources | resource proposal §3, §4, §8 **as amended by signoff changes 1–6**; signoff optional cuts applied: no worker CPU-rate cap, no affinity/CPU sets, no in-situ self-A/B, PAUSED off by default |

## 1. Shape

The Governor is a module **inside the daemon**, not a process. `Governor` is pure logic: it receives
`SensorSample`s (every `poll_s` = 2 s), `SpmSample`s (one per HB line), `WorkerOutcome`s, the next
commitment boundary and operator suspend/resume, and returns a `GovernorDecision` per poll. All I/O
lives in `Sensors` (probes) and `Platform` (priorities, worker spawn). This makes CI-3 a pure test.

**Factories (T5; called only by the daemon's composition root, C8 §5; proposal 008):**

```python
sae.governor.make_governor(cfg: GovernorConfig, min_level: str = "H0", preset: str = "balanced", *,
                           cpu_count: int | None = None, emit_period: int | None = None) -> Governor
sae.platform.make_sensors(game_names: tuple[str, ...] | None = None) -> Sensors
sae.platform.make_platform() -> Platform
```

`emit_period` is the resolved `config.bridge.emit_period` (restored on leaving H3, §3); `None` means
`BridgeConfig().emit_period` (1). The daemon calls `platform.lower_self_priority()` once at start (§7).

**Safety property.** Correctness never depends on throughput: lower levels only coarsen the timing
of observations; they never change decision logic (CI-1).

## 2. State axis (opportunity)

| State | Detection | Allowed work |
|---|---|---|
| PLAYING | game process present, and not PAUSED | online loop; one parse per eligible autosave, `worker_threads = 1`, cap `worker_mem_cap_bytes` (1 GiB) |
| PAUSED | only if `pause_detection = true` (after M1-P): game CPU ≤ `paused_cpu_max_pct` for `paused_debounce_s` (4–6 s) **and** no HB for longer than 2 × the 6-month median spm | deferred parse of the latest save; ≤ min(N−2, 4) threads; RAM checked before each job |
| CLOSED | no game process, or game minimised ≥ `closed_minimized_s` (10 min) | full local budget minus 2 cores; cap `worker_mem_cap_closed_bytes` (2 GiB) |

PAUSED is off by default (signoff: cut until M2 telemetry shows it matters). On leaving PAUSED for
PLAYING, a running worker is killed if available RAM < `h1_ram_bytes`, otherwise it continues at
`worker_threads = 1` (suspension is not used in v1: it saves CPU but not RAM).

## 3. Health ladder (pressure)

| Level | Entry trigger (any) | Behaviour (`GovernorDecision`) |
|---|---|---|
| H0 Normal | — | `parse_every_n = 1`; tier T2 under the dev preset, else T1 |
| H1 Sparse | available RAM < `h1_ram_bytes` (2 GiB); **or** spm ratio ≥ 1.10 (§4) | `parse_every_n = 2`; after 6 game months still at H1 → 3; tier T0 + scalars (T1). Skip saves before cutting threads. |
| H2 Decision-only | available RAM < `h2_ram_bytes` (1.5 GiB); **or** fault trigger (§4) | `parse_window_only = true`: parse only saves dated within `h2_parse_window_months` (3) before the next commitment boundary; `kill_worker = true` on entry; tier T0 |
| H3 Quiet | available RAM < `h3_ram_bytes` (1 GiB); **or** H2 for `h2_sustain_months_to_h3` (6) game months (only months in which an H2 trigger was active count; a clean month resets the count); **or** preset floor (`min_level`) at H3 | `parse_every_n = 0` (log tail only); `knob_request = {emit_period: h3_emit_period (12)}` once on entry; tier T0 |
| S Suspended | operator `pause` (C9) only | engine idle (no decisions, no emits); observation, logging and Governor polling continue; the mod keeps the last applied knobs |

On leaving H3, `knob_request = {emit_period: bridge.emit_period}` once, restoring the configured period.

`min_level` from the preset (C8, H0–H3, never S) is a floor: the effective level is `max(trigger level, min_level)`.
`forced_level` (test hook) overrides everything except S.

## 4. Triggers and hysteresis (exact)

1. **RAM and faults escalate first and immediately.** They are evaluated on every 2 s poll and act on
   the same poll; they may skip levels. CI-3 and R-4 require the target level to be reached within
   `reach_within_s` (10 s) end to end (signoff change 3).
2. **Fault trigger** (signoff change 5): baseline = median and MAD of `hard_fault_rate` over the first
   `fault_baseline_s` (10 min) of the session (or of the (a) run in R-4); trigger = rate > median +
   `fault_mad_k` (3) × max(MAD, `fault_mad_floor` (1.0 pages/s)) for `fault_consecutive_polls` (3)
   consecutive polls (the floor keeps a zero-MAD idle baseline from triggering on any page-in). While the baseline is
   incomplete, or when `hard_fault_rate` is `None`, the fault trigger is inactive.
3. **spm** (HB is exempt from `sae_quiet`, signoff change 1) is measured at poll resolution: on a
   poll that sees k ≥ 1 new HB lines, one `SpmSample` with `months = k` and `wall_s` = (wall time
   since the previous poll that saw an HB) / k. Several HBs in one poll (fast speed) therefore give an
   average, never 0. Resolution is ±2 s per month plus flush jitter (A-07); at fast speeds the ratio is
   noise-level, which is one more reason spm is capped at H1.
   A sample with `months > spm_short_months` (a save load or date jump, not play) is a discontinuity:
   it clears both spm windows and counts no months toward sustained H2, de-escalation, H1 age or the
   spm rate limit; month deltas fed to those counters are clamped to ≤ 1 per poll (proposal 008 §8).
   Ratio = median of the last `spm_short_months` (6) / median of the last `spm_long_months` (24); it
   needs 24 samples. Ratio ≥ `spm_h1_ratio` (1.10) escalates **at most one level per 6 game months**
   and **never above H1** (architect decision: spm is confounded by speed changes).
4. **Speed change:** if one month's spm differs from the previous month's by more than
   `spm_step_reset` (40 %), clear the 24-month window (and the 6-month one). The step test is skipped
   only when **both** months' spm are below 5 × `poll_s` (10 s), where poll jitter alone exceeds 40 %;
   samples below 5 × `poll_s` are not added to the ratio windows (proposal 008 §3).
5. **De-escalation:** one level down after `deescalate_after_months` (6) consecutive game months with
   no active trigger. "No RAM trigger" means available RAM ≥ entry threshold of the current level +
   `deescalate_ram_margin_bytes` (0.5 GiB). "No spm trigger" means ratio < `spm_clear_ratio` (1.05).
   In CLOSED (no HB), months are counted from wall time at 1 month = the last median spm floored at
   5 × `poll_s`, or 30 s if none.
6. **Daemon self-limit** (signoff change 6): `daemon_rss_bytes` > 150 MiB → `health budget_breach`
   (component daemon) once per game month, keep running. > 300 MiB → `daemon_should_exit = true`:
   the daemon flushes the bus, writes `health daemon_exit`, and re-executes itself in place
   (kill any worker, then start `[sys.executable, "-m", "sae", "daemon", "--resume", run_id, "--runs-dir", str(runs_dir)]` with
   `subprocess.Popen` (argv list, so paths with spaces are safe; stdio inherited) and exit; on POSIX
   `os.execv` with the same argv is equivalent. the parent writes `runs/CURRENT` = `"<run_id> <child_pid>"` after `Popen` returns and before it exits; no second
   `run_started`, bus `seq` continues) — no supervisor process, so the topology stays at three
   processes. 150 MiB stays the CI-4 gate.

## 5. Sensors (2 s poll, cheap probes only)

`SensorSample`: available RAM (`psutil.virtual_memory().available`); hard-fault rate (Windows PDH
`\Memory\Pages Input/sec`; Linux `pgmajfault` delta from `/proc/vmstat`; `None` if neither works);
game process present/CPU time/minimised time; daemon RSS and CPU %. HB timing arrives separately as
`SpmSample`. Worker self-reported cost arrives in `WorkerOutcome`. No NVML, no VRAM, no GPU probe of
any kind. A probe must not block > 100 ms; a failing probe yields `None`, never an exception.

The game process is found by name (`stellaris.exe` on Windows, `stellaris` on Linux; config
override). SAE **never** changes the game's priority or affinity.

## 6. In-game cost knobs

- `sae_emit_period` (1–12): a country emits SNAP/PLYR when `(m + sid) mod period == 0` (C1). The
  per-country slot is its `sid`, assigned from a running counter on the country's first monthly pulse, so slots spread evenly
  and stay spread when the period changes.
- `sae_quiet` (0/1): 1 suppresses SNAP and PLYR; **HELLO, HB and ACK are never suppressed**.
- Both change only through the C2 knob line (and so only when the player runs the file). In Ironman
  they are fixed per session. The Governor requests them via `knob_request`; the engine puts the
  request into its next batch (C3 `IntentBatch.knobs`).

## 7. Parse worker: spawn, limits, outcomes

Eligibility (all of): a new autosave is stable (size and mtime unchanged for `parse_stable_s` = 2 s)
and ≥ `parse_min_age_s` (8 s) old; the current decision allows it (`parse_every_n`, window); no worker
is running (one at a time; a newer save replaces a queued one, recorded as `save_skipped`).

`Platform.spawn_worker(WorkerSpec)`:

| | Windows | Linux |
|---|---|---|
| Priority | `CreateProcess` with `IDLE_PRIORITY_CLASS`; worker sets `PROCESS_MODE_BACKGROUND_BEGIN` + EcoQoS on itself (`--background`, C4) | `SCHED_IDLE` and `ionice -c3` applied to the child before exec |
| Memory cap | own Job Object: `JOB_OBJECT_LIMIT_PROCESS_MEMORY = mem_cap_bytes` (allocations fail at the cap; Rust aborts) + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (a crashed daemon cannot orphan a worker) | `systemd-run --user --scope -p MemoryMax=<cap>` when available; else `RLIMIT_AS = cap` (approximate, documented) |
| CPU cap | none (signoff: cut) | none |
| Daemon itself | `BELOW_NORMAL_PRIORITY_CLASS` + EcoQoS; **no Job** (nested Job limits clamp the child, VERIFIED in signoff) | `nice 10` |

Timeout: `parse_timeout_s` (60 s) → kill. Outcome classification (`WorkerOutcome.kind`, C4 §2):
`ok`; `clean_failure` (exit 2–6 with valid `ParseError`) → `health parse_failed`;
`budget_breach` (any other exit, signal, timeout, oversize or invalid stdout) → `health budget_breach`
the breached save is never retried (log-only until the next save), and the Governor escalates to at
least H2 with trigger `budget_breach` (resource proposal risk 2); the daemon keeps running;
`killed_by_governor` (the decision said `kill_worker`) → `health worker_killed`.

Windows-only code sits behind `sys.platform == "win32"` checks and is unit-tested with mocks; it is
labelled "not executed in CI on this machine" where true.

## 8. Health events

Every level or state transition → `health level_change` / `state_change` with `trigger` and the
triggering metric. Every game month (on HB, or every 30 s of wall time in CLOSED) → `health monthly`
with the C5 metrics keys. Always recorded at any level: transitions, breaches, every skipped parse,
every killed worker.

## 9. Examples

```json model=GovernorConfig
{"h1_ram_bytes": 2147483648, "h2_ram_bytes": 1610612736, "h3_ram_bytes": 1073741824,
 "pause_detection": false, "worker_mem_cap_bytes": 1073741824}
```

```json model=SensorSample
{"t_mono_s": 1234.5, "available_ram_bytes": 1500000000, "hard_fault_rate": 12.0, "game_running": true,
 "game_cpu_s": 8812.4, "game_minimized_s": 0, "daemon_rss_bytes": 61000000, "daemon_cpu_pct": 0.6}
```

```json model=GovernorDecision
{"state": "PLAYING", "level": "H2", "parse_every_n": 1, "parse_window_only": true, "worker_threads": 1,
 "worker_mem_cap_bytes": 1073741824, "kill_worker": true, "telemetry_tier": "T0", "knob_request": null,
 "trigger": "ram", "daemon_should_exit": false}
```

```json model=WorkerOutcome
{"kind": "budget_breach", "exit_code": 134, "cost": null, "detail": "SIGABRT; no JSON on stdout"}
```

Invalid: polling faster than 2 s.

```json model=GovernorConfig invalid
{"poll_s": 1.0}
```

Invalid: thresholds out of order.

```json model=GovernorConfig invalid
{"h1_ram_bytes": 1000, "h2_ram_bytes": 2000, "h3_ram_bytes": 500}
```

Invalid: the de-escalation margin does not bridge h3 → h2 (1 GiB + 0.25 GiB < 1.5 GiB), so steady RAM
in the gap would cycle H2 ↔ H3 (proposal 008 §6).

```json model=GovernorConfig invalid
{"deescalate_ram_margin_bytes": 268435456}
```

Invalid: level S forced by config (S is operator-only).

```json model=GovernorConfig invalid
{"forced_level": "S"}
```

Invalid: pause detection without a measured band.

```json model=GovernorConfig invalid
{"pause_detection": true}
```

## 10. Versioning

Numbers live in `GovernorConfig` and may be retuned by config without a contract change. New
levels, states, triggers, decision fields or a change to a rule in §3–§7 are contract changes.
v1.1 (proposal 008): trigger `budget_breach`, config `fault_mad_floor` and the RAM-band margin
check, the spm step-test and discontinuity rules, the sustained-H2 and CLOSED-month clarifications,
the factory signatures. No wire field changed; `CONTRACT_VERSIONS` and `run_started` keep `C7: 1`.

## 11. UNVERIFIED / HYPOTHESIS items

| Id | Assumption | Test | Fallback |
|---|---|---|---|
| A-25 | Game CPU-time rate separates paused from running | M1-P | PAUSED stays off; pause work only in CLOSED |
| A-26 | A reliable hard-fault counter exists (PDH Pages Input/sec vs psutil) | M0-12 | Fault trigger inactive; RAM triggers only |
| A-27 | Job `PROCESS_MEMORY` limit makes allocations fail so the Rust worker aborts (nesting rule VERIFIED in signoff) | R-5 | Kill on `rss_peak` overrun after the fact |
| A-28 | Background mode lowers I/O and memory priority, not just CPU | R-5 | ≥ 8 s start delay is the real protection |
| A-29 | Mod script costs ≤ 1 % of seconds per game year at the shipped config | M0-12, R-3 | Longer period, fewer managed countries |
