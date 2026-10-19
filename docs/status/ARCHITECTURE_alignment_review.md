# Architecture alignment review (Fable, 2026-10-19, post Wave 3)

Scope: the built MVP against the design report, the resource proposal (+ signoff), contracts C1–C9 and the
expansion proposal (player profiles + bounded RSI). Read-only; game behaviour stays UNVERIFIED (docs/m0_checklist.md,
56 A-/X- items). Measurements below were taken on WSL2 with the synth adapter; see §5 for the exact commands.

| Benefit | Verdict |
|---|---|
| 1. Performance and memory management | **ON TARGET** (2 contract drifts, Windows paths unexecuted) |
| 2. Good telemetry | **AT RISK** (parse cost and spm never reach the bus; monthly metrics thin) |
| 3. Properly adaptive AI engine | **AT RISK** (mechanics correct; evidence is circular until M2 labelled campaigns) |
| 4. Ready for learning (RSI) from saves | **AT RISK** (data path fine; 4 named seams missing, none breaks a settled decision) |

## 1. Performance and memory management — ON TARGET

Evidence (enforced in code, not only in tests):

- Daemon self-limit: `src/sae/contracts/c7_governor.py:84-85` defaults 150 MiB breach / 300 MiB exit;
  `src/sae/daemon/loop.py` `poll_once` emits `budget_breach` once per game month and re-execs on
  `daemon_should_exit` with a 3-per-hour guard (`REEXEC_MAX_PER_HOUR`, `reexec.json`). RSS source is
  `psutil.Process().memory_info().rss` (`platform/sensors.py` `_daemon`), i.e. working set on Windows, RSS on Linux: comparable.
- Governor ladder: `governor/core.py` `_ram_level` / `_escalate` act on the same poll; faults are median+3×MAD with the
  1.0 pages/s floor; spm capped at H1; de-escalation needs 6 clean months with the 0.5 GiB margin (`_blocks_deescalation`).
  Config validator refuses H2/H3 bands the margin cannot bridge (C7 §9 invalid examples).
- Worker cap: Linux `RLIMIT_AS` (hard-limit aware, `MALLOC_ARENA_MAX=2`) or `systemd-run --scope MemoryMax`, `SCHED_IDLE`,
  `ionice -c3` (`platform/spawn.py:160-200`); Windows `IDLE_PRIORITY_CLASS` + Job Object (`_assign_job`). Breach → `budget_breach`
  and H2 (`governor/core.py` `on_worker`). CI-5 proves the Linux path under 128 MiB.
- Priority: `daemon/wiring.py:92` calls `lower_self_priority()` once (nice 10 / BELOW_NORMAL+EcoQoS); nothing touches the game.
- Telemetry: `telemetry/stream.py:25` `RSS_LIMIT = 300 MiB`, checked every poll, exits 1.
- Disk: `daemon/disk.py` 5 GiB warning at start + monthly; nothing deleted. Bus flushes per event, fsyncs once per game month.
- Measured (synth, this machine): see §5 — daemon peak RSS, bus bytes/month and 200-year extrapolation vs the ≤ 100 MB budget.
  T7's CI-4 child `VmHWM` = 41–46 MiB (docs/status/T7.md).

Risks:

1. **Contract drift** `governor/core.py:86-90`: a worker `budget_breach` still queues `Trigger.RAM` although C7 v1.1 and
   `Trigger.BUDGET_BREACH` (c7_governor.py:58) exist. The bus will blame RAM for a parser overrun.
2. **Telemetry cap drift**: resource proposal §8 says telemetry ≤ 150 MB; code and docs/components/T6.md say 300 MB. Either is fine
   for an opt-in dev tool, but the number must be in one place.
3. **Windows-only paths unexecuted** (Job Object, EcoQoS, PDH faults, `IsIconic`): mock-tested only (docs/status/T5.md gap 1).
   `RLIMIT_AS` is address space, not RSS; a Rust parser with large virtual mappings may abort below the real cap (documented).
4. Parser peak RSS is self-reported but **never published** (see §2), so "≤ 1 GB peak" is enforced by the cap, not observed.

