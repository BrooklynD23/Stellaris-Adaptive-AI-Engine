# T4 plan — Engine core and daemon loop

Owner: T4 lead. Paths: `src/sae/core/**`, `src/sae/daemon/**`, `tests/unit/t4/**`. Status: v2 (after adversary review, see §10 v2 rules).

Inputs: C3, C5, C6, C7, C8, C9 (frozen v1), design report "engine core v0" (authoritative), advisor review,
`plan_engine_core.md` (loses on conflict), TEAM_PLAN §T4. Archetypes and strategies are the contract's
(turtle, tech_rush, early_aggression, balanced; `COUNTER_TABLE` in `sae.contracts.common`).

## 1. Principles

1. `sae.core` is pure: no I/O, no asyncio, no clock, no randomness except a seeded `random.Random` passed in.
   Every step is `f(state, input, cfg) -> (new_state, outputs)` on frozen dataclasses/pydantic models.
2. `sae.core` imports only stdlib, pydantic, `sae.contracts`. `sae.daemon` additionally imports `sae.core`
   and stdlib/psutil. Neither imports `sae.adapters`, `sae.bus`, `sae.governor`, `sae.platform`,
   `sae.control`, `sae.telemetry` at module import time (the only exception: lazy imports inside
   `sae.daemon.wiring`, the single composition root). No god-path names (C6 §4 grep list) anywhere.
3. Unobserved is `None` and contributes nothing. Evidence from one save (`legal_as_of`) is counted once.
4. CUSUM ruling: only a diagnostic `change_score` in `belief_update`; nothing reads it back (test).
5. Game-date arithmetic: month index `mi = year*12 + (month-1)`; Stellaris months are 30 days (A-05 normalised
   in `GameDate`). Decision tick = first observation whose `mi` exceeds the last decided `mi`.

## 2. Work units (sequential; one implementer at a time; disjoint files)

| Unit | Files | Content |
|---|---|---|
| U1 belief | `core/dates.py`, `core/features.py`, `core/belief.py`, `tests/unit/t4/test_features.py`, `test_belief.py` | features, likelihood model, forgetful Bayes, change_score |
| U2 decide | `core/strategy.py`, `core/expansion.py`, `core/validator.py`, tests | counter table + commitment + forced + safety floor; desired-state expansion; C3 §4 validator |
| U3 lifecycle | `core/lifecycle.py`, tests | seq allocator + floor rule, ack tracker, LOST/SUPERSEDED, re-emission, alarm |
| U4 engine | `core/engine.py`, `core/outcome.py`, `core/state.py`, tests incl. fake synth | the pure month step; decision ids; outcome; engine snapshot model (state.json payload) |
| U5 daemon ports | `daemon/ports.py`, `daemon/saveparser.py`, tests | BusSink/ControlInbox protocols, DaemonDeps; Governor-gated SaveParser |
| U6 daemon loop | `daemon/loop.py`, `daemon/control.py`, `daemon/statefile.py`, `daemon/__init__.py`, tests | `run_engine`, C9 application, state.json, re-exec, monthly health |
| U7 CLI | `daemon/cli.py`, `daemon/wiring.py`, `daemon/runs.py`, tests | `sae daemon`, CURRENT lock, `--resume`, composition root |

## 3. Component design

### U1 features (`core/features.py`)
Five features, aggregated over `player_legal` views with `in_contact=true` only:

| Feature | Definition | Aggregation |
|---|---|---|
| `border_fort_ratio` | fortified_border_starbases / frontier_systems | sum(fort)/sum(frontier) over views with both set and frontier > 0; else None |
| `expansion_rate` | systems gained per game year | max visible `systems_owned`; rate between the oldest sample ≥ 12 months before and now (window 12–36 months); else None |
| `fleet_power_cat` | RelCat −2..2 | median (lower median) of set values |
| `at_war` (v3) | player at war now (`player_war_count>0` or `at_war_with_observer`) | any in-contact view; None if no view carries war info; Bernoulli `p(at_war | a)` every evidence tick |
| `first_war_month` (report-only since v3) | months from campaign start (2200.01) to first observed war | censored: `(value, observed: bool)`; uncensored when any view shows `player_war_count>0` or `at_war_with_observer`; censored value = months elapsed; None before any in-contact view |
| `tech_cat` | RelCat | lower median |

