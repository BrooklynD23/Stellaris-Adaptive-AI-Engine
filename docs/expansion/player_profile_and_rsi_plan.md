# T9 plan: player profile and bounded self-improvement (RSI)

Owner: T9 lead (sae-team-lead). Date: 2026-10-19. Status: PLAN (no code). Settled decisions hold: no LLM in the
runtime loop; zero GPU bytes on the player's machine through M4; three processes; the game's priority/affinity
is never touched; Ironman has no live loop; **runtime self-modification stays OFF** — the daemon loads one
hashed, human-approved policy read-only at start and never changes it during a run.

Inputs: `context/research_notes/Player profiling and RSI/` (`proposal.md`, `verification_notes.md`,
`haiku_reverification.md` and `fable_review.md`, both produced for this plan), `docs/status/ARCHITECTURE_alignment_review.md`
(seams S1–S5), `docs/TEAM_PLAN.md` "Validation backlog" (V-1..V-5), contracts C1–C9, proposals 001–017.

## 0. What changed versus the research proposal

| Research proposal | This plan | Why |
|---|---|---|
| `player_snapshot` appended to `bus.jsonl` with mixed legality | No new legal event. God snapshot (if ever) rides inside `GodView` to `bus.god.jsonl` (023, conditional) | Fable B1; C5 §2 fixes `fow` per type; C6 §4.2 |
| `reputation` (god-view aggregates) as default profile mode | v1 is `strict` only; `reputation` is maintainer decision D1 | Fable B2: a prior built from god data is god data with extra steps |
| Campaign = run, closes after N idle days | Campaign = (hashed save folder, start date); closes only explicitly | Fable B3 |
| Stage 2: 10 pairs/archetype, LCB > 0 and ≤ 5 pp per matchup | Detector candidates gated on replay (no game runs); game-paired Stage 2 only after M4, continuous reward, n ≥ 30, 5 pp as a reported diagnostic | Fable B4 (SE ≈ 15 pp at n = 10) |
| Profile R1–R5, backfill, self-report, outer LLM loop | R1 (b0) only reaches the engine, and only after ≥ 8 closed campaigns with a positive fold score; rest cut or parked | Fable M3 and cut list |
| "pure NumPy" | stdlib `statistics`/`math`; no new dependency | Fable M8, CI-4 lock closure |

Citation corrections (Haiku + Fable) are recorded in `fable_review.md`; this plan cites only the paper numbers for
AIDE² (7 of 99 accepted; reward hacking 55 → 39 → 32 %), says six invariants for Falsifiable Release Gates, and
treats the Clark 2027/2028 figures as UNVERIFIED (primary 404). None of them changes a design choice here.

## 1. Contract proposals (all PROPOSED; architect decides)

| File | Contracts | What | Seam |
|---|---|---|---|
| `contracts/proposals/023-T9-player-snapshot-godview.md` | C4 v1.1 | optional `GodView.snapshot` (god file only). **Conditional on D1** | S3 |
| `contracts/proposals/019-T9-campaign-key-and-priors-loaded.md` | C6 v1.3, C5 v1.3, C8 §5.1 v1.2 | Part A: `Observation.save_folder_key`, `Observation.game_version`; `run_started.policy`; `open_sink(..., policy=)`. Part B (`priors_loaded`, contamination rule): **DEFERRED** | S1, S2 |
| `contracts/proposals/020-T9-profile-store-and-campaign-index.md` | new C10 §1–§2 (§3 deferred), C8 v1.2 | `<sae_dir>` layout, `campaigns.jsonl`, `CampaignLabel` (also `label.json` in recordings), `CampaignSummary`, `ProfileDoc`; `EnginePriors` **DEFERRED** | S2, S4 |
| `contracts/proposals/021-T9-policy-version-and-registry.md` | new C10 §4–§6, C8 v1.2 | `PolicyVersion` (detector mutable, counter table/templates/weights fixed), append-only registry, `Preregistration`, `GateReport` | S1 |
| `contracts/proposals/022-T9-recording-hygiene.md` | C8 v1.2, C6 §3, C5 v1.3 | `record.max_bytes`, `saves` none/every_n/all, PLYR redaction in recordings, `health record_capped` | S5 |

New bus content now: one `run_started` field, two `Observation` fields and one health kind; no new event type (`priors_loaded` is deferred). Everything else
(campaign summaries, profiles, policies, gate evidence) is an offline file under `<sae_dir>`, not a bus event.