Corrective actions: (a) T5: one-line fix, queue `Trigger.BUDGET_BREACH`; add the assertion in `tests/unit/t5`. (b) architect: pick
150 or 300 MB for telemetry and state it in C7 §4 or C8. (c) T8/M0: R-5 stays the only Windows evidence; keep it on the release gate.

## 2. Good telemetry — AT RISK

What the Engine Console promised (design report §"Watching every decision live") and what exists:

| Promise | Built | Where |
|---|---|---|
| Decision audit joined on `decision_id` (DuckDB view) | yes | `tools/console/views.sql` `v_decisions`, `v_intents`, `v_acks_final` |
| Beliefs: prior, loglik, posterior, top evidence | yes | `belief_update` payload; Rerun `belief/*`, `audit/belief` |
| Governor state, level, trigger | yes | `health level_change/state_change`, `health/level_index` |
| Alerts (multiplayer, Ironman, ini neutralised, ack alarm) | yes | `telemetry/mapping.py` `alert/*`, `ack_alarm` health |
| Proposed-but-unemitted intents | **partial** | validator rejections and Ironman/release are `intent_rejected`; but a batch deferred by the one-per-month rule or by `obs.m is None` (`core/engine_tick.py` run_tick, the two early `return`s) leaves **no bus line** |
| Monthly T0 metrics: spm, spm_med6/24, parse rss/cpu/wall, saves_skipped, workers_killed | **no** | `daemon/loop.py` `_metrics` emits only `daemon_rss_bytes`, `daemon_cpu_pct`, `available_ram_bytes`, `hard_fault_rate`. The Governor's spm windows are private; an OK parse's `cost` reaches `governor.on_worker` and is dropped (`daemon/saveparser.py` `_finish`, `health=None`) |
| Operator status | yes | `sae ctl status` → `EngineStatus` (C9) |

Within budget: telemetry is a separate opt-in process (`rerun-sdk` extra), tier-capped by level (`effective_tier`), 300 MiB self-limit.
Bus size measured in §5.

Risks: the resource proposal's whole monitoring story (R-1..R-6, "why did the Governor do that at 03:12") depends on monthly metrics
the bus does not carry. A user bug report will show level changes but not the spm medians or the parse cost that caused them.

Corrective actions: (a) T4+T5 (no contract change; keys are already listed in C5 §3): expose `spm_med6/spm_med24` and last
`WorkerOutcome.cost` from the Governor (a read-only property) and put them in the monthly `health`. (b) T4: emit one
`health kind=save_skipped`-style line (or reuse `invalid_event` with a clear detail) when a batch is deferred, so "proposed but
unemitted" is complete. (c) T6: add `v_deferred` to views.sql once (b) exists.

## 3. Properly adaptive AI engine — AT RISK (mechanics on target, evidence circular)

Mechanics, verified in code:

- Forgetful Bayes: `core/belief.py` `update` — fixed-share decay `(1-ρ)^k` toward uniform (ρ = 0.02/month, c8_config.py:44), evidence
  weight `w = min(1, k/12)`, per-feature |loglik| clipped at 3.0, probability floor. CUSUM `change_score` computed, never read by strategy.
- Fog-legal features: `core/features.py` reads only `player_legal` views with `in_contact=True`; unobserved → `None` and drops out of the
  product (`_raw_lls`). `at_war` is memoryless so late aggressors are caught (belief.py:63-65).
- Counter table + 24-month commitment + safety floor: `core/strategy.py` `select` order forced > commitment hold > table
  (threshold 0.45) > baseline; `_floor_ok` static check on the template; windows reopen only on change/force/expiry.
- Reward = `tanh(Σ w·Δ)` over managed AIs' own state only (`core/outcome.py`, weights HYP).
- Wave 2/3 evidence: acceptance 4/4 archetypes in 30 synth years; CI-1 rate 1.000 over 20 switches (H0 vs H2). Strategy counts and
  switch counts from this review's synth run are in §5.

Why this is "agrees with our own simulator" today:

