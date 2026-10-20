# C10 — Player profile and policy (campaign index, labels, profile, PolicyVersion, registry, gate evidence)

| | |
|---|---|
| Version | 1.1 (`v = 1` on JSONL rows, `schema_version = 1` on JSON documents) |
| Status | FROZEN v1.1 (2026-10-20; v1.1 proposal 025 §2–§3: `append_registry_row` landed, §1.7–§1.15 and §2 rules 1–3 index/profile clarifications, no schema change; v1 2026-10-19: proposals 020 §1–§2 and 021, ACCEPTED-WITH-CHANGES; 020 §3 `EnginePriors` and 019 Part B `priors_loaded` DEFERRED, maintainer D2) |
| Owner | architect (contract); T9 implements the profile side (`src/sae/profile/**`, `src/sae/tune/**`), T7 the gate (`src/sae/bench/gate/**`), T4 the daemon's policy loader |
| Producers | `sae profile …` (T9: §1, §2); `sae tune` (T9: proposed policies, `propose` rows); `sae gate stage1\|stage2` (T7: `stage*` rows, GateReports); `sae policy …` (T9, human-only rows, Preregistrations) |
| Consumers | T4 daemon (§4, §5 read-only at start); T9; T7 (gate, privacy and fog tests); T6 (console views); T8 (docs) |
| Code | `src/sae/contracts/c10_profile.py` — `CampaignEvent`, `CampaignLabel`, `CampaignSummary`, `ProfileDoc`, `PolicyVersion`, `RegistryRow`, `fold_registry`, `append_registry_row`, `policy_sha256`, `pin_matches`, `Preregistration`, `GateReport` |

Settled decisions hold: no LLM in the runtime loop, zero GPU, three processes, **runtime self-modification OFF**.
The daemon loads one hashed policy read-only at start and never changes it during a run; only validated,
versioned, human-approved policies are promoted. Policies are **personal** (maintainer D4): they live under the
player's `<sae_dir>` and are never uploaded or shipped to other players (repo-shipped policies under
`src/sae/policies/**` need maintainer review and a separate decision).

## 0. Files

All C10 files live under `<sae_dir>` (C8 `paths.sae_dir`, resolved only by `c8_config.sae_dir()`), never under
`user_dir`, never uploaded.

```text
<sae_dir>/
  salt                                  32 random bytes; HMAC key for folder keys (C6 §2). Created by the adapter (T2)
  runs/                                 C8 §6 (the default runs_dir; unchanged)
  campaigns.jsonl                       §1 CampaignEvent (append-only; `sae profile` only)
  profile/<player_key>/
    labels/<campaign_id>.json           §1 CampaignLabel
    summaries/<campaign_id>.json        §2 CampaignSummary (cache, rebuildable)
    profile.json                        §2 ProfileDoc
  policies/
    <sha256>.json                       §4 PolicyVersion (content-addressed: name = policy_sha256)
    registry.jsonl                      §5 RegistryRow (append-only)
    registry.jsonl.lock                 §5 writer lock
    evidence/<sha256>.json              §6 Preregistration / GateReport (name = sha256 of the file bytes)
```

Readers: the daemon reads only `policies/<sha>.json`, `policies/registry.jsonl` and the built-in package file
(§4). It never opens `campaigns.jsonl`, `profile/**`, `bus.god.jsonl` or evidence (T7 F4 grep test). No
`priors.json` exists in v1 (020 §3 deferred).

Privacy: no save path, folder name, empire, species, leader or player name is stored in any C10 file (hashes,
enums, numbers and dates only). `player_key` is a plain config string (C8 `profile.player_key`).

## 1. Campaign index and labels

`campaigns.jsonl`: one `CampaignEvent` per line, `seq` gapless from 0, append-only (C5 §1 line rules: UTF-8, LF,
a final line without LF is ignored). Written only by `sae profile` (T9). Rows are never rewritten.

| `event` | Required fields | Meaning |
|---|---|---|
| `seen` | `folder_key`, `start_date`, `last_date`, `run_ids` (≥ 1); optional `game_versions`, `policy_sha256` | `sae profile update` indexed runs of this campaign |
| `closed` | — | `sae profile close <campaign>` or the user confirmed a newer campaign in the same folder |
| `labelled` | `label_sha256` (sha256 of the label file bytes) | a label was written |
| `excluded` | `reason` recommended | user removed it from profiles and tuning (e.g. mod-list change) |
| `deleted` | — | `sae profile delete --campaign` |

