# 030-T3 — `cost.rss_peak_bytes` on Windows reports peak commit

Status: **ACCEPTED** (2026-11-14; maintainer-approved first step of the optimization study). Reporter: T3. Owner:
architect (C4 §2 wording; T3 does not edit `contracts/C4_sae_parse.md` or `contracts/CHANGES.md`).
Contracts: C4 §2 (`cost`). Version class: **C4 → v1.1 (rule change; `schema_version` stays 1)**.
Consumers: T5 daemon (`parse_rss_peak_bytes` health metric), T6 telemetry/health, T7 CI and bench, T8 `tools/m0`
(M0-11 evidence), console view.

## Problem
C4 §2 says Windows `rss_peak_bytes` is `PeakWorkingSetSize`. `--background` (`PROCESS_MODE_BACKGROUND_BEGIN`) caps
the working set at about 32 MiB, so the figure under-reports (33.6 MB resident against 103 MB commit in the Wave B
measurement). The Job memory limit (C7 §8) caps commit, not working set. Sources: `docs/status/opt_wave_B_review.md`
item 2a, `docs/status/opt_wave_D_review.md` blocking change 2.

## Change (rule change, C4 v1.1; no model, schema or wire change)
C4 §2, `cost` line:

```diff
-`cost` is self-measured: `rss_peak_bytes` (Windows `PeakWorkingSetSize`; Linux `getrusage(RUSAGE_SELF).ru_maxrss × 1024`),
+`cost` is self-measured: `rss_peak_bytes` (Windows: peak commit, `PROCESS_MEMORY_COUNTERS.PeakPagefileUsage`; Linux
+`getrusage(RUSAGE_SELF).ru_maxrss × 1024`). The field name is kept for compatibility; on Windows it is not RSS.
```

## Migration
Windows `rss_peak_bytes` from a parser before this change is peak working set; from the new parser version on it
is peak commit. Do not compare Windows values across the two parser versions (use `parser_version` to tell them
apart). Linux values are unchanged.

## Impact
- Field name `rss_peak_bytes` unchanged (a rename would be breaking). Type and range unchanged.
- Meaning changes on Windows only: values rise (in background mode ~3× for the Wave B save). Consumers comparing
  against a working-set threshold must treat it as commit; the 1 GiB Job cap and the D1 "commit > 250 MB" trigger
  already are commit thresholds, so they now line up.
- Implemented in `parser/src/cost.rs` (T3). Validation (Wave B): `rss_peak_bytes` within 5 % of externally measured
  peak commit in both modes on the game PC (M0-11, UNVERIFIED until then).