1. `LikelihoodModel` defaults were **aligned to `synth.py PROFILES`** by construction (belief.py:36-38). The detector cannot fail on synth.
2. `outscale_siege` and `early_punish` expand to identical knobs (`core/expansion.py:749-750`: MILITARY + OFFENSIVE); only the reason key
   differs. Two of four counters are therefore the same action; adaptation in game terms is 3-way at best.
3. Knob efficacy (A-21/A-23) and the 24-month horizon are unverified; the reward is computed from AIs' own deltas, which also move
   for reasons unrelated to the strategy.
4. `expansion_rate` uses total player systems once in contact (A-19 approximation, C4 §3), which is more than the diplomacy screen shows.
5. Measured (§5): 200 synth years give 1 switch, 3 emitted batches and 99 outcome rewards all within ±0.02. The reward window carries
   no signal on synth; the loop is closed in structure, not yet in information.

Evidence that would change the verdict (owner): M2 **five hand-labelled real campaigns** replayed through `sae replay` with
leave-one-out likelihood fitting; top-1 accuracy above chance by year 30 and ECE ≤ 0.10 (report M2 exit). M0-8 A/B showing one knob
produces a visible difference over 15 years. Until then the belief machinery is correct and the numbers are guesses, and the docs say so.

Corrective actions: (a) T4: keep HYP labels; add a `likelihood_source: "synth-aligned"` note to `run_started` or the user guide so a
bus reader knows the tables are untrained. (b) architect/maintainer: decide whether `early_punish` gets a distinct template (needs a third
knob, C3 change) or the counter table collapses to 3 entries honestly. (c) T7: a replay test that fits likelihoods on one synth seed and
scores on another would at least show the fit is not memorised.

## 4. Ready for learning (RSI) from saves — AT RISK

What already supports the expansion proposal (no change needed):