`FeatureMemory` (frozen) keeps the systems-sample history (≤ 48 entries, keyed by legal_as_of mi) and first-war mi.
New evidence only when `legal_as_of` differs from the last consumed one; otherwise features are reported but
`new_evidence=False`. Date regression: drop samples after the new date; first-war cleared if after it.

### U1 belief (`core/belief.py`)
- Likelihoods (`LikelihoodModel`, frozen, defaults labelled HYP, calibrated in M2): Gaussian (μ, σ) per
  archetype for `border_fort_ratio`, `expansion_rate`; categorical p(−2..2) for the two RelCats; exponential
  rate for `first_war_month` — **replaced in v3** by Bernoulli `p(at_war | a)`; `first_war_month` is report-only.
  `balanced` is the broad catch-all (largest σ, near-flat categorical).
- Step: `prior = u + (1−ρ)^k (b − u)` for `k` elapsed months (k=0 → no forgetting). If `new_evidence`:
  per feature `ll_f(a)` centred by subtracting the mean over archetypes, clipped to `[−L, L]`;
  `post ∝ prior · exp(Σ_f ll_f)`; else `post = prior`. Probabilities floored at 1e−6 then renormalised.
- Output `BeliefUpdatePayload`: prior, loglik (Σ clipped), posterior, entropy (nats), features dict
  (`first_war_month` as float or None), top_evidence ≤ 5 by |llr| desc then name, `change_score`.
- `change_score`: one-sided CUSUM of surprise `s_t = −log Σ_a prior(a)·exp(Σ ll)` minus running mean,
  `S_t = max(0, S_{t−1} + s_t − κ)`, κ = 0.5. Stored in `BeliefState`; never read by strategy/engine.

### U2 strategy (`core/strategy.py`)
`StrategyState`: strategy, commit_from_mi/commit_until_mi (+ GameDates), opened_by decision_id, forced
(strategy, until_mi) | None, window_open_metrics. Order each tick:
1. Forced override active → forced strategy, reason `forced`, `reason_key=operator_forced`; a new force or a
   clear opens a new window immediately (operator may switch inside a commitment).
2. Inside commitment → hold, reason `commitment_hold`.
3. Top archetype ≥ `belief_threshold` → `COUNTER_TABLE`, reason `table`; else `baseline`, `below_threshold`.
   Ties broken by `Archetype` declaration order. A window (24 months) opens whenever the strategy changes
   **or** the previous window expired (so outcomes are emitted per window).
4. Safety floor (`safety_floor` reason): if the chosen non-baseline strategy expands to zero valid intents for
   every in-contact managed AI after validation (U2 validator), the tick falls back to `baseline` and logs it.
   Floors (1) additive mod entries and (2) the validator rule are structural (C3 §6).
- Date regression below `commit_from_mi`: commitment and force are cleared; the next tick re-evaluates.

### U2 expansion (`core/expansion.py`)
`EXPANSIONS: Mapping[Strategy, Template]` = C3 §6 table (configurable defaults). For each managed AI whose
legal view is in contact: SET_ECON_PLAN + SET_MILITARY_POSTURE(target=player) + FLEET_COMPOSITION_BIAS
(sizes not named keep 50 → full 5-size map, so state is complete), reason_key from the strategy on the econ
intent. Not in contact: no intents. `desired_state(country)` is the full restatement (desired-state rule).
`intent_id` from a monotone counter in engine state (`i-%06d`); `ttl_months` from config.

### U2 validator (`core/validator.py`)
`validate(intents, ctx) -> (accepted, rejected[(intent, RejectRule, detail)])`, rules in C3 §4 order:
schema (re-validate via `INTENT_ADAPTER`), reserved_kind, mode, not_managed, bad_target, not_in_contact,
safety_floor, rate_limit (batch-level: second batch in a month unless re-emission; > max_managed countries →
countries beyond the first `max_managed` by sid rejected). Pure; ctx is frozen.

### U3 lifecycle (`core/lifecycle.py`)
- `SeqState(next_seq)`; floor rule from ManagedState.applied_seq/pending_seq, every ACK seq, `applied` on
  DUPLICATE/SEQ_OLD (→ `resync_needed=True`, engine re-emits desired state next tick).
- `InFlight(country, seq, attempt, issued_mi, intent_ids, intents)`. Mod ACK → terminal, `intent_ack` with
  `ack_lag_months`. Higher-seq ACK for the country → older in-flight SUPERSEDED/NEWER_BATCH. On each month:
  in-flight with `mi − issued_mi ≥ ACK_TIMEOUT_MONTHS` → LOST/TIMEOUT; attempt 1 and no newer batch for the
  country → re-emission request (same intents, new seq, attempt 2); attempt 2 → `ack_alarm` (repeat ≤ once per
  12 months while no ACK arrives at all). Late ACK after LOST/SUPERSEDED still yields its own event.