Process topology: unchanged. `sae profile`, `sae tune`, `sae policy`, `sae gate` are offline CLI commands like
`sae replay`/`sae bench`: not resident, never imported by the daemon (CI-4 `FORBIDDEN_SAE` gains `sae.profile`,
`sae.tune`; `sae.bench` is already listed), and `sae profile update`/`sae tune` refuse to run while the game process is
detected (T5 `make_sensors`) unless `--force`, at idle priority (T5 `lower_self_priority`).

## 2. The snapshot-and-diff extractor

### 2.1 v1: legal stream only (no parser change)
The v1 profile needs no new extraction. Every input already sits on `bus.jsonl`: `observation.player_legal`
(C4 §3 `LegalView`s), `belief_update.posterior`, `strategy_selected`, plus the new `save_folder_key` and
`game_version` (019). The "diff" is over successive `LegalView`s of one campaign:

| Profile feature (C10 §2) | C4 / bus source | Diff rule |
|---|---|---|
| `expansion_rate_early`, `expansion_rate_late` | `systems_owned` | Δ per game year, split at year 30 |
| `border_fort_ratio` | `fortified_border_starbases`, `frontier_systems` | monthly ratio, mean |
| `first_war_year`, `war_share` | `player_war_count` | first month > 0; share of months > 0 |
| `fleet_power_cat_mean`, `tech_cat_mean`, `economy_cat_mean` | the three `*_cat` | mean |
| `final_belief` | last `belief_update.posterior` | — |

Per month, views with `in_contact = true` are averaged exactly as `sae.core.features` does, so the profile sees what
the engine saw. Time resolution = save interval (C6 §2 `legal_as_of`). Reloads: order by run `t_wall`; a date
regression drops later-dated observations of the abandoned branch (Fable M1).

### 2.2 Conditional (D1): god snapshot
If the maintainer approves D1, proposal 023 adds `GodView.snapshot` (colonies, upgraded starbases, border
fortification against any neighbour, naval cap use, fleet count and concentration, hull counts, research income by
category, war starts). It reuses the parser's existing six-section walk, goes to `bus.god.jsonl` through the
unchanged god path, and is read only offline. Keys are UNVERIFIED A-47..A-49 until M0-11.

### 2.3 Fog boundary
1. Now the daemon reads no profile file at all. When 019 Part B / 020 §3 are re-submitted it will read exactly one,
   `priors.json` (4 probabilities, hashes, counts). It never opens `bus.god.jsonl`, `campaigns.jsonl`, `profile.json`
   or summaries (grep test, §3 F4).
2. Profile inputs are `fow = legal` lines of **closed, live/release-mode** campaigns only. When priors reach the
   engine (deferred), a campaign whose folder fed the profile gets designer b0 (`self_contaminated`).
3. Profile schemas hold no coordinates, ids from the save, names or paths; folder keys are HMAC-salted (019).

### 2.4 Cost against the resource budgets

| Where / when | Added cost | Budget (resource proposal) |
|---|---|---|
| Daemon, PLAYING | one policy JSON read (≤ 1 MiB) at start; two extra short fields per observation | RSS ≤ 150 MiB (measured 55 MiB, arch. review §5); unchanged |
| Parser, PLAYING | none in v1. With 023: ≤ +10 % `cost.wall_s`, RSS unchanged, ≤ 2 KiB more JSON | ≤ 3 s, ≤ 1 GB, stdout ≤ 4 MiB |
| `bus.jsonl` | ≈ +40 B/observation (two fields) → ≈ +0.2 MB per 200 years | ≤ 100 MB (measured 10.8 MB) |
| `bus.god.jsonl` | none in v1. With 023: ≤ 2 KiB/save → ≈ 5 MB per 200 monthly years | ≤ 300 MB |
| Recordings | capped at 4 GiB by default (022) | disk: 5 GiB warning today |
| `sae profile update`, CLOSED | streams buses line by line; target ≤ 30 s CPU and ≤ 200 MiB RSS for 20 campaigns; idle priority | no in-game cost (game not running) |
| `sae tune detector`, CLOSED | 180 grid points × 5 folds on the 5-campaign tune split ≈ 2 M belief steps; feature vectors precomputed once, `_raw_lls` called directly (no pydantic per step); target ≤ 30 min, one thread (Fable plan review 5) | same |
| GPU | none, anywhere | zero GPU through M4 |