- Replay evaluators (I1, leave-one-campaign-out): `bus.jsonl` carries `observation` (legal views) + `belief_update` + labels in synth;
  `sae synth --record` / LiveAdapter write the replay session format (`game.log`, `game.log.ts`, `saves/*.sav`), so saves can be
  re-extracted when the extractor changes (proposal §3.9 #4).
- Fog-perturbation test (§5.4 #4): `ReplayAdapter.__init__(parser=...)` (`adapters/replay.py:63-67`) is an injectable `SaveParser`;
  a wrapper that randomises `god` needs no contract change. `GodView` already leaves only via `Assembler._route_god`.
- Profile priors: `run_engine(state=EngineState)` (`daemon/loop.py`) accepts an initial `EngineState`, so `b_0` and ρ can be injected at
  campaign start without touching the engine.
- Settled decisions hold: no LLM/GPU imports (CI-4 pinned closure, 6 dists), runtime changes nothing but beliefs, policies are code.

Seams that are missing or must change (each is additive; none reopens a settled decision):

| # | Seam | Today | Needed | Owner |
|---|---|---|---|---|
| S1 | **PolicyVersion identity** | `run_started` has `engine_version` + `config_sha256` (EngineConfig only). `LikelihoodModel`, `COUNTER_TABLE`, `DEFAULT_EXPANSIONS`, outcome `WEIGHTS` are code constants with no hash | C5 additive field `policy_hash` (sha256 over the serialised likelihood tables + counter table + templates) and C8 `engine.policy_path` to load a versioned, human-approved JSON. Without it the gate's "scores bound to the candidate hash" and one-step rollback are impossible | architect + T4 (X2) |
| S2 | **Campaign identity** | `game_version` is parsed (C4 `meta`) but dropped before the bus; no playset hash; no `campaigns.jsonl`; `run_id` ↔ save folder only via `config.resolved.json` | C5 additive: `game_version` on `run_started` or `observation`; `runs/campaigns.jsonl` written by the daemon at start (run_id, save_dir, game_version, policy_hash, status) | T4 + T6 (X0) |
| S3 | **player_snapshot** | parser emits counts only (`tech_researched`, 6 sections); no tech list, policies, timeline events, starbase detail | C4 v1.x optional `snapshot` section (god-tagged, routed to `bus.god.jsonl`) and a new god-only event type; the legal bus stays unchanged, so fog rules hold by construction | T3 + architect (X0) |
| S4 | **Labels for real campaigns** | synth labels live only in the CLI schedule; recordings carry no manifest | a `label.json` next to a recording (`archetype@year` schedule or human labels) read by the evaluator | T2 + T7 (X1) |
| S5 | **Recording hygiene** | `--record` keeps raw `game.log` with PLYR and full `.sav` copies (Wave 3 N2); 5 GiB warning only | a retention/size cap under `paths` and a `strict` mode that stores snapshots instead of saves; needed before any profile store | T2 + T4 (X1) |

Held-out tuning and the two-stage gate need S1 and S4 only; the profile store needs S2, S3 and S5. Runtime self-modification stays off:
the daemon would load a policy file read-only at start, exactly as it loads config today.

## 5. Measurements (this machine, WSL2, synth adapter)

Command: `/usr/bin/time -v uv run sae synth --years 200 --runs-dir <scratch>/runs --record <scratch>/rec200`
(default single-archetype schedule, seed default). Run dir `20261019-214536-synth-2dbb`.

| Metric | Value | Budget |
|---|---|---|
| Peak RSS (`uv` + daemon, 200 game years) | 56,472 KB = 55 MiB | ≤ 150 MiB (CI-4 child VmHWM: 41–46 MiB) |
| Wall / CPU | 110 s wall, 12.2 s user + 6.5 s sys (17 %) | — |
| `bus.jsonl` | 10,796,767 B, 10,519 lines → 4.5 KB/game-month → **10.8 MB per 200-year campaign** | ≤ 100 MB |
| `bus.god.jsonl` | 979,432 B | ≤ 300 MB |
| `state.json` | 8,506 B | — |
| `sae telemetry --rrd` offline over this run | **peak RSS 194,840 KB = 190 MiB**, 20.2 s wall, `out.rrd` 4,182,347 B | proposal: ≤ 150 MB; code: 300 MiB exit (`stream.py:25`) |
| Recording (`--record`): 800 saves | 9.2 MB (fixture saves; real autosaves are 10–50 MB each → 8–40 GB per 200 years at monthly autosave) | 5 GiB warn only |
| Events | 2,706 observation, 2,400 belief_update, 2,400 strategy_selected, **3 intent_emitted**, 11 intent_ack, 99 outcome, 2,899 health (2,400 monthly, 494 save_skipped, 3 level_change, 1 state_change, 1 daemon_exit) | — |
| Strategy | 456 months baseline then 1,944 months outscale_siege; **1 switch** in 200 years; 0 `intent_rejected` | — |
| Outcome reward | 99 windows, all in **[−0.014, +0.018]** | reward ∈ [−1, 1] |

Reading: budgets hold with a 2× margin on RSS and 9× on bus size. 494 `save_skipped` and 3 level changes in a synth run show the
Governor reacting to this host's real RAM (WSL2, < 2 GiB available → H1), which is correct behaviour but means synth runs on a starved
dev box are not H0 runs unless `forced_level` is set. The reward column is the important number for §3: with a stationary synth player
the 24-month outcome signal is indistinguishable from zero, so nothing downstream (RSI I2, "feel" metrics) can be tuned on synth outcomes.

## 6. Top corrective actions

1. T5: `governor/core.py:89` queue `Trigger.BUDGET_BREACH` (contract drift).
2. T4+T5: publish `spm_med6/24` and last parse `cost` in monthly `health` (keys already in C5).
3. T4: emit a bus line for deferred batches so "proposed-but-unemitted" is complete.
4. architect+T4 (X2 prerequisite): `policy_hash` on `run_started` + versioned policy file (S1).
5. T4+T6 (X0): `game_version` on the bus and `runs/campaigns.jsonl` (S2).

Bottom line: the MVP enforces its budgets in code and keeps god data out of the engine; what it cannot yet do is prove to an outside
reader why the Governor moved or which policy made a decision, and those two gaps are exactly what the learning loop will need first.
