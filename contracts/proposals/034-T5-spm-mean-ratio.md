# 034-T5 — spm trigger: window all samples, mean ratio, armed floor; `spm_ratio` / `spm_armed` on the bus

Status: **ACCEPTED** (2026-11-15; maintainer-approved, Wave 2). Reporter: T5 (governor); co-owner T4 (sampler).
Owner: architect. Work order W2-A0. Plan: `docs/plans/opt/IMPLEMENTATION_WAVES.md` §3; Wave B review
(`docs/status/opt_wave_B_review.md`) item 1 blocking changes 1–3; plan review B5, B7.
Contracts: C7 §1, §4.3, §4.4, §4.5 (v1.1 → **v1.2**); C5 §3 monthly keys (v1.3 → **v1.4**, `v` stays 1).
No model or schema change (`HealthPayload.metrics` is `dict[str, float]`; `GovernorConfig` keeps every field).
Consumers: T5 (`governor/core.py`, W2-1), T4 (`daemon/loop.py`, W2-2), T6 (mapping + console view, W2-4), T7 (CI-3).

## Problem
1. C7 v1.1 §4.4 says samples below 5 × `poll_s` are not added to the ratio windows. The code adds every sample, and
   the daemon emits one sample per observation, not per poll: 3 s/month gave 8 false H1 in 600 months.
2. A ratio of medians over 2 s-quantised samples cannot see a +15 % step (ratio 1.087).
3. `spm_med6` / `spm_med24` cannot reproduce the trigger; a disarmed trigger is invisible.

## Change 1 — C7 §4.3 and §4.4 (v1.2)
- **Sampling (unchanged rule, restated):** one `SpmSample` per **poll** that saw k ≥ 1 new HB lines, `months = k`,
  `wall_s` = (wall time since the previous poll that saw an HB) / k. A poll with no HB gives no sample. C7 §1 said
  "one per HB line"; corrected.
- **Windows:** every sample is windowed (k entries of `wall_s`); the per-sample 5 × `poll_s` exclusion is removed.
- **Armed:** the spm trigger is armed when the long window holds `spm_long_months` (24) entries **and** their
  median ≥ 5 × `poll_s` (10 s). Otherwise it is unarmed: no ratio exists, no spm escalation, and "no spm trigger"
  holds for de-escalation.
- **Ratio** (armed only) = mean(last `spm_short_months` (6)) / mean(last `spm_long_months` (24)).
- **Effective threshold.** The short window is inside the long one, so a sustained slowdown by factor r peaks at
  r / (0.75 + 0.25 r), six months after the step. `spm_h1_ratio` 1.10 therefore fires only for r ≥ 1.138
  (a 13.8 % slowdown); the Wave B review's 14.3 % step peaks at 1.103, +12 % at 1.087 (never fires), +20 % at 1.143.
- **Accepted cost:** means admit short player pauses below the 40 % step reset. Budget: ≤ 1 false H1 per 100 game
  months under pauses (W2-1 test); a false H1 only halves parsing for ≥ 6 months.
- **Step test (§4.4):** unchanged, including the skip when both months are below 5 × `poll_s`.
- Unchanged: discontinuity rule (`months > spm_short_months`), one level per 6 game months, never above H1,
  `spm_clear_ratio` 1.05.

## Change 2 — Governor read-only accessors (C7 §1)
T5's governor exposes, next to `spm_med6` / `spm_med24` (not part of the `Governor` protocol; the daemon reads them
with `getattr(..., None)` so test fakes need not implement them):

| Attribute | Type | Meaning |
|---|---|---|
| `spm_armed` | `bool` | the armed condition above |
| `spm_ratio` | `float \| None` | the ratio the trigger compares; `None` when unarmed |

## Change 3 — C5 §3 monthly `health` keys (v1.4, additive)
| Key | Value | Rule |
|---|---|---|
| `spm_armed` | `1.0` / `0.0` | on every `monthly` event (`metrics` values are floats) |
| `spm_ratio` | float | the governor's `spm_ratio`; **omitted** when unarmed (never `null`, never 0) |

`spm_med6` / `spm_med24` stay (medians; they no longer reproduce the trigger).

## Migration
- T5 W2-1: `_ratio()` to means + armed floor; drop nothing from `GovernorConfig`.
- T4 W2-2 (after W2-1): poll-tick sampler; emit both keys in `_metrics`.
- T6 W2-4: map and show both keys. Old buses lack them: readers treat a missing `spm_armed` as unknown, not 0.
- Not breaking: no wire field, no model. `CONTRACT_VERSIONS` keeps `C5: 1`, `C7: 1`.

## UNVERIFIED
- Below a median of 10 s/month (early game, fast speeds) the trigger never arms. How often real play sits there is
  unmeasured (M0-12); RAM and fault triggers remain.
- Pause distribution in real play (the ≤ 1 per 100 months budget is a synthetic test).