- `in_flight_batches` for `EngineStatus` = distinct seqs in flight.

### U4 engine (`core/engine.py`, `core/outcome.py`, `core/state.py`)
`EngineState` (pydantic, frozen, `state_version=1`): features memory, belief, strategy, lifecycle, seq,
counters (intent id, decision counter per month), last decided mi, last batch mi, suspended flag, pending
operator commands, last observation summary. It **is** the `state.json` payload.
- `on_observation(state, obs, ctx) -> (state, Outputs)`; `on_ack(state, ack) -> (state, Outputs)`;
  `on_control(state, cmd) -> (state, ControlResult, Outputs)` (status immediate; others queued and applied at
  the next tick boundary in command_id order); `on_emitted(state, batch, receipt)`.
- `Outputs`: tuple of `EventDraft(type, payload, game_date, decision_id)` + optional `IntentBatch` + health
  drafts. The daemon turns drafts into C5 events via the sink.
- Month tick order: lifecycle month step → belief → strategy → expansion → validator → batch (if desired
  state changed for any country, a country newly in contact, resync, re-emission, or a Governor knob request;
  else no batch) → outcome if a window closed. Suspended: belief only; no strategy/expansion/batch
  (C9 pause = level S). Release mode: expansion + validator run, everything rejected by `mode` but only
  on would-emit ticks (no rejection spam).
- `decision_id = d-YYYY.MM-NN`, NN per game month 00–99; > 99 decisions in a month → reuse 99 and health
  `invalid_event`-free warning (cannot happen at ≤ 1 tick per month; tested).
- Outcome (`core/outcome.py`, HYP): metrics from managed AIs' own fog-legal `ManagedState`
  (Δ fleet_power share of naval cap, Δ systems_owned, Δ alloys_income over the window, averaged over AIs
  in contact at window open); `reward = tanh(Σ w_i·m_i)` clipped [−1, 1]; `forced` flag; the opening decision_id.

### U5 daemon ports and save parser
- `ports.py`: `BusSink` protocol `emit(draft: EventDraft) -> None` + `flush()/close()`; `ControlInbox`
  protocol `poll() -> Sequence[InboxItem]`, `complete(item, result)`; `DaemonDeps` frozen dataclass (sink,
  god_sink, governor, sensors, platform, inbox, parser_exe, monotonic clock). Contract proposal 004 (§8).
