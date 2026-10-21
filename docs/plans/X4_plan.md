# X4 plan — WU-6 `sae tune detector`, WU-7 `sae gate` + `sae policy` (game-free, synth-proven)

Owner: T9 lead (coordinating T7). Date: 2026-10-20. Branch: `feat/x4-tune-gate-scaffold`. Status: PLAN.
Source: `docs/expansion/player_profile_and_rsi_plan.md` §4–§6 (the "RSI plan"), C10 v1.1, C8 v1.5.
Settled and binding: D1 no reputation mode; D2 no priors into the engine until a profile is eligible; D3 6
labelled campaigns at M2 (5 LOO + 1 sealed); D4 policies personal only (no sharing, no signing); D6 Stage 2
objective undecided; CUSUM is a diagnostic only (never a gate check); a policy never changes during a run.
Deferred-game rule (2026-10-20): everything needing real saves/logs/labelled campaigns is built now, skipped
without inputs, and documented in `docs/handoff/deferred/X4.md`.

## 1. Design

### 1.1 Flow
```
sae profile label (X1)  ->  sae tune detector  ->  policies/<sha>.json + propose(tuner)
sae policy prereg <sha> (human, TTY)  ->  sae gate stage1 <sha>  ->  stage1_pass|fail + GateReport
sae gate stage2 <sha> --objective detector_replay  ->  stage2_pass|fail (sealed tripwire, 1 query)
sae policy approve <sha>  ->  sae policy promote <sha>  ->  daemon picks it at next start (WU-2, unchanged)
sae policy rollback [<sha>] | pin builtin  ->  previous sha at next start
```
No new process, nothing resident, nothing imported by the daemon (`sae.tune`, `sae.bench`, `sae.profile` are in
CI-4 `FORBIDDEN_SAE` already). The daemon's resolver `src/sae/daemon/policy_resolve.py` is reused unchanged.

### 1.2 Shared pieces (batch 1)
| Piece | Path (owner) | Why |
|---|---|---|
| Labelled-campaign loader + sealed-query ledger | `src/sae/profile/ledger.py` (T9) | one loader for tune and gate; ledger counts sealed queries per proposal 029 §3 and feeds `campaign_state(events, sealed_queries)` |
| Observation order with reload drop | `src/sae/profile/summary.py` gains public `kept_observations()` (alias of `_kept_observations`, returns `list[_Kept]`) | reuse, no copy |
| Detector replay + scoring (the judge) | `src/sae/bench/gate/metrics.py` (T7, immutable surface) | the tuner optimises the **gate's** metric; the tuned party never owns the judge |
| Synth labelled multi-campaign fixture | `tests/unit/t9/_synth_campaigns.py` (T9) | 6 labelled campaigns (+1 unlabelled), live- and synth-mode variants; used by t9 and t7 tests |
| `game` marker | `tests/unit/t9/conftest.py`, `tests/unit/t7/conftest.py` (local minimum; X3 owns the global one) | deferred tests |

Frozen B↔C seam (review blocker 3): `ledger.labelled_campaigns(sae_dir, player_key, runs_dir, split, include_synth)
-> list[LabelledCampaign]`, `LabelledCampaign` a frozen dataclass `(state: CampaignState, label: CampaignLabel,
observations: list[Observation])` with `observations = [k.obs for k in summary.kept_observations(state, runs_dir)]`
(plain `sae.contracts` `Observation`, never `_Kept`). `metrics.detector_trace`/`strategy_trace` take
`Sequence[Observation]`, i.e. exactly `LabelledCampaign.observations`. `metrics.MonthPosterior` = frozen dataclass
`(mi: int, posterior: Mapping[Archetype, float])`.

`metrics.py` API (pure, stdlib only):
- `detector_trace(observations, policy: PolicyVersion) -> list[MonthPosterior]`: feeds `Observation`s through
  `sae.core.features.extract` and `sae.core.belief.update` with `from_doc(policy)` tables and the policy's ρ/clip.
- `strategy_trace(observations, policy) -> list[(mi, Strategy)]`: runs `sae.core.engine.on_observation` (pure) to
  get real strategy selections with commitment windows (feel bounds).