## 3. The fog-perturbation test (TEAM_PLAN V-5, extended)

V-5's criterion is "belief change = 0 for every god-only perturbation". This plan keeps it and makes it stricter in
one way (whole decision stream, not only beliefs) and wider in two (CI on synth, past campaigns).

| Check | Perturbs | Through | Must hold | Runs |
|---|---|---|---|---|
| F1 engine boundary | every `GodView` field (and `snapshot` if 023) replaced by seeded random values, all `PLYR` numbers randomised in the session's `game.log` | `ReplayAdapter(parser=PerturbingParser(real))` wrapper (existing seam, `src/sae/adapters/replay.py:63`) | `bus.jsonl` lines of type `observation`, `belief_update`, `strategy_selected`, `intent_emitted`, `intent_rejected`, `outcome` byte-identical after dropping `t_wall`, `t_mono_ns`, `run_id`, `save_folder_key`; `bus.god.jsonl` **differs** (non-vacuity) | CI on synth recordings (T7, every PR); V-5 on real recordings (M2) |
| F2 parser boundary | `FixtureSpec` fields no C4 §3 legal definition reads (player energy, minerals, alloys stockpile, naval cap; 023 snapshot inputs) | render both specs with `sae-parse fixture`, parse both | `legal` and `self_state` equal; `god` differs | CI (T7 + T3 fixtures) |
| F3 past campaigns | every past campaign's `bus.god.jsonl` replaced by random valid god events (Fable B2) | `sae profile update --include-synth` on a multi-campaign fixture | `profile.json` byte-identical (and `priors.json` once deferred §3 exists) | CI |
| F4 code boundary | — | grep of `sae/core`, `sae/daemon` for `bus.god`, `GodView`, `GodObservation`, `PlayerRecord`, `campaigns.jsonl`, `profile.json`, `summaries/` and import graph (`sae.profile`, `sae.tune` forbidden) | no match | CI (extends C6 §4.4 test) |
| F0 sensitivity control | a test-only assembler mutant that copies `god.fleet_power` into the `LegalView.fleet_power_cat` it yields (god data cannot enter `Observation` by type, C6 §4.1, so the mutant must sit at the assembler) | F1 harness | F1 **fails** | CI; proves F1 is not vacuous (Fable m2) |

Approved approximation channels are excluded from F2 by name, because C4 §3 legal features are deliberately computed
from the player's true fleet power, researched-tech count, alloys income, systems, frontier starbases and wars
(report decision 3, A-19). The list lives in the test and changes only with C4.

## 4. Held-out-campaign detector tuning (TEAM_PLAN V-1, V-3)

Goal: replace the synth-aligned HYP likelihoods (`src/sae/core/belief.py:29`; architecture review §3: the detector
"cannot fail on synth") with values fitted on real labelled campaigns, without leaking the campaigns used to judge them.