- `saveparser.py`: `GovernedSaveParser` implements C6 `SaveParser`: counts eligible saves and honours
  `parse_every_n` (0 → skip), `parse_window_only` (save date within `h2_parse_window_months` before
  the engine's next boundary), `worker_threads`, `worker_mem_cap_bytes`, timeout; spawns via `Platform`;
  classifies per C4 §2 (ok / clean_failure / budget_breach / killed_by_governor); `governor.on_worker`;
  health `parse_failed`, `save_skipped`, `worker_killed`, `budget_breach` drafts; kill on `kill_worker`.
  Stdout cap 4 MiB. Never raises into the adapter.

### U6 daemon loop
`run_engine(config, adapter, run_id, deps, state: EngineState | None) -> int`. Tasks under one
`asyncio.TaskGroup`: observations consumer, acks consumer, governor poll (`poll_s`), inbox poll (2 s).
All engine calls serialised on one task via an `asyncio.Queue` (no shared mutation). State.json written
atomically (`.tmp` + `os.replace`) after each tick. Governor: level/state change → health; `knob_request`
→ queued into the next batch's `knobs`; `daemon_should_exit` → flush, `health daemon_exit`, re-exec per C7 §4.6
via `deps.reexec` (injected; tests use a fake). SpmSample from consecutive HB month counters (`m`, source=log).
RSS > 150 MiB → `budget_breach` once per game month. Adapter iterator end (replay/synth) → exit 0.
Engine exceptions: logged as health `daemon_exit` detail, exit 1; bus flushed in `finally`.

### U7 CLI and wiring
- `cli.py`: `main(command, argv) -> int`; common flags (C8 §5) + `--adapter live|release` + `--resume`; TOML via
  `tomllib`; `resolve_config(file, env_layer(os.environ), cli)`; exit 2 usage, 3 invalid config, 1 runtime.
- `runs.py`: run dir creation, `config.resolved.json`, CURRENT lock rules (C8 §6) with psutil pid check.
- `wiring.py`: the only module that imports other teams' packages, lazily, by the factory names in
  proposal 004. Missing factory → stderr `sae daemon: <component> not available yet (owner Tn)`, exit 1.

## 4. Tests (all deterministic, `tests/unit/t4/`)

| Area | Tests |
|---|---|
| belief | posterior sums to 1 (hypothesis over features/priors); monotone: more turtle-like `border_fort_ratio` never lowers P(turtle); clipping: one extreme feature moves log-odds by ≤ 2L; `None` features → posterior == prior; repeated same `legal_as_of` counted once; forgetting pulls toward uniform; change_score never alters posterior/strategy |
| features | out-of-contact views ignored; hypothesis over arbitrary `LegalView` tuples never raises, never yields NaN/inf; regression drops memory |
| strategy | no switch inside 24 months unless forced/safety floor (hypothesis over posterior sequences); threshold; forced + clear; windows emit outcome |
| expansion/validator | one test per `RejectRule` (8); desired-state completeness; order of rules |
| lifecycle | APPLIED, SUPERSEDED, LOST→re-emit→LOST→alarm, alarm repeat 12 months, late ACK, seq floor from state/obs/ACK/DUPLICATE |
| engine | fake synth per archetype (4 archetypes × 4 seeds): correct counter by game year 30 in ≥ 3 of 4 seeds; suspended emits nothing; release rejects with `mode`; state.json round-trip resumes identically; every emitted payload validates as its C5 model |
| daemon | fake adapter/governor/sensors/platform/sink/inbox: chain observation→belief→strategy→intent→ack shares decision_id; Governor-gated parse (every-n, window-only, kill); C9 at tick boundary; re-exec path; RSS breach once/month |
| boundary | import test: `sae.core`/`sae.daemon` modules never import `sae.adapters`/bus/governor/platform/control/telemetry at import time (subprocess `sys.modules` check); grep for C6 §4 god names; no GPU packages in `sys.modules`; RSS of a 120-year fake synth run ≤ 150 MiB (psutil, Linux) |
| CLI | usage errors, `--resume` flag rules, CURRENT lock (alive/dead pid), invalid config → 3 |

## 5. Acceptance criteria
Exit gate: `uv run pytest -x -q`, `uv run mypy --strict src/sae/core src/sae/daemon`, `uv run ruff check` clean.
All TEAM_PLAN §T4 acceptance items above. No TODO stubs.

## 6. Risks
1. Hand-authored likelihoods misclassify real players (HYP until M2 calibration) — defaults in one table, configurable.
2. T2/T5/T6 factory names unknown — composition root isolated in `wiring.py`; proposal 004.
3. Contract chicken-and-egg: replay/synth adapters need `GodSink` + `SaveParser` that the daemon owns — proposal 004.
4. `expansion_rate` from *visible* systems conflates contact growth with expansion — window ≥ 12 months, documented.
5. Fake-synth acceptance proves self-consistency only; the real synth gate is T7/Wave 2.

## 7. UNVERIFIED game assumptions used (labelled in code)
A-05 date format; A-21 econ/posture knobs move behaviour; A-22 ship-size knob; A-23 opinion modifier;
A-30 save stores sae vars (seq floor); A-32 contact detection (`sae_managed`). Sent to T8 via status file.

## 8. Contract proposals
- `contracts/proposals/004-T4-daemon-adapter-factory.md`: (a) `BusSink` protocol in C5 (engine hands event drafts,
  writer stamps envelope); (b) `ControlInbox` protocol in C9; (c) `sae.daemon.open_runtime(config, run_id) ->
  DaemonRuntime` exposing `god_sink` and `save_parser` so T2's replay/synth CLIs can build adapters before
  `run(config, adapter, run_id, runtime=...)` (optional 4th arg, additive); (d) factory names for T5/T6/T2.

## 9. Expansion backlog
Bandit across campaigns (condition C); CUSUM-triggered commitment breaks (maintainer decision);
BetweenSessionEmitter for release mode; per-(observer, target) fog mask; per-AI beliefs; calibrated
likelihoods from M2; fleet_conc / fleet_near_border features after the mask decision; LLM condition D (M4).

## 10. Revision log
- v1: initial.
- v2 (adversary review, 20 findings, all accepted; these rules OVERRIDE §1–§8 where they conflict):
  1. Safety floor = static template check: chosen strategy's expansion template empty or any fleet weight
     < `safety_floor_ship_min` → `baseline`, reason `safety_floor`. Strategy never reads validator results.
  2. Session reset (`session_epoch` change or `DATE_REGRESSED`): `last_decided_mi = obs.mi − 1`; every in-flight
     `issued_mi = obs.mi`; `resync_needed = True`; feature memory dropped after the new date; commitment
     re-anchored to `[mi, mi + remaining]` (not cleared); force re-anchored `until_mi = mi + remaining`;
     last-consumed `legal_as_of` reset; first-war censor anchor reset.
  3. `obs.m is None` → no batch (defer; `resync_needed` stays set). Tested.
  4. Forgetting `k` = months since the last *evidence* step, applied only on evidence ticks; no-evidence
     ticks leave belief unchanged. Test: dropping every 2nd/3rd save (H1/H2 sparsity) yields the same strategy
     at commitment boundaries (CI-1 property).
  5. Evidence weight `w = min(1, k / evidence_period_months)` (new T4-internal default 12, not a C8 key)
     multiplies Σ clipped ll. L stays 3.
  6. `first_war_month` censoring anchored at the first in-contact observation (and each session reset), not
     2200.01; mid-campaign install assumption labelled HYP.
  7. Evidence only when `legal_as_of` > last consumed in the epoch; older saves → `new_evidence=False`.
  8. `EmitReceipt.ok=False` → batch not in flight, health `ini_write_failed`, `resync_needed=True`.
  9. One batch per tick. A due desired-state batch wins over a LOST re-emission (the LOST batch stays LOST;
     it is superseded by any later ACK). Attempt-2 re-emission only when nothing else is due.
  10. C9: commands applied between any two serialised engine calls (as soon as the queue drains), not at a
      month tick; `applied_at` = adapter clock date. Daemon calls `governor.suspend()` on pause/resume and
      `governor.set_next_boundary(commit_until)` after every strategy step.
  11. Batch need is computed against the last *decided* desired state (release mode: rejections only when
      desired state changes).
  12. No `ParseResult`/stdout text in any health detail; test that `bus.jsonl` never contains key `"god"`.
  13. Fake synth draws raw `LegalView` fields from test-local numeric generators with noise (not from
      `LikelihoodModel`). change_score test: stub constant vs real → identical strategy/batches.
  14. Exact duplicate ACKs deduped (last 64 (country, seq, status)); ACK for unknown (country, seq) →
      `intent_ack` with `intent_ids=()`, attempt 1, lag None.
  15. SpmSample `wall_s` clamped > 0.
  16. `Evidence.llr` = clipped ll(a) − mean ll over the *other* archetypes (C5 wording); the update itself
      centres on all archetypes (equivalent for the posterior; documented).
  17. RSS test measures delta over the pytest-process baseline.
  18. Proposal renumbered **004** (architect; 005 = unloggable rejections). T4 only *applies* `ControlCommand`s; the inbox reader is T6's (C9) and
      reaches T4 through the internal `ControlInbox` port.
  19. `--resume` with missing/corrupt `state.json` → exit 1. Pid check matches `sae` in the cmdline.
  20. One-month evidence lag documented (save parsed after the month's HB tick); no re-tick.
  Seam (advisor): proposal 004 = C8 §5 signature `run(config, build_adapter: Callable[[SaveParser, GodSink],
  GameAdapter], run_id)`; daemon keeps sole ownership of bus, parser and run dir. `BusSink`/`ControlInbox`
  stay T4-internal ports (no contract entry). Until accepted, T4 also exposes the current
  `run(config, adapter, run_id)` (adapter built without a governed parser) so nothing breaks.
- v3 (2026-10-19, demo blocker: real `sae synth` early_aggression → outscale_siege, 30-year drift in T7 CI-1):
  root causes (a) HYP likelihood scales far from the synth profiles, (b) the observed first-war pdf entered once
  while snapshot features (border ratio, zero expansion after the galaxy fills) repeated every save, (c) a lifetime
  first-war-month pdf keeps voting for the old archetype after a turtle→aggressor switch. Fix: likelihood tables
  aligned with T2 synth PROFILES (HYP until M2); `first_war_month` report-only (its censor anchor now affects
  only the reported value); new memoryless state feature `at_war`. Acceptance: `tests/unit/t4/test_real_synth.py`.