Fold rules (pure; T9 owns `campaign_state()`; the rules are contract):
1. Only runs whose `run_started.adapter_mode` is `live` or `release` are indexed; replay and synth runs only with
   the test-only flag `--include-synth`. The daemon writes nothing here.
2. `CampaignId = "c-" + sha256(folder_key + "|" + first run_id)[:16]`.
3. Split inside one folder: a run whose first game date is more than 5 game years earlier than the folder's current
   campaign `last_date` is a **candidate** new campaign; `sae profile update` asks the user (reload vs new game)
   and records a confirmed one as a `seen` row with a new id. Unconfirmed candidates stay out of profiles.
4. Status: `active` after `seen`; `closed` only by a `closed` row (never on idle time). A `seen` row after a
   `closed` row with a later `last_date` makes the campaign active again (no extra row); it leaves every profile
   until closed again. `excluded` and `deleted` remove it from every profile and tuning set.
5. Split: a campaign's first `labelled` row fixes its label order. Labels #1–#5 are `tune` (TEAM_PLAN V-1); #6,
   #10, #14 … are `sealed`; the rest `tune`. A sealed campaign becomes `tune` once ≥ 2 newer sealed campaigns
   exist **and** it has served ≥ 10 sealed queries (`stage2_*` registry rows whose GateReport names it).
6. Ironman campaigns have no observations in v1 (C6 §1.10) and never appear.
7. **Run segments** (v1.1). Runs are indexed per segment: consecutive `observation`s of one run with the same
   non-null `save_folder_key`; observations with a null key are skipped. A segment's key is the pair
   `(run_id, folder_key)` (a live run can load a different save mid-run).
8. **Idempotent update** (v1.1). `sae profile update` appends a `seen` row only for a new segment or a later
   `last_date`; a second update over unchanged runs appends nothing. Several `seen` rows of one campaign fold to:
   union of `run_ids`, `game_versions`, `policy_sha256`; min `start_date`; max `last_date`; `folder_key` from the
   first.
9. **Run lookup** (v1.1), per segment `(run_id, folder_key)`: (a) a non-deleted campaign with this `folder_key`
   whose `run_ids` contain `run_id` → that campaign (`seen` only if `last_date` is later); (b) a deleted campaign
   with this `folder_key` whose `run_ids` contain `run_id` → skip, no row; (c) otherwise the folder's current
   campaign = the latest non-deleted campaign with this `folder_key` by first `seen`, then the split rule (rule 3);
   no current campaign → new campaign with the rule 2 id. Matching is on the pair, never on `run_id` alone.
10. **Split prompt** (v1.1). When stdin is not a TTY a split candidate is reported on stderr and left pending (no
    row); it is asked again at the next update. "New game" writes `closed` for the old campaign (if not closed)
    then `seen` with the new id; "reload" writes `seen` on the old campaign.
11. **Deleted is terminal** (v1.1). No row is appended for a `deleted` campaign; its segments are never re-indexed.
12. **Campaign order** (v1.1; decay age, rolling-origin folds, "latest" for drift) = order of each campaign's first
    `seen` row.
13. **`profile.mode = "off"`** (v1.1): `sae profile update` exits 2 ("profile.mode is off"); `show`, `close`,
    `exclude`, `label`, `delete` still work. `exclude <id>` and `delete --campaign <id>` also remove
    `summaries/<id>.json` and `profile.json` at once (the next `update` rebuilds), so a privacy command takes effect
    even when `update` is blocked.
