# 035-T5/T4 — Stack-flip metrics: `daemon_private_bytes` and `engine_ms`

Status: **ACCEPTED** (2026-11-15; maintainer-approved, Wave 2). Reporters: T5 (sensor), T4 (loop). Owner: architect.
Work order W2-A0. Plan: `docs/plans/opt/IMPLEMENTATION_WAVES.md` §3, §5; Wave D review
(`docs/status/opt_wave_D_review.md`) §4 item 2; plan review B5, B7. Precedent: 026 (private memory).
Contracts: C7 §4.7, §5, model `SensorSample` (v1.2 → **v1.3**); C5 §3 monthly keys (v1.4 → **v1.5**, `v` stays 1).
Schema change: `SensorSample.schema.json` (one optional field). Sequenced after 034.
Consumers: T5 (`platform/sensors.py`, W2-3), T4 (`daemon/loop.py`, W2-2), T6 (W2-4), T8 (M0-12 row, W2-5).

## Problem
The stack-flip rule (move the daemon off pydantic to msgspec) names "private working set > 100 MiB" and "engine
work > 50 ms per game month". Neither is recorded: `daemon_rss_bytes` is psutil `rss` (Windows working set, shared
pages included) and no engine-time metric exists.

## Change 1 — `SensorSample.daemon_private_bytes` (C7 §5, v1.3)
```python
daemon_private_bytes: int | None = Field(default=None, ge=0)
```
- Linux: `RssAnon` from `/proc/self/status`. Windows: `psutil.Process().memory_info().private` (private commit).
  Other platforms: `None` (no RSS substitute; the metric must stay truthful).
- A failed probe keeps the last good value; `None` only before the first success or where no measure exists.
- Optional with a default, so every existing `SensorSample(...)` call still validates.
- `daemon_rss_bytes` stays, and the daemon self-limit (C7 §4.6, 150 / 300 MiB) stays on it.

## Change 2 — C5 §3 monthly `health` keys (v1.5, additive)
| Key | Value | Rule |
|---|---|---|
| `daemon_private_bytes` | float (bytes) | the latest `SensorSample.daemon_private_bytes`; omitted when `None` |
| `engine_ms` | float (ms) ≥ 0 | wall time (`time.perf_counter`) the daemon spent handling observations (features, belief, strategy, intent compile/validate/emit, their bus writes) summed since the previous `monthly` event; `0.0` when it handled none; on every `monthly` event |

`engine_ms` excludes the parse worker (a separate process; see `parse_cpu_s`), sensor polls and idle waits.

## Change 3 — Stack-flip rule restated (C7 §4.7; not a Governor trigger)
Reconsider the stack (msgspec) only if the M0-12 session on the game PC shows any of:
1. `daemon_private_bytes` p95 > 100 MiB (record `daemon_rss_bytes` p95 alongside);
2. `daemon_cpu_pct` session mean > 1 (% of one core);
3. `engine_ms` p95 > 50 per game month.

It changes no level and no decision; it is a maintainer decision rule, read from monthly `health` events.

## Migration
- T5 W2-3: probe + field. T4 W2-2 (after W2-1, W2-3): both keys in `_metrics`. T6 W2-4: mapping + view.
  T8 W2-5: M0-12 row.
- Not breaking. `CONTRACT_VERSIONS` keeps `C5: 1`, `C7: 1`. Old buses lack the keys.

## UNVERIFIED
- A-49 (new, README §5): Windows `memory_info().private` and Linux `RssAnon` each read in well under the 100 ms
  probe bound and track the daemon's private memory (M0-12). Linux cost measured ~12 µs (026); Windows unmeasured.
- The statistics in Change 3 (p95, session mean) are the architect's choice; the reviews name only the thresholds.
