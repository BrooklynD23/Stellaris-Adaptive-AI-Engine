# T9 plan — Player profile and tuning

Owner: T9 lead. Owned paths: `src/sae/profile/**`, `src/sae/tune/**`, `tests/unit/t9/**`.
Inputs: `contracts/C10_profile_policy.md` (v1), `contracts/C8_config_cli.md` §5, `docs/expansion/player_profile_and_rsi_plan.md`
§3, §6, `docs/status/ARCHITECTURE_alignment_review_2.md` "Review 2 follow-up" items 3 and 5.
Settled (not reopened): D1 no reputation mode; D2 profile is a read-only panel until eligible (no `priors.json`);
D4 personal policies only; D5 launcher; no LLM in runtime; three processes; daemon reads no profile file.

## X1 (game-free, synth data)

### 1. Scope

| # | Item | Owner | In X1 |
|---|---|---|---|
| 1 | WU-5 `sae profile update\|show\|delete\|close\|exclude\|label`; index fold; strict summaries; R1 alpha; folds; eligible; drift | T9 | yes |
| 2 | D5 `sae play` (`sae.profile.launcher`) | T9 | yes |
| 3 | F3 passes; F1 PLYR leg non-vacuous (V-5, review-2 item 3) | T7 | yes |
| 4 | WU-7 slice: `c10_profile.append_registry_row` + `isatty()` refusal + test (review-2 item 5) | architect (contract code) | yes |
| 5 | Docs, changelog fragment + merged CHANGELOG, status rows | T8 (+ architect for TEAM_PLAN) | yes |
| — | `sae tune`, `sae policy …` CLI, `sae gate stage1\|stage2`, `priors.json` | — | **no** (M2 data / D2) |

Item 4 note: C10 §5 puts the helper in `src/sae/contracts/c10_profile.py` (architect-owned), so the architect writes it.
The `sae policy` CLI that will call it with `human:` rows stays WU-7.

### 2. Profile store (all under `<sae_dir>` = `c8_config.sae_dir(cfg)`)

```text
<sae_dir>/campaigns.jsonl                    CampaignEvent rows (append-only, seq gapless)
<sae_dir>/profile/<player_key>/labels/<id>.json      CampaignLabel
<sae_dir>/profile/<player_key>/summaries/<id>.json   CampaignSummary (rebuilt every update)
<sae_dir>/profile/<player_key>/profile.json          ProfileDoc
```
Runs are read from `cfg.paths.runs_dir` if set, else `<sae_dir>/runs`. Only `runs/*/bus.jsonl` is opened, through
`sae.bus.reader.iter_events(path)` (god=False). No profile code names `bus.god.jsonl` (F3/F4).

Modules (`src/sae/profile/`):

| File | Content |
|---|---|
| `__init__.py` | docstring only |
| `store.py` | paths, `read_index()` (skip-and-count invalid, detect seq gap), `append_events()` (gap → `IndexCorrupt`; `campaigns.jsonl.lock` via `O_CREAT|O_EXCL` → `IndexLocked`, exit 1; non-empty file without final LF → one LF first, 025 §3.9), `write_json_atomic()`, `canonical_bytes()` |
| `index.py` | `CampaignState`, `campaign_state(events, sealed_queries={})` fold, `scan_runs(runs_dir, include_synth)` → segments, `plan_update(state, segments, ask)` → new rows (run lookup 025 §3.2 a/b/c) |
| `summary.py` | `build_summary(campaign, runs_dir) -> CampaignSummary` (strict, reload rule, features) |
| `model.py` | HYP constants, `build_profile(summaries, player_key, built_at) -> ProfileDoc` (pooling, alpha, folds, drift) |
| `cli.py` | `main(command, argv)`; subcommands; config; game guard; idle priority |
| `launcher.py` | `main(command, argv)` for `sae play` |

