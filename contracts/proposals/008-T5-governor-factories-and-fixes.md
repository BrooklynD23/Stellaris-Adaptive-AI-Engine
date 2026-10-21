# 008-T5 — Governor factories, budget-breach trigger, spm step test, MAD floor

Status: ACCEPTED (architect, 2026-10-19; all of §1–§8, C7 → v1.1, C8 → v1.1 §5.1). No topology or
settled decision touched (budget_breach → ≥ H2 was already C7 §7), so no advisor round. §8's T4
suggestion is optional: the Governor's discontinuity rule is the single enforcement point.
Proposed 2026-10-19. Author: T5 lead. Owner: architect. Contracts: C7 (rules), C8 §5 (wiring), C5
(health `trigger` value). Consumers: T4 (wiring.py), T6 (health readers), T7 (CI-3).
T5 keeps building against C7 v1 as frozen until this is ACCEPTED.

## 1. Record the T5 factory signatures (additive, documentation)

```python
sae.governor.make_governor(cfg: GovernorConfig, min_level: str = "H0", preset: str = "balanced", *,
                           cpu_count: int | None = None, emit_period: int | None = None) -> Governor
sae.platform.make_sensors(game_names: tuple[str, ...] | None = None) -> Sensors
sae.platform.make_platform() -> Platform
```

T4 change requested (one line in `wiring.py`): pass `emit_period=config.bridge.emit_period`, so leaving H3
restores the configured period (C7 §3) including TOML/env overrides. Default without it:
`BridgeConfig().emit_period` (1). T4 should also call `deps.platform.lower_self_priority()` once at daemon
start (C7 §7 "Daemon itself"); nothing calls it today.

## 2. `Trigger.BUDGET_BREACH = "budget_breach"` (additive)

C7 §7 says a worker `budget_breach` escalates to at least H2, but `Trigger` has no value for it. T5 reports
`ram` meanwhile (a memory-cap breach is memory pressure). Diff: add the enum member; C7 §7 "… escalates to at
least H2 (trigger `budget_breach`)".

## 3. spm step test (C7 §4.4, rule change)

Current: "skipped when either month's spm is below 5 × poll_s". A burst of tiny samples (startup log backlog,
where T4 floors `wall_s` at 0.001; or the fastest game speed) fills the windows; later real months never pass
the step test (prev < 10 s), the short median climbs and the ratio jumps → false H1.
Diff: "skipped only when **both** months are below 5 × poll_s; samples below 5 × poll_s are not added to the
ratio windows." Not breaking (numbers unchanged).

## 4. Fault MAD floor (C7 §4.2, additive config)

A zero-MAD baseline (idle Linux: `pgmajfault` delta is 0 for 10 min) makes any single page-in above the
median a trigger candidate. Diff: `GovernorConfig.fault_mad_floor: float = 1.0 (ge=0)`; trigger uses
`median + fault_mad_k × max(MAD, fault_mad_floor)`.

## 5. Clarification: sustained H2 (C7 §3 H3 row)

"H2 for 6 game months" counts only months in which an H2 trigger (RAM < h2 or the fault trigger) was active;
a clean month resets it. Without this, steady RAM between h3 + margin and h2 flaps H2↔H3 every 6 months.
T5 implements this reading now.

## Migration

None for §1, §2, §4 (additive). §3 changes behaviour only on sub-10 s spm samples. Contract tests unaffected
except new enum/field.

## 6. Config validator gap (C7 §9, additive check)

With `h3_ram_bytes + deescalate_ram_margin_bytes < h2_ram_bytes` (legal today), steady RAM in that band
de-escalates H3→H2, then sustained H2 re-enters H3 six months later, each time with a worker kill and a knob
request. Defaults are safe (1 + 0.5 = 1.5 GiB = h2). Diff: `GovernorConfig._check` rejects
`h3 + margin < h2` and `h2 + margin < h1`.

## 7. Clarification: CLOSED month length floor (C7 §4.5)

"1 month = the last median spm" is floored at 5 × poll_s, the same floor as the step test; T4 floors backlog
`wall_s` at 0.001 s, which would otherwise count thousands of months per CLOSED poll. T5 implements this now.

## 8. Clarification: date jumps are not months (C7 §4.3, §4.5)

T4 `loop._spm` sends `months = k` = the `m` delta between log lines with `wall_s` floored at 0.001; loading a
later save gives k in the hundreds. T5 treats `months > spm_short_months` in one sample as a discontinuity:
spm windows cleared, no months counted toward sustained-H2, de-escalation, H1 age or spm rate limit. Per-poll
month deltas fed to those counters are clamped to ≤ 1. Suggest T4 also skip `on_month` when the date goes
backwards or jumps (save load).

## 9. Interim behaviour still contract-exact (known gaps until accepted)

§3 (fast→slow speed change gives a false H1) and §4 (zero-MAD baseline gives H2 on any page-in rate above the
median for 3 polls) remain as frozen in C7 v1 until this proposal is accepted.