- `fold_score(trace, label, burn_in_months=12) -> FoldScore(log_loss, ece, counter_ok, n_months)`; ECE with 10
  equal-width bins on the label-archetype probability; `counter_ok` = C3 §6 counter of the modal post-burn-in
  argmax equals the counter of the label's dominant archetype (V-1 wording).
- `round2(x)` (2 significant digits, sealed reporting).
- `fast_lls(feature_vectors, LikelihoodModel)`: precomputed `_raw_lls` path for the tuner grid (RSI plan §2.4).

### 1.3 `sae tune detector` (T9, `src/sae/tune/`)
1. Load index; `campaign_state(events, ledger.sealed_query_counts(...))`; keep `status == "closed"`, labelled,
   `split == "tune"`, not excluded/deleted, `adapter_mode in {live, release}` (synth only with `--include-synth`).
   Need ≥ 5; fewer → exit 2 "need 5 labelled tune campaigns, have N". Sealed campaigns are never opened.
2. Precompute FeatureVectors once per campaign (metrics helpers).
3. Shrinkage fit toward the parent (current registry policy, else built-in): Normal–Inverse-Gamma for `border`,
   `expansion` (κ pseudo-months), Dirichlet κ·p₀ for `fleet`, `tech`, Beta for `war_now`; months labelled by the
   label segment active at that date. Grid κ ∈ {1,3,10,30,100} × ρ ∈ {0.01,0.02,0.04,0.08} × clip ∈ {2,3,4} ×
   threshold ∈ {0.40,0.45,0.55} = 180 points; LOCO over the 5 tune campaigns; pick min mean log loss subject to
   ECE ≤ 0.10 on each fold and counter_ok on ≥ 4 of 5; ties broken by grid order (deterministic). No feasible
   point → exit 1, no file written, per-fold table printed.
4. Final fit on all tune campaigns at the chosen point; `PolicyVersion(parent_sha256=parent, fixed=parent.fixed,
   tuning=TuningManifest(method="loco_shrinkage", likelihood_source="fitted_real" (synth runs: "synth_aligned"),
   campaign_ids=..., kappa=..., tuner_version="0.1.0"))`; `notes` carries per-fold numbers, V-3 diagnostics
   (switch count from `strategy_selected`, reward spread from `outcome`) and "CUSUM: diagnostic only".
5. Write `policies/<sha>.json` atomically (tmp + `os.replace`, canonical JSON so name == hash), append one
   `propose` row `by: tuner`. Print sha, per-fold table, V-3 verdict. Refuse while the game is detected unless
   `--force`; idle priority (reuse `sae.profile.cli` helpers). Grid injectable for tests (`grid=` parameter).
6. Determinism: same inputs → same sha (no wall-clock in the policy: `created_at` = `at` of the last folded index
   row, as C10 §2 rule 2 does for profiles).