Fold rules: C10 §1 1–6 plus proposal 025 §3 1–10 (segments, idempotence + run lookup, split prompt, deleted terminal,
order + fold-relative decay age, index lock, no label overwrite).
Split by label order: #1–#5 `tune`, #6, #10, #14 … `sealed`, else `tune`; sealed → tune with ≥ 2 newer sealed and
≥ 10 sealed queries (0 in X1).

Summary (C10 §2): observations of the campaign's segments ordered by run `run_started.t_wall`, then seq; a date
regression drops kept observations dated after the new date (and their `belief_update`s). Per observation:
`sae.core.features.extract()` with one `FeatureMemory` per campaign gives `border_fort_ratio`, `fleet_power_cat`,
`tech_cat`, `at_war` (what the engine saw); `economy_cat` = lower median and `systems_owned` = max over
`in_contact` views, computed locally. One sample per game month (last wins).

Profile maths (stdlib only): designer hyperpriors HYP = the C10 §2 example values; within-player sd σ = `hyper_sd`;
weights `0.5 ** (age / 5)` (age 0 = newest); posterior precision `1/hyper_sd² + n_eff/σ²`; `player_sd` = predictive
sd `sqrt(1/precision + σ²)`; `archetype_alpha[a] = 1 + Σ w·final_belief[a]`; folds k = 1..n−1 (fit 1..k, score k+1,
summed Normal log-density delta vs `N(hyper_mu, sqrt(hyper_sd² + σ²))`); `drift_outside` = features of the newest
campaign outside the 90 % predictive interval of the fit on the others; eligible / drift flags as the model enforces.

### 3. CLI surface (`sae profile`, C8 common flags `--config --preset --set --runs-dir`)

| Command | Behaviour | Exit |
|---|---|---|
| `update [--include-synth] [--force]` | mode off → 2; game running (T5 `make_sensors().sample().game_running`) → 1 unless `--force`; idle priority (psutil); index → summaries of closed, non-excluded, non-deleted campaigns → `profile.json` | 0 / 1 / 2 / 3 |
| `show [--json]` | panel: campaigns (id, status, split, dates), eligible, drift, per-feature μ ± sd, alpha, salt path | 0 |
| `close <id>` / `exclude <id> [--reason]` | append row; unknown or deleted id → 2; `exclude` also removes `summaries/<id>.json` and `profile.json` (025 §3.7) | 0 / 1 / 2 |
| `delete [--campaign <id>] [--yes]` | needs `--yes` or typed `DELETE` on a TTY; all: rmtree `profile/<key>/` + `deleted` rows; one: row + its label/summary + `profile.json` (025 §3.7) | 0 / 1 / 2 |
| `label <id>\|--recording <dir> --segment DATE=ARCH … [--note] [--not-blind]` | write `CampaignLabel` (canonical bytes); indexed id → `labelled` row with sha256 of file bytes; target file exists → 2 (025 §3.10) | 0 / 1 / 2 |

`sae play`: daemon flags (`--config --preset --set --runs-dir --adapter --resume`). If resolved `profile.mode != "off"`:
`subprocess.run([sys.executable, "-m", "sae", "profile", "update", <common flags>], timeout=600)`; any non-zero,
exception or timeout prints `sae play: profile update failed (...); starting the daemon anyway` to stderr. Then
returns `sae.daemon.cli.main("daemon", <all flags>)`. Subprocess (not in-process) so idle priority never sticks to the
daemon. Never launches Stellaris, never touches its install or mod folder.

### 4. Tests (acceptance per plan §6 WU-5)