14. **Index writer lock** (v1.1). Every `campaigns.jsonl` append takes `campaigns.jsonl.lock` with
    `os.open(O_CREAT|O_EXCL|O_WRONLY)` (same pattern as §5); lock present → exit 1 ("index locked:
    campaigns.jsonl.lock"; a stale lock after a crash is removed by hand). Under the lock: if the file is non-empty
    and does not end with LF, write one LF first; seq = last valid seq + 1, counted **after** that repair (a
    repaired final line that parses as a complete row counts; a partial one becomes a skipped invalid line); flush,
    `os.fsync`; remove the lock in `finally`. Recovery from "campaigns.jsonl corrupted" (§7 seq gap): rename the
    index and re-run `update` (ids are deterministic; labels keyed by id survive; split answers are asked again).
15. **Labels are never overwritten** (v1.1). `sae profile label` refuses (exit 2) when `labels/<id>.json` or
    `<recording_dir>/label.json` exists (for synth recordings that file is the ground-truth schedule).

`CampaignLabel` (one model, two homes): `profile/<key>/labels/<campaign_id>.json` for indexed campaigns and
`<recording_dir>/label.json` beside a recorded session (C6 §3; SynthAdapter writes it from its schedule with
`source = "synth_schedule"`, `campaign_id = null`, `blind = true`). Segments have strictly increasing `from_date`.

```jsonl model=CampaignEvent
{"v":1,"seq":0,"at":"2026-10-19T09:00:00Z","campaign_id":"c-3f9a0c1b2d4e5f60","event":"seen","folder_key":"f-9c1185a5c5e9fc54","start_date":"2200.01.01","last_date":"2263.07.01","run_ids":["20261019-031500-live-0a1b","20261020-191000-live-77c2"],"game_versions":["Cepheus v4.4.4"],"policy_sha256":["0455844eb53cc7c608159f0192b83f7f506951c9c19aa6f2b4f4ac8925da8cfc"]}
{"v":1,"seq":1,"at":"2026-10-21T08:00:00Z","campaign_id":"c-3f9a0c1b2d4e5f60","event":"closed"}
{"v":1,"seq":2,"at":"2026-10-21T08:05:00Z","campaign_id":"c-3f9a0c1b2d4e5f60","event":"labelled","label_sha256":"5b2f0e6c1d4a3b29871605f4e3d2c1b0a9f8e7d6c5b4a3928170605f4e3d2c1b"}
{"v":1,"seq":3,"at":"2026-10-22T08:05:00Z","campaign_id":"c-3f9a0c1b2d4e5f60","event":"excluded","reason":"mod list changed"}
```

Invalid: a `seen` row without `run_ids`; a `closed` row carrying campaign facts; a `labelled` row without a hash.

```jsonl model=CampaignEvent invalid
{"v":1,"seq":0,"at":"2026-10-19T09:00:00Z","campaign_id":"c-3f9a0c1b2d4e5f60","event":"seen","folder_key":"f-9c1185a5c5e9fc54","start_date":"2200.01.01","last_date":"2263.07.01"}
{"v":1,"seq":1,"at":"2026-10-21T08:00:00Z","campaign_id":"c-3f9a0c1b2d4e5f60","event":"closed","folder_key":"f-9c1185a5c5e9fc54"}
{"v":1,"seq":2,"at":"2026-10-21T08:05:00Z","campaign_id":"c-3f9a0c1b2d4e5f60","event":"labelled"}
```

```json model=CampaignLabel
{"schema_version": 1, "campaign_id": "c-3f9a0c1b2d4e5f60", "source": "human",
 "segments": [{"from_date": "2200.01.01", "archetype": "turtle"}, {"from_date": "2241.01.01", "archetype": "early_aggression"}],
 "labelled_at": "2026-10-21T08:05:00Z", "blind": true}
```

```json model=CampaignLabel invalid
{"schema_version": 1, "campaign_id": null, "source": "synth_schedule",
 "segments": [{"from_date": "2230.01.01", "archetype": "turtle"}, {"from_date": "2210.01.01", "archetype": "balanced"}],
 "labelled_at": "2026-10-21T08:05:00Z", "blind": true}
```

## 2. Campaign summary and profile

Profile inputs are **strict**: only `fow = legal` lines (`observation`, `belief_update`) of indexed, closed,
non-excluded campaigns' `bus.jsonl`. No profile code opens `bus.god.jsonl` (T7 F3/F4).

| Feature | Definition (per month, views with `in_contact = true` averaged as `sae.core.features` does) |
|---|---|
| `expansion_rate_early` | Δ`systems_owned` per game year, first 30 game years |
| `expansion_rate_late` | same, after year 30 |
| `border_fort_ratio` | mean `fortified_border_starbases / frontier_systems` over months with frontier > 0 |
| `first_war_year` | game years from start to the first month with `player_war_count > 0` (`null` if never) |
| `war_share` | months with `player_war_count > 0` / months observed |
| `fleet_power_cat_mean`, `tech_cat_mean`, `economy_cat_mean` | mean of the C4 category |

Reloads: observations are ordered by run `t_wall`; a game-date regression drops every earlier-recorded
observation dated after the reloaded date (the abandoned timeline is not learned). `final_belief` is the last
`belief_update.posterior`.

`ProfileDoc` maths (T9; stdlib `math`/`statistics` only): Normal–Normal partial pooling per feature with designer
hyperpriors, decay weight `0.5 ** (age / 5)`; `archetype_alpha` is a decayed Dirichlet over final beliefs.
Model-enforced flags: `eligible == (len(source_campaigns) >= 8 and mean(folds.log_score_delta) > 0)`;
`drift_alarm == (drift_outside >= 3)`. Eligibility only unlocks re-submission of the deferred priors path
(maintainer D2); in v1 the profile is a read-only panel (`sae profile show`).

Rules (v1.1, proposal 025 §3):
1. **Fold-relative decay age.** Decay age is relative to the campaign being predicted (order: §1 rule 12): in fold
   k (fit 1..k, score k+1) campaign k has age 0; for drift, the fit on all but the newest gives the second-newest
   age 0.
2. **Deterministic rebuild.** `ProfileDoc.built_at` = `at` of the last valid `campaigns.jsonl` row folded (not wall
   time), so a rebuild from the same index and buses is byte-identical. Profile files (`summaries/*.json`,
   `profile.json`) are written as `json.dumps(model_dump(mode="json"), sort_keys=True, indent=1) + "\n"`,
   atomically (tmp + `os.replace`).
3. **Sealed-query count** for §1 rule 5 comes from the registry fold; with no gate yet (X1) it is 0.

```json model=CampaignSummary
{"schema_version": 1, "campaign_id": "c-3f9a0c1b2d4e5f60", "builder_version": "0.1.0",
 "game_versions": ["Cepheus v4.4.4"], "policy_sha256": ["0455844eb53cc7c608159f0192b83f7f506951c9c19aa6f2b4f4ac8925da8cfc"],
 "start_date": "2200.01.01", "last_date": "2263.07.01",
 "features": {"expansion_rate_early": {"value": 0.8, "n_obs": 360}, "expansion_rate_late": {"value": 0.4, "n_obs": 402},
   "border_fort_ratio": {"value": 0.71, "n_obs": 655}, "first_war_year": {"value": null, "n_obs": 762},
   "war_share": {"value": 0.0, "n_obs": 762}, "fleet_power_cat_mean": {"value": 0.3, "n_obs": 762},
   "tech_cat_mean": {"value": 0.6, "n_obs": 762}, "economy_cat_mean": {"value": 0.2, "n_obs": 762}},
 "final_belief": {"turtle": 0.7, "tech_rush": 0.1, "early_aggression": 0.05, "balanced": 0.15}}
```

```json model=ProfileDoc
{"schema_version": 1, "player_key": "default", "mode": "strict", "builder_version": "0.1.0",
 "built_at": "2026-10-21T08:10:00Z", "source_campaigns": ["c-3f9a0c1b2d4e5f60"],
 "archetype_alpha": {"turtle": 1.7, "tech_rush": 1.1, "early_aggression": 1.05, "balanced": 1.15},
 "features": {
   "expansion_rate_early": {"hyper_mu": 1.5, "hyper_sd": 0.8, "player_mu": 1.0, "player_sd": 0.6, "n_eff": 1.0},
   "expansion_rate_late": {"hyper_mu": 0.8, "hyper_sd": 0.6, "player_mu": 0.5, "player_sd": 0.5, "n_eff": 1.0},
   "border_fort_ratio": {"hyper_mu": 0.4, "hyper_sd": 0.25, "player_mu": 0.6, "player_sd": 0.2, "n_eff": 1.0},
   "first_war_year": {"hyper_mu": 25.0, "hyper_sd": 15.0, "player_mu": 30.0, "player_sd": 14.0, "n_eff": 1.0},
   "war_share": {"hyper_mu": 0.2, "hyper_sd": 0.15, "player_mu": 0.1, "player_sd": 0.12, "n_eff": 1.0},
   "fleet_power_cat_mean": {"hyper_mu": 0.0, "hyper_sd": 0.8, "player_mu": 0.2, "player_sd": 0.7, "n_eff": 1.0},
   "tech_cat_mean": {"hyper_mu": 0.0, "hyper_sd": 0.8, "player_mu": 0.4, "player_sd": 0.7, "n_eff": 1.0},
   "economy_cat_mean": {"hyper_mu": 0.0, "hyper_sd": 0.8, "player_mu": 0.1, "player_sd": 0.7, "n_eff": 1.0}},
 "folds": [], "eligible": false, "drift_alarm": false, "drift_outside": 0}
```

Invalid: `eligible` with one campaign (and an incomplete `features` map).

```json model=ProfileDoc invalid
{"schema_version": 1, "player_key": "default", "mode": "strict", "builder_version": "0.1.0",
 "built_at": "2026-10-21T08:10:00Z", "source_campaigns": ["c-3f9a0c1b2d4e5f60"],
 "archetype_alpha": {"turtle": 1.7, "tech_rush": 1.1, "early_aggression": 1.05, "balanced": 1.15},
 "features": {}, "folds": [{"k": 1, "log_score_delta": 0.3}], "eligible": true, "drift_alarm": false, "drift_outside": 0}
```

## 3. Engine priors — DEFERRED

`EnginePriors` / `priors.json` (proposal 020 §3) and the `priors_loaded` event with its contamination rule
(019 Part B) are **not part of C10 v1**. They are re-submitted once a profile is eligible (maintainer D2). Until
then `sae profile` writes no `priors.json` and the daemon reads no profile file.

## 4. PolicyVersion

One JSON file holding every behaviour constant that is not a contract rule.

- `detector` is the **only mutable surface** in v1 (ρ, likelihood clip, belief threshold, likelihood tables).
- `fixed` is copied verbatim from the parent: `counter_table` (must equal C3 §6; the model rejects anything else),
  `templates` (one per `Strategy`), `outcome_weights`. The gate rejects a candidate whose `fixed` differs from
  its parent's.
- Commitment months, safety floor, intent vocabulary, validator rules and fog code are **not** policy content;
  they stay in code and contracts (immutable surfaces).
- Identity: `policy_sha256(p)` = sha256 of `canonical_json(p)` (`model_dump(mode="json")`, sorted keys,
  separators `(",", ":")`, UTF-8, `allow_nan=False`). A file `policies/<name>.json` is valid only if
  `name == policy_sha256(parsed file)`.
- Built-in policy `0.1.0`: package data `src/sae/policies/builtin-0.1.0.json` (T4 writes it from today's
  `LikelihoodModel()` defaults, `EngineConfig` defaults, `COUNTER_TABLE`, `DEFAULT_EXPANSIONS`, `outcome.WEIGHTS`;
  `likelihood_source = "synth_aligned"`). A T4 test asserts it equals the code constants until the constants are
  deleted. Its sha is computed at load, never hard-coded.

**Daemon resolution (T4, once at start, before `open_sink`):**
1. `policy.pin = "builtin"` → built-in, `source = "builtin"`.
2. `policy.pin = <sha>` → `policies/<sha>.json`; it must exist, hash to `<sha>`, and be in
   `fold_registry(...).approved` → `source = "pinned"`.
3. `policy.pin = null` → `fold_registry(rows, builtin_sha).current`; `None` → built-in (`source = "builtin"`,
   no fallback); else that file, hash-checked → `source = "registry"` (the built-in sha after a rollback →
   `source = "builtin"`).
4. Any failure (missing or oversized file > 1 MiB, invalid model, hash mismatch, `RegistryError`, pinned sha not
   approved) → built-in with `run_started.policy.fallback_reason` (≤ 256 chars, no paths).
5. Game-version pin: the game version is unknown at start. On the first `Observation` with a `game_version`,
   if `not pin_matches(policy.game_version_pin, game_version)` the daemon emits one `health
   policy_pin_mismatch` (component `engine`, detail `<pin> vs <game_version>`) and **keeps** the policy (no
   switch during a run). Offline, `sae policy promote` and gate Stage 1 refuse a pin that does not match the
   recorded campaigns.
6. `--resume` re-resolves; a sha different from the bus's `run_started.policy.sha256` → exit 1
   ("policy changed; start a new run").
7. Config overrides: C8 v1.2 keeps numeric `engine.rho|likelihood_clip|belief_threshold` defaults until WU-2
   lands; with WU-2 the defaults become `null` (= from the policy) and an explicit number overrides the policy
   value (see C8 §2).

```json model=PolicyVersion
{"version": "0.1.0", "created_at": "2026-10-19T00:00:00Z", "game_version_pin": "v4.4.*", "parent_sha256": null,
 "detector": {"rho": 0.02, "likelihood_clip": 3.0, "belief_threshold": 0.45, "likelihood": {
   "border": {"turtle": [0.85, 0.25], "tech_rush": [0.1, 0.2], "early_aggression": [0.5, 0.25], "balanced": [0.35, 0.25]},
   "expansion": {"turtle": [0.5, 0.6], "tech_rush": [1.2, 0.7], "early_aggression": [3.0, 1.5], "balanced": [1.8, 0.9]},
   "fleet": {"turtle": [0.05, 0.15, 0.5, 0.2, 0.1], "tech_rush": [0.3, 0.35, 0.25, 0.07, 0.03], "early_aggression": [0.02, 0.05, 0.13, 0.3, 0.5], "balanced": [0.1, 0.2, 0.4, 0.2, 0.1]},
   "tech": {"turtle": [0.05, 0.15, 0.5, 0.2, 0.1], "tech_rush": [0.02, 0.05, 0.13, 0.3, 0.5], "early_aggression": [0.1, 0.25, 0.4, 0.15, 0.1], "balanced": [0.1, 0.2, 0.4, 0.2, 0.1]},
   "war_now": {"turtle": 0.1, "tech_rush": 0.1, "early_aggression": 0.65, "balanced": 0.15}}},
 "fixed": {"counter_table": {"turtle": "outscale_siege", "tech_rush": "early_punish", "early_aggression": "hold_counterpunch", "balanced": "baseline"},
   "templates": {"outscale_siege": {"econ": "military", "posture": "offensive", "reason_key": "turtle_siege"},
     "early_punish": {"econ": "military", "posture": "offensive", "reason_key": "tech_rush_punish"},
     "hold_counterpunch": {"econ": "military", "posture": "defensive", "reason_key": "aggression_hold"},
     "baseline": {"econ": "balanced", "posture": "deterrent", "reason_key": "baseline"}},
   "outcome_weights": {"d_fleet_share": 1.0, "d_systems_owned": 0.1, "d_alloys_income": 0.01}},
 "tuning": {"method": "builtin", "likelihood_source": "synth_aligned", "tuner_version": "none"}}
```

(Its `policy_sha256` today is `0455844eb53cc7c608159f0192b83f7f506951c9c19aa6f2b4f4ac8925da8cfc`; informative only.)

Invalid: a candidate that changes the counter table (and a categorical that does not sum to 1).

```json model=PolicyVersion invalid
{"version": "0.2.0", "created_at": "2026-10-19T00:00:00Z", "game_version_pin": "v4.4.*", "parent_sha256": "0455844eb53cc7c608159f0192b83f7f506951c9c19aa6f2b4f4ac8925da8cfc",
 "detector": {"rho": 0.02, "likelihood_clip": 3.0, "belief_threshold": 0.45, "likelihood": {
   "border": {"turtle": [0.85, 0.25], "tech_rush": [0.1, 0.2], "early_aggression": [0.5, 0.25], "balanced": [0.35, 0.25]},
   "expansion": {"turtle": [0.5, 0.6], "tech_rush": [1.2, 0.7], "early_aggression": [3.0, 1.5], "balanced": [1.8, 0.9]},
   "fleet": {"turtle": [0.05, 0.15, 0.5, 0.2, 0.2], "tech_rush": [0.3, 0.35, 0.25, 0.07, 0.03], "early_aggression": [0.02, 0.05, 0.13, 0.3, 0.5], "balanced": [0.1, 0.2, 0.4, 0.2, 0.1]},
   "tech": {"turtle": [0.05, 0.15, 0.5, 0.2, 0.1], "tech_rush": [0.02, 0.05, 0.13, 0.3, 0.5], "early_aggression": [0.1, 0.25, 0.4, 0.15, 0.1], "balanced": [0.1, 0.2, 0.4, 0.2, 0.1]},
   "war_now": {"turtle": 0.1, "tech_rush": 0.1, "early_aggression": 0.65, "balanced": 0.15}}},
 "fixed": {"counter_table": {"turtle": "early_punish", "tech_rush": "early_punish", "early_aggression": "hold_counterpunch", "balanced": "baseline"},
   "templates": {"outscale_siege": {"econ": "military", "posture": "offensive", "reason_key": "turtle_siege"},
     "early_punish": {"econ": "military", "posture": "offensive", "reason_key": "tech_rush_punish"},
     "hold_counterpunch": {"econ": "military", "posture": "defensive", "reason_key": "aggression_hold"},
     "baseline": {"econ": "balanced", "posture": "deterrent", "reason_key": "baseline"}},
   "outcome_weights": {"d_fleet_share": 1.0}},
 "tuning": {"method": "manual", "likelihood_source": "synth_aligned", "tuner_version": "none"}}
```

## 5. Registry (`policies/registry.jsonl`)

Append-only `RegistryRow` lines, `seq` gapless from 0. "Current" is the pure fold `fold_registry(rows,
builtin_sha256)`:

- Chain per sha, in exactly this order: `propose → prereg → stage1_pass → stage2_pass → approve`. A chain row out
  of order, any `stage1_fail`/`stage2_fail`, or `reject` breaks that sha for good.
- `promote` needs a complete, unbroken chain. `rollback` targets the built-in sha or a sha promoted earlier. Both
  carry the pointer they replace in `previous_sha256` (`null` before the first promote).
- `RegistryError` (→ built-in policy with `fallback_reason`): seq gap or repeat, a bad promote/rollback, or the
  current sha broken after promotion. Readers skip and count lines that fail validation (C5 §5), then fold the
  rest; a skipped line therefore shows up as a seq gap.
- `RegistryState.approved` = shas with a complete unbroken chain (what `policy.pin = <sha>` accepts).

| action | `by` (model-enforced) | `evidence_sha256` |
|---|---|---|
| `propose` | `tuner` or `human:<name>` | forbidden |
| `stage1_pass\|fail`, `stage2_pass\|fail` | `gate` | required (GateReport) |
| `prereg` | `human:<name>` | required (Preregistration) |
| `approve`, `reject`, `promote`, `rollback` | `human:<name>` | forbidden |

Human rows are written only by `sae policy prereg|approve|reject|promote|rollback` (T9): interactive TTY, the
user types the first 8 hex of the sha; the CLI refuses without a TTY (an agent cannot set its own margin or
approve). On one machine this is a speed bump, not a lock; the maintainer's review of `src/sae/policies/**` is the
lock for anything shipped in the repo.

**Trust-on-write** (review 2 action 5): a `by: human:<name>` row is trusted because it was written, not because it
is proven. `fold_registry` checks only its shape (`human:` + name pattern); it carries no signature and the reader
cannot tell a human-typed row from one any local process appended. This is accepted under D4 (personal use, no
signing). The guard is on the write side: WU-7's `append_registry_row` **refuses a row whose `by` starts with
`human:` unless `sys.stdin` and `sys.stdout` are both an interactive TTY** (`isatty()`), raising `RegistryError`
before taking the lock; `tuner` and `gate` rows need no TTY. A test must cover the refusal.

Writers: every writer appends through one helper, `c10_profile.append_registry_row(path, row)` — exclusive lock file
`registry.jsonl.lock` (`O_CREAT|O_EXCL`), re-read the last `seq`, write `seq + 1`, flush + fsync, remove the lock.
The helper **landed X1 (proposal 025)**; its first caller is WU-7 (`sae policy …`). Like §1 rule 14, a
final line without LF is terminated with one LF first and seq counts after that repair.

Rollback is one row and takes effect at the next daemon start. `policy.pin = "builtin"` is the emergency exit.

```jsonl model=RegistryRow
{"v":1,"seq":0,"at":"2027-03-01T10:00:00Z","action":"propose","sha256":"7d1e5f3a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e","by":"tuner"}
{"v":1,"seq":1,"at":"2027-03-01T10:05:00Z","action":"prereg","sha256":"7d1e5f3a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e","by":"human:danny","evidence_sha256":"1111111111111111111111111111111111111111111111111111111111111111"}
{"v":1,"seq":2,"at":"2027-03-01T10:40:00Z","action":"stage1_pass","sha256":"7d1e5f3a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e","by":"gate","evidence_sha256":"2222222222222222222222222222222222222222222222222222222222222222"}
{"v":1,"seq":3,"at":"2027-03-01T11:30:00Z","action":"stage2_pass","sha256":"7d1e5f3a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e","by":"gate","evidence_sha256":"3333333333333333333333333333333333333333333333333333333333333333"}
{"v":1,"seq":4,"at":"2027-03-01T12:00:00Z","action":"approve","sha256":"7d1e5f3a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e","by":"human:danny"}
{"v":1,"seq":5,"at":"2027-03-01T12:01:00Z","action":"promote","sha256":"7d1e5f3a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e","by":"human:danny","previous_sha256":null}
{"v":1,"seq":6,"at":"2027-03-09T20:00:00Z","action":"rollback","sha256":"0455844eb53cc7c608159f0192b83f7f506951c9c19aa6f2b4f4ac8925da8cfc","by":"human:danny","previous_sha256":"7d1e5f3a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e","reason":"felt passive"}
```

Invalid: an agent approving; a gate row without evidence; a gate-written `propose`.

```jsonl model=RegistryRow invalid
{"v":1,"seq":4,"at":"2027-03-01T12:00:00Z","action":"approve","sha256":"7d1e5f3a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e","by":"tuner"}
{"v":1,"seq":2,"at":"2027-03-01T10:40:00Z","action":"stage1_pass","sha256":"7d1e5f3a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e","by":"gate"}
{"v":1,"seq":0,"at":"2027-03-01T10:00:00Z","action":"propose","sha256":"7d1e5f3a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e","by":"gate"}
```

## 6. Gate evidence

`Preregistration` (human, before Stage 1; `budget >= min_n` is model-enforced) and `GateReport` (gate; `passed ==
all(checks.passed)` is model-enforced), both at `policies/evidence/<sha256 of the file bytes>.json`. v1 has only
`kind = "detector_replay"`; game-paired candidates need their own proposal after M4. Sealed-split checks report
`passed` and a `value` rounded to 2 significant digits only. With 1–3 sealed campaigns Stage 2 is a tripwire, not
a significance test; every Stage 2 report says so in a check `detail`.

```json model=Preregistration
{"schema_version": 1, "candidate_sha256": "7d1e5f3a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e",
 "incumbent_sha256": "0455844eb53cc7c608159f0192b83f7f506951c9c19aa6f2b4f4ac8925da8cfc", "kind": "detector_replay",
 "primary_metric": "loco_log_loss", "min_effect": 0.05, "non_inferiority_margin": 0.02, "budget": 6, "min_n": 1,
 "created_at": "2027-03-01T10:05:00Z"}
```

```json model=GateReport
{"schema_version": 1, "stage": 1, "candidate_sha256": "7d1e5f3a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e",
 "incumbent_sha256": "0455844eb53cc7c608159f0192b83f7f506951c9c19aa6f2b4f4ac8925da8cfc", "gate_version": "0.1.0",
 "checks": [{"name": "mutable_surface", "passed": true}, {"name": "sealed_split", "passed": true},
   {"name": "loco_log_loss_delta", "passed": true, "value": -0.071}], "passed": true, "created_at": "2027-03-01T10:40:00Z"}
```

Invalid: `passed` while a check failed.

```json model=GateReport invalid
{"schema_version": 1, "stage": 2, "candidate_sha256": "7d1e5f3a9b2c4d6e8f0a1b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f2a4b6c8d0e",
 "incumbent_sha256": "0455844eb53cc7c608159f0192b83f7f506951c9c19aa6f2b4f4ac8925da8cfc", "gate_version": "0.1.0",
 "checks": [{"name": "sealed_noninferior", "passed": false, "value": 0.031}], "passed": true, "created_at": "2027-03-02T10:40:00Z"}
```

## 7. Error handling

| Reader | Bad input | Behaviour |
|---|---|---|
| daemon (T4) | any policy or registry problem (§4 step 4) | built-in policy, `fallback_reason`; never exits for a policy problem except `--resume` sha change (exit 1) |
| `sae profile` (T9) | invalid `campaigns.jsonl` line | skip and count (C5 §5); a seq gap → refuse to append (exit 1, "campaigns.jsonl corrupted") |
| `sae profile` (T9) | invalid label/summary file | ignore the campaign, report it; summaries are rebuilt |
| gate (T7) | candidate fails the model, name ≠ hash, `fixed` ≠ parent's, sealed id in `tuning.campaign_ids` | `stage1_fail` with a GateReport naming the check |

## 8. Versioning

`v = 1` / `schema_version = 1`. New optional fields with defaults are a minor version (C10 v1.N, README §3.7).
Removing or retyping a field, or changing a fold rule, the canonical-JSON rule or the chain order, is v2
(`schema_version = 2`); readers reject versions they do not know. Changing the mutable surface (adding a tuned
field outside `detector`) needs its own proposal and M4 evidence (plan §5.5).

## 9. UNVERIFIED assumptions

| Id | Assumption | M0 test | Fallback |
|---|---|---|---|
| A-46 | Each campaign's autosaves live in one folder under `save games/`; a folder name is reused only by a new game of the same empire name | M0-11 | §1 split rule (user confirms) + `sae profile exclude` |