### 1.4 `sae gate stage1|stage2` (T7, `src/sae/bench/gate/`, immutable surface, `GATE_VERSION = "0.1.0"`)
Stage 1 checks (each a `GateCheck`; any fail → `stage1_fail`):
1. `schema` (model valid, file name == `policy_sha256`), `parent` (exists), `mutable_surface` (diff vs parent
   only under `detector`/`tuning`/`version`/`created_at`/`notes`/`parent_sha256`; `fixed` identical), `pin`
   (matches recorded campaigns' game versions).
2. `sealed_split` (`tuning.campaign_ids ∩ sealed = ∅`, using the ledger-aware fold), `seed_range` (all
   `synth_seeds < 1_000_000`), `sealed_budget` (sha256 of the canonical `detector` block of any sha that already
   has a `stage2_*` row equals the candidate's → fail; stops re-proposing the same detector to buy sealed queries).
3. `prereg` (a `prereg` row exists for the sha and the evidence parses) — chain order is propose → prereg → stage1.
4. `gate_replay`: every tune campaign replayed through `detector_trace` + `strategy_trace` twice with the
   candidate; no exception, identical outputs (determinism).
5. `heldin_log_loss_delta` (review blocker 6): candidate minus incumbent mean log loss over the tune campaigns
   ≤ −`min_effect`. This is **held-in** (the candidate was fit on these campaigns, so it is biased toward the
   candidate); `detail` says "held-in, biased toward the candidate; LOCO per fold is in the tuner notes; the
   unbiased check is Stage 2 sealed". V-1 constraints (ECE ≤ 0.10 per campaign, counter_ok ≥ 4/5) also reported.
   True LOCO recomputation in the gate is backlog (needs the fitter inside the judge).
6. `feel_switches` (≤ incumbent + 1 per campaign) and `feel_commitment` (no switch inside a commitment window).
7. `repo_suite` only with `--repo <checkout>`: runs `uv run pytest -x -q`, `uv run mypy --strict`,
   `uv run ruff check` there with the built-in policy (subprocess, exit codes recorded; pinning the candidate into
the e2e fog tests is backlog, they read no policy knob today);
   without `--repo` the check is absent and the report `detail` of `gate_replay` says "repo suite not run".
Writes `evidence/<sha256 of bytes>.json` (GateReport) and a `stage1_*` row `by: gate`.

Stage 2 (`--objective` required, no default):
- `detector_replay`: requires stage1_pass and prereg; refuses if any `stage2_*` row already exists for the sha
  (one sealed query per candidate). Reads sealed campaigns only (`split == "sealed"`); 0 sealed → exit 2 "no
  sealed campaign (D3: label a 6th)". Checks `sealed_noninferior:<cid>` per campaign (candidate log loss ≤
  incumbent + margin; `value = round2(delta)`), `detection_delay`, `false_lock` and `v3_signal` (reported),
  each report carries detail "tripwire, not a significance test (n_sealed = k)". Pass rule (review blocker 2, C10
  v1.2): stage 2 passes iff every `sealed_noninferior:<cid>` check passes; `detection_delay`, `false_lock`,
  `v3_signal` are informational: always `passed: true`, numbers in `value`, meaning in `detail` (so
  `GateReport.passed == all(checks.passed)` holds). Real data → registry row +
  evidence. `--include-synth` → report printed and evidence written, **no** registry row (no ledger burn).
- `beat_player` / `challenge_band`: writes a draft protocol JSON to `--out` (objective, paired seeds, `min_n ≥ 30`,
  30-year windows, crash = loss, halt after 2 crashes, sealed start-save bank ≥ 3× draw, retire after 10 uses),
  no registry row, exit **4** "no result: game-paired Stage 2 needs D6 and real paired runs (M4)" (3 stays "invalid
  config", C8 §5; review blocker 1).

### 1.5 `sae policy` (T9, `src/sae/profile/policy_cli.py`)
`propose <file>` (human), `prereg <sha> --metric --min-effect --margin --budget --min-n` (writes Preregistration
evidence then row), `approve`, `reject`, `promote <sha>` (pin vs campaigns), `rollback [<sha>]` (one step default),
`pin builtin` (rollback row to built-in sha + hint), `show` (current, approved, chain per sha, sealed-query ledger).
Every human verb: `append_registry_row` (TTY guard, already landed) + `input()` "type the first 8 hex". Evidence
files: canonical JSON, name = sha256 of bytes, atomic write.

## 2. Owned paths
| Team | Paths touched in X4 |
|---|---|
| architect | `contracts/C8_config_cli.md`, `contracts/C10_profile_policy.md`, `contracts/CHANGES.md`, `contracts/proposals/029-*.md` (Decision), `src/sae/cli.py`, `tests/test_cli.py` |
| T9 | `src/sae/tune/**`, `src/sae/profile/ledger.py`, `src/sae/profile/policy_cli.py`, `src/sae/profile/summary.py` (public alias only), `tests/unit/t9/**` |
| T7 | `src/sae/bench/gate/**`, `tests/unit/t7/**` (new files + conftest) |
| T8 (docs order) | `docs/user_guide.md`, `docs/handoff/deferred/X4.md`, `docs/components/T9.md`, `docs/status/T9.md`, `changelog.d/X4.md` |
Not touched: `.github/workflows/**`, `tools/m0/**`, `src/sae/m0/**`, `src/sae/core/**`, `src/sae/daemon/**`.

## 3. Work orders
| Id | Batch | Agent | Files | Depends |
|---|---|---|---|---|
| X4-A | 1 | sae-architect | C8, C10, CHANGES, 029 Decision, `src/sae/cli.py`, `tests/test_cli.py` | — |
| X4-B | 1 | sae-implementer | `src/sae/bench/gate/__init__.py`, `metrics.py`, `tests/unit/t7/conftest.py`, `tests/unit/t7/test_gate_metrics.py` | — |
| X4-C | 1 | sae-implementer | `src/sae/profile/ledger.py`, `policy_cli.py`, `summary.py`, `tests/unit/t9/conftest.py`, `_synth_campaigns.py`, `test_ledger.py`, `test_policy_cli.py`, `test_policy_rollback_e2e.py` | — |
| X4-D | 2 | sae-implementer | `src/sae/tune/**`, `tests/unit/t9/test_tune_*.py`, `tests/unit/t9/test_tune_game.py` | B, C |
| X4-E | 2 | sae-implementer | `src/sae/bench/gate/stage1.py`, `stage2.py`, `cli.py`, `tests/unit/t7/test_gate_stage*.py`, `test_gate_cli.py`, `test_gate_game.py` | A, B, C |
| X4-F | 3 | sae-implementer | docs paths above | A–E |

## 4. Test list (game-free; all must pass in the Gates phase)
| Test | Proves |
|---|---|
| `tests/unit/t7/test_gate_metrics.py` | trace determinism; log loss/ECE/counter on hand-made traces; `round2`; builtin vs fast path equal posteriors |
| `tests/unit/t9/test_ledger.py` | loader returns only tune/sealed as asked; synth excluded without flag; ledger counts `sealed_noninferior:<cid>`; unreadable evidence counts against all sealed |
| `tests/unit/t9/test_policy_cli.py` | each verb; non-TTY refusal; wrong 8-hex refusal; prereg evidence hash; promote pin refusal; `show` output |
| `tests/unit/t9/test_policy_rollback_e2e.py` | **WU-7 exit:** promote A, promote B, `rollback` → `sae.daemon.policy_resolve.resolve_policy(cfg).ref.sha256 == A` (the exact call `wiring.py:89` makes for `run_started.policy`; no parser, no daemon loop); `pin builtin` → built-in sha. Live `sae play` check = X4-G5 |
| `tests/unit/t9/test_tune_fit.py`, `test_tune_cli.py` | shrinkage maths; grid determinism; sealed campaign never opened (spy); < 5 campaigns refused; synth fixture with a shifted "player" improves LOCO log loss vs built-in; same sha twice; game-detected refusal; `propose` row by `tuner` |
| `tests/unit/t7/test_gate_stage1.py` | **WU-7 exit:** dummy candidate (tuner output on synth fixture; or builtin with ρ 0.021 and prereg `min_effect = -1.0`) passes; sealed-id candidate fails `sealed_split`; `fixed`-changing candidate fails `mutable_surface`; missing prereg fails; seed ≥ 1e6 fails |
| `tests/unit/t7/test_gate_stage2.py`, `test_gate_cli.py` | `--objective` missing → usage error 2; `beat_player`/`challenge_band` → exit 4, protocol file, no registry row; detector_replay on live-mode fixture writes `stage2_*` + ledger +1; second query refused; synth run writes no row; 0 sealed → exit 2 |
| `tests/test_cli.py` (architect) | `tune`, `policy`, `gate` rows dispatch |
Commands: `uv run pytest -x -q tests/unit/t9 tests/unit/t7 tests/test_cli.py`; Gates phase only:
`uv run pytest -x -q`, `uv run mypy --strict`, `uv run ruff check`,
`uv run pytest --cov=sae.tune --cov=sae.bench.gate --cov=sae.profile --cov-report=term-missing tests/unit/t9 tests/unit/t7` (≥ 90 % each).

## 5. Deferred list (owner of `docs/handoff/deferred/X4.md`: X4-F only)
| Id | What it proves | Command (PowerShell) | Inputs | Pass rule | Est. |
|---|---|---|---|---|---|
| X4-G1 | V-1 numbers: real LOCO tuning on the 5 tune campaigns | `uv run sae tune detector`; `uv run pytest -m game tests/unit/t9/test_tune_game.py -q` | `SAE_GAME_SAE_DIR` (copy of the real `<sae_dir>` with 6 labelled closed campaigns), `SAE_GAME_PLAYER_KEY` | ECE ≤ 0.10 every fold; counter correct ≥ 4/5; a candidate sha printed | 30–45 min |
| X4-G2 | V-3 signal on the same data | same run; read the V-3 block | same | > 1 switch where the label changes style; rewards not all within ±0.02 | 5 min |
| X4-G3 | sealed-slot query end to end | `sae policy prereg <sha>`; `sae gate stage1 <sha>`; `sae gate stage2 <sha> --objective detector_replay`; `pytest -m game tests/unit/t7/test_gate_game.py` | same (+ TTY) | one `stage2_*` row; ledger shows 1 query on the 6th campaign; values at 2 significant digits | 15 min |
| X4-G4 | game-paired Stage 2 (BLOCKED until D6 + M4) | `sae gate stage2 <sha> --objective <D6 choice> --out protocol.json` | D6 decision, M4 farm | today: exit 4 + protocol file; after M4: own proposal, n ≥ 30 pairs, LCB > 0 | 2 min today |
| X4-G5 | A/B + rollback on the game PC | `sae policy approve/promote <sha>`; `sae play`; check `run_started.policy`; `sae policy rollback`; `sae play` again | game PC, promoted candidate | sha == candidate, then == previous after rollback; no policy change mid-run | 20 min + play |
Every game test copies `SAE_GAME_SAE_DIR` to a temp dir first (append-only files must not be touched in place) and
skips with "set SAE_GAME_SAE_DIR (X4-G1)" when unset.

**Prerequisite X4-G0 (review blocker 5).** X4-G1–G3 need 6 *closed, labelled, live* campaigns (~10 game years
each, 5 tune + 1 sealed) in `<sae_dir>`. Tomorrow's game PC will almost certainly not have them; a short session
gives autosaves, not campaigns. The handoff must list, per campaign: `sae profile update` (fold runs), `sae
profile close <cid>`, `sae profile label <cid> ...` (segments), with time (play ≈ hours per campaign; profile
steps ≈ 5 min each), and state **"V-1 NOT VERIFIED until 6 labelled campaigns exist"**. Fallback on the day:
`uv run sae tune detector --include-synth` and `uv run sae gate stage2 <sha> --objective detector_replay
--include-synth` = diagnostic only, no registry row, never quoted as verification. No status, component or
changelog text may word a synth result as "verified" or "V-1 met".

## 6. Risks
| # | Risk | Mitigation |
|---|---|---|
| R1 | Tuner owns its own judge (reward hacking by construction) | scoring lives in the T7 gate (`metrics.py`); tuner imports it |
| R2 | Stage 1 without `--repo` skips the repo suite and candidate-pinned fog tests | stated in the report detail; F4 and CI-4 are policy-independent; candidates differ only in `detector` numbers (structural `mutable_surface` check) |
| R3 | Synth fixture "passes" trivially (synth-aligned tables) | fixture shifts the player's feature means away from the built-in tables so tuning must move them; synth results labelled diagnostic, never gating |
| R4 | Sealed-query ledger burn from test runs | synth Stage 2 writes no registry row; one query per candidate enforced |
| R5 | Tuner runtime on real data (180 × 5) | precomputed features + `fast_lls`; ≤ 30 min target measured in X4-G1 |
| R6 | Human-row trust is a speed bump (C10 §5) | unchanged, stated in the user guide |
| R7 | Three waves share 6 cores | targeted tests only until the Gates phase |

## 7. UNVERIFIED (to T8 for the M0 checklist)
No new game assumption. Reused: A-46 (one folder per campaign), game-version string format used by `pin_matches`
(`v4.4.*` vs the `game_version` the parser reports) — both UNVERIFIED until M0/M2.

## 8. Review log
Adversary (Fable) plan review: APPROVE_WITH_CHANGES, `docs/status/X4_plan_review.md`; 6 blockers closed in this
revision (see "Plan review response" there).