| Test | Where | Proves |
|---|---|---|
| synth multi-campaign fixture with `--include-synth` | `tests/unit/t9/test_cli.py` (hand-built buses) + `tests/e2e/test_fog_f3.py` (real `sae synth`) | 3 campaigns indexed, closed, profiled |
| reused-folder split test | `tests/unit/t9/test_index.py` | new / reload / skip / no-TTY pending; run lookup a/b/c |
| index durability | `tests/unit/t9/test_index.py` | lock present → `IndexLocked` / exit 1; partial last line → next append gapless |
| privacy takes effect | `tests/unit/t9/test_cli.py` | `profile.json` + summary absent after `exclude` / `delete --campaign`, even with mode off |
| reload test | `tests/unit/t9/test_summary.py` | abandoned timeline dropped |
| F3 | `tests/e2e/test_fog_f3.py` (T7) | ≥ 1 non-empty god bus first; random and deleted god buses → `profile.json` byte-identical; legal `systems_owned` change on an `in_contact` view → differs |
| idempotent update, gap refusal, synth ignored without flag | `tests/unit/t9/test_index.py` | fold rules |
| maths | `tests/unit/t9/test_model.py` | alpha, shrinkage, folds, eligible, drift |
| launcher | `tests/unit/t9/test_launcher.py` | update failure still starts daemon |
| registry writer refusal | `tests/contract/test_c10_registry_writer.py` (architect) | `human:` row refused without TTY, no lock left |
| F1 PLYR leg | `tests/e2e/test_fog.py` (T7) | PLYR before saves reaches god bus; decisions unchanged |

Gates: `uv run pytest -x -q`, `uv run mypy --strict src/sae`, `uv run ruff check`; `sae.profile` coverage ≥ 90 %.

### 5. Work orders

| Id | Batch | Agent | Files |
|---|---|---|---|
| X1-A | 1 | sae-architect | proposal 025, `contracts/C8_config_cli.md`, `contracts/C10_profile_policy.md`, `contracts/CHANGES.md`, `src/sae/cli.py`, `src/sae/contracts/c10_profile.py`, `tests/contract/test_c10_registry_writer.py`, `tests/test_cli.py` |
| X1-B | 1 | sae-implementer (T9) | `src/sae/profile/**`, `tests/unit/t9/**` |
| X1-C | 1 | sae-implementer (T7) | `tests/e2e/_fog.py`, `tests/e2e/test_fog.py` |
| X1-D | 2 | sae-implementer (T7) | `tests/e2e/test_fog_f3.py`, `tests/integration/test_fog_f4.py` |
| X1-E | 2 | sae-implementer (T8) | `docs/user_guide.md`, `README.md`, `docs/BUILD_REPORT.md`, `changelog.d/T9.md`, `changelog.d/T7.md`, `CHANGELOG.md` |
| X1-F | 2 | sae-architect | `docs/TEAM_PLAN.md` |

X1-B builds against proposal 025 §3 as written; if the architect changes a clarification, the T9 lead relays it.

### 6. Risks

| # | Risk | Mitigation |
|---|---|---|
| 1 | Unit fixtures diverge from real buses | F3 runs on real `sae synth` buses; fixtures built through the C5 pydantic models |
| 2 | `built_at` as index time surprises readers | proposal 025 §3.6; `show` prints it as "index as of" |
| 3 | Game-process detection is T5's UNVERIFIED process-name list | `--force`; already on T5's M0 list, no new assumption |
| 4 | Tests writing to the real `<sae_dir>` | every test sets `paths.sae_dir` to tmp; launcher tests mock subprocess and daemon |
| 5 | F1 offset rewrite breaks replay | assert 0 `log_parse_error` and decision stream length ≥ 12 |
| 6 | Concurrent or killed `update` corrupts the index | writer lock + LF repair (025 §3.9); recovery documented |
| 7 | Fixture without god data makes fog tests vacuous | `make_run` always writes ≥ 1 `observation_god`; F3 asserts it before randomising |

UNVERIFIED (to T8 M0 list): none new. A-46 (folder per campaign) already listed; game detection is T5's.

### 7. Review gate
Fable (`sae-adversary`) reviews this plan before batch 1 and the wave after batch 2 (ON TARGET / AT RISK / OFF
TARGET on perf/memory, telemetry, adaptive engine, RSI readiness). Plan review done 2026-10-20: ACCEPT WITH CHANGES
(`docs/status/X1_plan_review.md`); all 5 blocking items closed in this plan, proposal 025 §3 and the work orders
(see that file, "Plan review response").
