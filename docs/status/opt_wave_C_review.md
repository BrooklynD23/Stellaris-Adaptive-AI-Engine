# Wave C (adaptiveness) adversarial plan review

Reviewer: sae-adversary, 2026-11-14. Inputs: `docs/plans/opt/SYNTHESIS.md` §2, §3, §5, §7; `docs/plans/opt/engine-core.md`
r1-r3; code in `src/sae/core/`, `src/sae/daemon/cli.py`, `src/sae/profile/`. Plan review only; no code changed.

**Wave verdict: ACCEPT WITH CHANGES.** Do not start item 3 until B1-B4 are in the plan. Item 4 r3 (clamp) can start
after B6.

## Item 3: carry belief, feature memory, commitment across sessions (ACCEPT WITH CHANGES)

Code facts checked:
- `save_folder_key` arrives only with the first parsed save (`adapters/assembler.py:220-292`); log-only observations
  before it carry `None`. The first tick runs before the key exists (`core/engine.py:73-101`), opens a 24-month
  window (`core/strategy.py:119-126`) and can emit a batch.
- Replay and synth keys are `f-synth-` + sha256(run_id) (`contracts/common.py:63-65`), so two replay sessions never share
  a key. The proposed validation cannot run through the real identity path without injecting a key.
- `_reset` on regression keeps the posterior and `window_open` (`core/engine.py:49-59`); the outcome of a window that
  spans a reload compares two timelines. This bug exists today in-session; carrying makes the gap unbounded.
- A-46 (one folder per campaign; reuse only by same empire name) is UNVERIFIED until M0-11. The C10 split rule
  (5 game years) misses a new game started in a folder whose old campaign is under 5 years old.

Blocking:
- **B1 Identity.** Carry only when all hold: same `save_folder_key`, same `player_sid`, same policy sha256 for the
  commitment part, and the C10 §1 rule-3 split test (reuse `profile/index.py` logic, do not re-implement) does not fire.
  Otherwise start fresh and publish why (B4). Policy differs -> carry belief and features, drop the window.
- **B2 Hold until keyed.** Until the first keyed observation (cap: 12 game months, then fall back to fresh), run
  features and belief only: no `strategy.select`, no batch. On carry, merge only `features`, `belief`, `strategy`;
  keep this run's `lifecycle`, `next_intent_id`, `decision_gen`, `session_epoch`, `gov_*`. Never swap the whole
  `EngineState` mid-run (seq allocator and in-flight batches belong to the live run).
- **B3 Branch fairness.** Carried state dated after the loaded save is player behaviour from a timeline this game
  never had. On regression of D months (carry or in-session): decay the posterior with `(1-rho)^D` toward uniform,
  reset to uniform if D > `commitment_months`, set `window_open=None` and mark the closing outcome void (new
  `metrics["void"]=1`, excluded from V-3 and tune), not reward 0.
- **B4 Audit.** One bus event per decision (`health` kind `info`, or a C5 additive field on `run_started`):
  `carried_from=<run_id>` or `carry_refused=<reason>`. Without it nobody can tell a carried session from a fresh one.

Commitment and floor: `strategy.reanchor` keeps remaining months, never extends past 24; safety floor is a static
template check (`core/strategy.py:73-75`) and is untouched. OK once B3 voids cross-branch outcomes.
Ironman: `observe_only` survives a carry; never let carried state clear it. Add a test.

Corrected validation (replaces "0 baseline months, 0 revert batches"):
1. Two sessions of one recorded campaign through the real key path (fixed salt + folder name, not `f-synth-`):
   session-2 strategy equals session-1 last, `commit_until` equals remaining months, no batch before the keyed
   observation.
2. Negative tests, each must start fresh and emit `carry_refused`: new game in the same folder at 2200.01 after a
   2204 campaign; different `player_sid`; different policy sha (window dropped, belief kept).
3. Reload 30 years earlier: posterior uniform, closing outcome void.
4. Game PC (M0-11, `docs/handoff/GAME_PC_AGENT.md`): two real sessions of one campaign plus a same-name new game;
   record which order (log-first or save-first) happens and whether A-46 holds. No ship before this.

## Item 4 r2: scale-free outcome metrics (ACCEPT WITH CHANGES)

Root cause confirmed: `fleet_share = fleet_power / naval_cap` (`core/outcome.py:55-57`) is not a share; power and cap
use different units (synth fixes it at 80, `adapters/synth.py:230`), so a 2 % change is a raw delta of ~1.6 and
`tanh` saturates. Log deltas fix units. They do not fix the confound: every AI grows over 24 months whatever it is
told (synth fleet +1 %/month, `synth.py:229`, gives log delta +0.24 every window). A scale-free reward then measures
game phase, and "spread > 0.02 for > 20 %" passes on any monotone rescale. It proves the code changed, not that the
reward ranks strategies.

Blocking:
- **B5** New metric keys (`d_log_fleet_power`, `d_log_systems`, `d_log_alloys`, each `log((y+1)/(x+1))`), a new
  PolicyVersion (major bump), and V-3 / tune must not mix old-key and new-key outcomes. Same names with new meaning
  is silent drift.
- Corrected validation: (a) unit: ±25 % fleet gives |reward| < 0.5, 0 systems does not raise; (b) on a reacting
  synth adapter with an injected effect (counter strategy +10 % fleet growth vs baseline), mean reward ranks the
  better strategy first in >= 8 of 10 seeds; (c) baseline-only runs: |corr(reward, window start year)| < 0.3.
  Drop the spread test.

Suggested (not blocking): subtract the mean log growth of managed AIs not in contact (they run baseline) as a
control; fog-legal, since it reads only managed AIs.

## Item 4 r3: clamp `expansion_rate` (ACCEPT WITH CHANGES)

Confirmed: an unclamped negative rate votes for the widest Gaussian (early_aggression, sd 1.5) after centring
(`core/belief.py:100-101`). But negative rates come mostly from fog, not strategy: the rate uses max visible systems
across in-contact observers (`core/features.py:116-121`), so losing an observer drops it. Clamping to min mu turns
fog loss into a full-strength turtle vote.
- **B6** `rate < 0` -> `None` (no evidence); clamp the top at max mu. Property tests: for x in [0, 10],
  P(turtle) non-increasing and P(early_aggression) non-decreasing; losing one observer moves no posterior.
- The 28.5 -> 3.0 month gain comes from synth territory collapse at a switch; do not quote it.

## §7 maintainer decisions touching this wave

1. `below_threshold` pre-emption (C3 §6, settled): needs the maintainer. Advise **defer**. First evidence has full
   weight (`features.py:23`, one save moves the top posterior to 0.60-0.98), so pre-emption plus the sharper r3
   belief lets one noisy save lock a 24-month counter. With item 3 the gain only applies to session 1. If approved,
   require >= 2 evidence ticks above threshold before pre-empting.
2. Early break: agree, keep closed.
3. Per-AI floor: out of scope, but the floor is vacuous today (always true); say so in docs, do not cite it as protection.

## Architecture check

| Area | Rating | Why |
|---|---|---|
| Performance and memory | ON TARGET | One extra small JSON read/write per run; engine cost unchanged |
| Telemetry | AT RISK | No carry event (B4); metric rename breaks V-3 history (B5) |
| Adaptive engine | AT RISK | Reward still confounded with growth; carry identity rests on UNVERIFIED A-46 |
| Learn profile from saves | AT RISK | Tune would fit time trend as reward; cross-branch outcomes poison labels until B3 |