1. **Data.** Labelled campaigns: `bus.jsonl` observation streams (legal views; no re-parse needed) plus a
   `CampaignLabel` (020) written blind (`sae profile label` does not show SAE's beliefs). Recordings carry the same
   model as `label.json` (S4); synth recordings write it from the schedule.
2. **Splits (fixed by label order, C10 §1).** Labelled #1–#5 = `tune` and are exactly V-1's five campaigns.
   #6, #10, #14 … = `sealed`. Split rows are never rewritten; a sealed campaign retires to `tune` after ≥ 10 sealed
   queries once 2 newer sealed campaigns exist.
3. **Fit (`sae tune detector`, T9).** Shrinkage toward the parent policy, not free fitting: Gaussian features
   (`border`, `expansion`) get Normal–Inverse-Gamma posteriors centred on the parent's (μ, σ) with κ pseudo-months;
   categoricals (`fleet`, `tech`) Dirichlet with pseudo-counts κ·p₀; `war_now` Beta(κp₀, κ(1−p₀)). Grid over
   κ ∈ {1, 3, 10, 30, 100}, ρ ∈ {0.01, 0.02, 0.04, 0.08}, clip ∈ {2, 3, 4}, threshold ∈ {0.40, 0.45, 0.55}
   (180 points). No CMA-ES: 180 points of a pure function is enough and is reproducible.
4. **Objective (public signal, tune split only).** Leave-one-campaign-out: fit on 4, score the 5th, rotate. Score =
   mean monthly log loss of the posterior against the label after a 12-month burn-in; constraints ECE ≤ 0.10 on
   **each** held-out campaign and correct counter (C3 §6 table applied to the label) on ≥ 4 of 5 — exactly V-1's pass
   criterion. Months are autocorrelated, so the campaign is the unit; report per-fold values, never a pooled CI.
5. **What the tuner cannot touch.** It loads campaigns only through `campaign_state()` with `split == "tune"`; its
   `TuningManifest.campaign_ids` is checked by Stage 1 (any sealed id → fail). Synth stand-ins: tuner seeds
   0–9 999; the gate draws fresh seeds ≥ 1 000 000 per query, but synth results are a **reported diagnostic only**
   (the synth generator is what the old tables were aligned to; Fable plan review 8). Gating evidence = real campaigns.
6. **Sealed use.** Only `sae gate stage2` reads sealed campaigns, once per candidate, and reports pass/fail plus a value
   rounded to 2 significant digits (limits adaptive overfitting). With 1–3 sealed campaigns this is a **tripwire**
   (non-inferior on every sealed campaign by the pre-registered margin), not a significance test; the plan says so in
   every GateReport.
7. **Signal check (V-3).** Before any fitted policy is proposed, V-3 must pass on the same data (> 1 switch where the
   player changes style; rewards not all within ±0.02). If V-3 fails, the detector may still be recalibrated, but no
   reward-based metric may be pre-registered.
8. **Honest limit.** All campaigns come from one player. A `fitted_real` policy measures generalisation across that
   player's campaigns only; it stays a personal policy on that player's machine unless the maintainer decides
   otherwise (D4).

Difference from V-1, stated explicitly: V-1 uses all five campaigns for leave-one-out and has no untouched
campaign; this plan keeps V-1 unchanged as the M2 exit and adds a sixth, sealed campaign before the first
`fitted_real` policy can be promoted. Reason: a gate whose holdout was used to choose the method is not held out.

## 5. Two-stage acceptance gate, versioned policy, rollback

### 5.1 Policy format (C10 §4, proposal 021)
`PolicyVersion` JSON: semver, game-version pin, parent hash, `detector` (ρ, clip, threshold, likelihood tables —
the only mutable surface), `fixed` (counter table = C3 §6, templates, outcome weights — copied, hashed, never tuned),
`tuning` manifest with `likelihood_source` (`synth_aligned` for the built-in 0.1.0). Identity = sha256 of canonical
JSON; files are content-addressed. The hash goes on `run_started.policy` (019, S1).

### 5.2 Stage 1 — "did it work?" (`sae gate stage1 <sha>`, T7, minutes, automatic)
1. Schema valid; file name = hash; parent exists; **mutable-surface check**: diff vs parent only under `detector`
   and `tuning`; `fixed` identical; pin compatible.
2. Tuning manifest ∩ sealed campaigns = ∅; seeds outside the gate range.
3. Repo suite with the **built-in** policy (`uv run pytest -x -q`, `uv run mypy --strict`, `uv run ruff check`, CI-1..CI-5),
   then a named `gate_replay` subset with the candidate pinned (contract tests, F0–F4, determinism, no crash on every
   recorded session). CI-1 thresholds are synth-tuned and would reject every `fitted_real` candidate (Fable plan review 4).
4. Fog checks F0–F4 (§3) with the candidate.
5. Replay determinism: same bus hash twice at `speed=max`, after dropping `t_wall`, `t_mono_ns`, `run_id`, `save_folder_key`.
6. Held-in improvement on the tune split: LOCO log loss better than the incumbent by ≥ the pre-registered
   `min_effect`; V-1 constraints hold.
7. Feel bounds on replay: switches per campaign ≤ incumbent + 1; no switch inside a commitment window.
Writes `GateReport(stage=1)` and a `stage1_pass|fail` registry row with `gate_version`. The gate code and fixtures are
immutable surfaces (only humans change them, by review).

### 5.3 Stage 2 — "is it meaningful?" (`sae gate stage2 <sha>`, T7 + human)
1. `Preregistration` written and recorded by a **human** (`sae policy prereg`, TTY) before Stage 1 (primary metric,
   `min_effect`, margin, budget, `min_n`); the gate refuses to run without it or if `budget < min_n`.
2. **Detector candidates (v1, `kind = detector_replay`).** Sealed real campaigns once (tripwire, §4.6) — the only
   gating evidence; detection delay and false-lock on any off-style campaign; V-3 still holds; synth seeds reported.
3. **Game-paired candidates (after M4 and V-2/V-4; own proposal, not in 021).** Primary = mean `outcome.reward`
   over 30-year windows from mid-game start saves, paired seeds, `min_n ≥ 30` pairs, lower bound > 0; per-archetype
   5 pp rule reported, not gating (Fable B4). A crash or timeout counts as a **loss** for that arm; two in one batch
   halt Stage 2 (Fable M4). Counter-specificity is a constraint (matched ≥ mismatched + δ), never maximised (M5).
   Sealed start-save bank ≥ 3 × the per-candidate draw; a save retires after 10 uses; new saves come from farm runs.
4. **Human approval.** `sae policy approve <sha>` (interactive TTY, type the first 8 hex) after reading the
   GateReports; then `promote`. A policy meant to ship in the repo additionally needs one maintainer playtest campaign.
   An agent cannot approve: `by` must be `human:*`, the CLI refuses without a TTY. On one machine this is a speed bump,
   not a lock; for policies shipped in the repo the maintainer's review of `src/sae/policies/**` is the real lock.

### 5.4 Registry and rollback (C10 §5)
Append-only `<sae_dir>/policies/registry.jsonl`; current = pure fold requiring
`propose → prereg(human) → stage1_pass → stage2_pass → approve(human) → promote`. Rollback = one `rollback` row naming an
earlier promoted sha (or the built-in); it takes effect at the next daemon start. `sae policy pin builtin` is the
emergency exit. The daemon verifies hash, pin and chain at start; any failure → built-in policy with
`run_started.policy.fallback_reason`. No policy ever changes during a run.

### 5.5 Out of v1
Template θ tuning (I2), counter-table changes, code proposals from an LLM outer loop (X4), randomised counters for
off-policy evaluation, surrogate simulator. Each needs M4 evidence and its own proposal.

## 6. Work units

New team **T9** owns `src/sae/profile/**`, `src/sae/tune/**`, `tests/unit/t9/**` (C8 §7 row to add). The gate
lives under T7's `src/sae/bench/gate/**` (immutable surface). Built-in policy data `src/sae/policies/**` is
maintainer-reviewed (architect owns the packaging entry).

| WU | Owner | Work | Depends on | Milestone | Exit test |
|---|---|---|---|---|---|
| WU-1 | architect | Decide 019 Part A, 020 §1–§2, 021, 022 (023 waits for D1; 019 Part B and 020 §3 deferred); C10 doc + `c10_profile.py`; C8 v1.2, C5/C6 v1.3; schemas; `Sha256Hex`, `RunId`; `profile|tune|policy|gate` subcommands in `src/sae/cli.py` | none | now | `uv run pytest tests/contract` |
| WU-2 | T4 (+ T6 `open_sink` keyword) | Policy resolve/verify at start, before `open_sink`; `LikelihoodModel` and `fixed` tables from the policy; built-in 0.1.0 equals code constants; `run_started.policy`; resume refuses a changed sha | WU-1 | X0 (M2–M3) | existing e2e identical after dropping `run_started.policy`; unit tests for each fallback reason |
| WU-3 | T2 | `save_folder_key` (salt) + `game_version` on `Observation`; `label.json` from `sae synth --record`; recording cap/redaction (022) | WU-1 | X0 | assembler tests; capped recording replays |
| WU-4 | T7 | Fog checks F0–F4 on synth (F1 needs only the existing `parser=` seam and can start before WU-1); CI-4 forbidden list; bus checker for new event | F1/F2/F4: none; F3: WU-5 | now → X0 | F0 fails, F1–F4 pass in CI |
| WU-5 | T9 | `sae profile update|show|delete|close|exclude|label`; index fold (live/release only, split confirmation); strict summaries; R1 alpha + per-fold scores; eligibility flag; drift flag (panel only) | WU-1, WU-3 | X1 (after M3) | synth multi-campaign fixture with `--include-synth`; reused-folder split test; reload test; F3 |
| WU-6 | T9 | `sae tune detector` (§4) | WU-2, WU-5, M2 labelled campaigns | X2 (M2 data) | reproduces V-1 numbers on the same 5 campaigns |
| WU-7 | T7 + T9 | `sae gate stage1|stage2` (T7), `sae policy …` (T9) | WU-2, WU-4, WU-6 | X2 | a dummy candidate passes; a sealed-id candidate and a `fixed`-changing candidate fail; one-step rollback restores the previous sha at next start |
| WU-8 | T8 | Docs (privacy, fairness modes, policy registry and rollback, recording hygiene); M0 checklist A-46 (+ A-47..A-50 if D1); changelog fragment | WU-1 | X0 | `test_checklist_drift.py` green |
| WU-9 | T3 | Proposal 023 snapshot extraction | D1 yes, M0-11 | after M0 | F2 + cost ≤ +10 % |

Order: WU-1 → (WU-2, WU-3, WU-4, WU-8 in parallel) → WU-5 → WU-6 → WU-7. Nothing here needs the game before M2
except the A-46 check at M0-11.

### 6.1 Dependencies on M0/M2
- M0-11: A-46 (folder per campaign); with D1 also A-47..A-50.
- M2: five labelled campaigns (V-1), V-3 on the same data, V-5 on real recordings; a sixth labelled campaign for the
  first sealed slot.
- M4 + V-2 + V-4: anything game-paired (I2 θ tuning).

### 6.2 What must change in the MVP seams
1. `src/sae/core/belief.py`: `update(..., model=None)` defaults to `LikelihoodModel()`; the engine must pass the model
   built from the loaded policy (state or ctx), never the default (T4).
2. Deferred with 019 Part B: `initial_state`/`_reset` start from profile b0 via the existing `run_engine(state=)` seam.
3. `COUNTER_TABLE`, `DEFAULT_EXPANSIONS`, `outcome.WEIGHTS`: read from `policy.fixed` (T4); the constants remain
   only as the source of the built-in file until deleted.
4. `EngineConfig` `rho`, `likelihood_clip`, `belief_threshold`: default `null` = from policy (architect, C8 v1.2).
5. `src/sae/adapters/assembler.py`: two `Observation` fields (T2). Recorder: cap + redaction (T2).
6. `src/sae/daemon/wiring.py`: resolve the policy before `open_sink` and pass `policy=` (T4); `open_sink` keyword (T6).
7. `tests/integration/test_ci4_imports.py` `FORBIDDEN_SAE` and the C6 §4.4 grep (T7).

### 6.3 Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | 5–20 campaigns are too few: the profile never becomes eligible (≥ 8 closed real campaigns is likely 2027 at the maintainer's play rate) | Engine path deferred; panel still useful; measure before adding R2 |
| R2 | One player's fit shipped to others | personal by default; D4 |
| R3 | Holdout erosion by repeated human iteration | one sealed query per candidate, coarse reporting, retirement after 10, query ledger in the registry |
| R4 | Approval forgeable by a local agent | TTY + typed hash is a speed bump; repo review of shipped policies is the lock; stated, not hidden |
| R5 | Folder-name reuse makes a folder permanently `self_contaminated` | conservative by design; `sae profile exclude`; A-46 measures it |
| R6 | Synth-aligned detector stays the default for long | `likelihood_source` on every bus via the policy hash; user guide says so |
| R7 | stellaris-dashboard (pattern source) licence | pattern only, no code copied; licence UNVERIFIED, check before any code reuse |

## 7. Messages to other leads (relay by orchestrator)
- architect: proposals 023 (conditional), 019, 020, 021, 022; new contract C10; C8 §7 rows for T9.
- T2, T3, T4, T6, T7: follow-ups listed at the end of each proposal.
- T8: new UNVERIFIED rows A-46 (now) and A-47..A-50 (only if D1); user-guide sections in WU-8. Labels follow
  `contracts/README.md` §5.

## 8. Maintainer decisions (answered 2026-10-19)

| # | Decision | Recommendation | Maintainer decision |
|---|---|---|---|
| D1 | Allow a `reputation` profile mode (god-view aggregates of your own finished games) and the 023 snapshot? | No for v1. Revisit after the strict profile is eligible and measured | **No for v1.** Proposal 023 stays parked; A-47..A-50 are not added; WU-9 does not start |
| D2 | Re-submit 019 Part B / 020 §3 (priors into the engine) once a profile is eligible | Yes, then default `strict` with a visible notice; until then the profile is a panel only | **Yes, once eligible**, as recommended |
| D3 | Label a sixth campaign so the first sealed slot exists | Yes, at M2 | **Yes, 6 labelled campaigns at M2** (5 for leave-one-out, 1 sealed) |
| D4 | May a policy fitted on your campaigns ship to other players? Signing? | Personal only until data from more players exists; channels and signing then get their own proposal | **Personal only**, as recommended |
| D5 | Run `sae profile update` manually or from a launcher before `sae daemon` | Manual in v1 | **From a launcher** (differs from the recommendation). Add a launcher work unit: it runs `sae profile update`, then starts `sae daemon`; it must never launch Stellaris or touch its install or mod folder, and must start the daemon even when the profile update fails, with a visible notice. Owner and contract entry (C8 CLI) to be assigned in WU-1 |
| D6 | Primary objective for any later game-paired Stage 2 (beat the player vs keep a challenge band) | Decide before M4 | **Decide before M4**, as recommended |

## 9. Review log

Round 1 — sae-adversary (Fable), plan review, 2026-10-19. Verdict: ACCEPT WITH CHANGES (4 blockers, 5 major,
8 minor). It confirmed the earlier proposal-review findings B1, B2, B4, M1–M6, M8, m2 resolved; B3 half-resolved;
M7 partly (see #10).

| # | Finding | Resolution |
|---|---|---|
| 1 BLOCKER | Campaign split by start date is dead: every game starts 2200.01.01; synth seed 0 merges all synth runs | 020: split candidate when a run starts > 5 years before the campaign's `last_date`, user confirms; `CampaignId = sha256(folder_key|first run_id)`; 019: synth folder key from `run_id` |
| 2 BLOCKER | Index ingests synth runs into the profile | 020: only `adapter_mode ∈ {live, release}`; `--include-synth` for tests |
| 3 BLOCKER | "e2e unchanged bit-for-bit" impossible (`run_started.policy`, `priors_loaded` shift `seq`) | `priors_loaded` deferred (emitted only when profile mode ≠ off when it returns); WU-2 exit = identical after dropping `run_started.policy` |
| 4 BLOCKER | Stage 1 runs synth-tuned CI-1 against a `fitted_real` candidate | §5.2 step 3: full suite on built-in policy; `gate_replay` subset with the candidate |
| 5 MAJOR | `sae tune` cost under-stated ~100× | §2.4: tune split only, precomputed features, `_raw_lls` direct, ≤ 30 min target |
| 6 MAJOR | `prereg` writable by the gate | 021 + §5.3: `prereg` human-only, TTY |
| 7 MAJOR | `open_sink` seam missing for `run_started.policy` | 019: C8 §5.1 `open_sink(..., policy=)`, owner T6; WU-2 names T6 |
| 8 MAJOR | Fresh synth seeds in Stage 2 are circular evidence | §4.5, §5.3: synth is a reported diagnostic; gating = sealed real campaigns |
| 9 MAJOR | Determinism hash includes salted `save_folder_key` | §3 F1, §5.2 step 5 drop it |
| 10–17 MINOR | `RunId` alias missing; `n_campaigns` mismatch; `TemplateSpec` fields; redundant `sae.bench.gate`; salt race; resume vs new priors; F0 mutant unspecified; `min_effect` unused, `source` for pin builtin; CLI in architect's `cli.py` | `RunId` added to 020 + WU-1; `n_campaigns` now only in deferred 020 §3 (ge=8); `TemplateSpec` = `Template(econ, posture, reason_key)` checked against `src/sae/core/expansion.py:19`; CI-4 line fixed; salt `O_EXCL`; resume rule in 019 Part B; F0 = assembler mutant; `min_effect` used in Stage 1 step 6, pin mapping in 021; CLI in WU-1. M7: `player_key` stays a plain config string, accepted and stated in 020 |
| Cut | Priors→engine path; `history.jsonl`; drift auto-action; `reopened`/`export`; channels; `repo_tree_sha256`; `paired_game` prereg | All cut or deferred (019 Part B, 020 §3); `excluded` kept (needed for mod-list changes); drift kept as a panel flag only |

Not adopted: none. No second round run (nested mode, one child at a time; the fixes are local text changes with no
new design). If the orchestrator wants a confirmation pass, the scope is proposals 019–021 and plan §4–§6.
