# 020-T9 — Profile store, campaign index, label manifest, engine priors (new C10 §1–§3; C8 v1.2)

Status: **§1–§2 ACCEPTED-WITH-CHANGES** as C10 v1 §0–§2 + C8 v1.2 (2026-10-19, architect, WU-1; changes W1-7, W1-9). **§3 DEFERRED** (maintainer D2).
Original: PROPOSED (2026-10-19, T9 lead; revised after Fable proposal review B2, B3, M3, M7, M8 and
architecture review S2, S4). Reporter: T9. Contracts: new **C10 Profile and policy** (§1–§3 here, §4–§6 in
021); C8 §2, §6, §7 (v1.1 → v1.2, additive). Consumers: T9 (writes everything here), T4 (nothing now; reads `priors.json`
only when §3 is re-submitted), T2 + T7 (read `label.json` in recordings), T6 (console views), T8 (privacy docs).
Plan: `docs/expansion/player_profile_and_rsi_plan.md` §1, §4.

## Decision summary
1. All profile data lives under `<sae_dir>`, never under `user_dir`, never uploaded.
2. Only `sae profile …` (T9, offline CLI) writes profile files. The daemon reads no profile file now; later exactly one, `priors.json` (§3, deferred).
3. v1 is **strict only**: every profile input is a legal (`fow = legal`) line of a past campaign's `bus.jsonl`.
   No profile code opens `bus.god.jsonl`. `reputation` mode is a maintainer decision (plan §8 D1) and would
   need its own proposal.
4. The campaign index is append-only; status and split are a pure fold. Campaigns close only explicitly.
5. Profiles are rebuilt from scratch on every `sae profile update` (no incremental state, no build history).
6. The profile reaches the engine only when it has earned it: ≥ 8 closed campaigns **and** a positive mean
   rolling-origin log-score delta (Fable M3). Before that, `priors.json` is not written and the profile is a
   read-only panel (`sae profile show`).

## C8 v1.2 additions

| Section | Key (default) | Rule |
|---|---|---|
| `paths` | `sae_dir` (`null`) | `null` = `%LOCALAPPDATA%\sae` (Windows) or `${XDG_STATE_HOME:-~/.local/state}/sae` (Linux); `runs_dir` default is `<sae_dir>/runs` (the same path as today) |
| `profile` | `mode` (`"off"`) | `off` / `strict` |
| `profile` | `player_key` (`"default"`) | `^[a-z0-9_]{1,32}$`; a plain config string, not a salted hash of the OS user (Fable M7 accepted as is: one folder per key; multi-user PCs set it per OS user) |

```text
<sae_dir>/
  salt                                   32 random bytes; HMAC key for folder keys (proposal 019)
  runs/                                  C8 §6 (unchanged)
  campaigns.jsonl                        C10 §1 (append-only; `sae profile` only)
  profile/<player_key>/
    labels/<campaign_id>.json            C10 §1 CampaignLabel (`sae profile label`)
    summaries/<campaign_id>.json         C10 §2 CampaignSummary (cache, rebuildable)
    profile.json                         C10 §2 ProfileDoc
    priors.json                          C10 §3 EnginePriors (DEFERRED; never written until §3 is accepted)
  policies/                              C10 §4–§6 (proposal 021)
```

## C10 §1 — Campaign index and labels

```python
FolderKey = Annotated[str, Field(pattern=r"^f-(synth-)?[0-9a-f]{8,16}$")]      # = Observation.save_folder_key
CampaignId = Annotated[str, Field(pattern=r"^c-[0-9a-f]{16}$")]                 # sha256(folder_key + "|" + first run_id)[:16]
RunId = Annotated[str, Field(pattern=r"^\d{8}-\d{6}-[a-z]+-[0-9a-f]{4}$")]     # new alias in common.py (C8 new_run_id)

class CampaignEvent(Frozen):
    v: Literal[1] = 1
    seq: int = Field(ge=0)                          # gapless per file (C5 rule)
    at: AwareDatetime
    campaign_id: CampaignId
    event: Literal["seen", "closed", "labelled", "excluded", "deleted"]
    folder_key: FolderKey | None = None             # "seen"
    start_date: GameDate | None = None              # "seen": first game date in this folder's campaign
    run_ids: tuple[RunId, ...] = Field(default=(), max_length=1024)        # "seen"
    game_versions: tuple[str, ...] = Field(default=(), max_length=16)      # "seen", from Observation.game_version
    policy_sha256: tuple[Sha256Hex, ...] = Field(default=(), max_length=64) # "seen", from run_started.policy
    last_date: GameDate | None = None               # "seen"
    label_sha256: Sha256Hex | None = None           # "labelled"
    reason: str | None = Field(default=None, max_length=256)
```

Fold `campaign_state(events) -> dict[CampaignId, CampaignState]` (pure; T9 owns the code, the rules are contract):
- `seen` rows come from `sae profile update` scanning `runs/*/bus.jsonl` (`run_started`, `observation`). Only runs
  with `run_started.adapter_mode ∈ {live, release}` are indexed; synth and replay runs are ignored unless the
  test-only flag `--include-synth` is given (Fable plan review 2). The daemon writes nothing here.
- Campaign split inside one folder (Fable plan review 1; every game starts 2200.01.01, so start dates cannot
  separate games): a run whose first game date is earlier than the folder's current campaign `last_date` by
  > 5 years starts a **candidate** new campaign; `sae profile update` asks the user to confirm (reload vs new
  game) and records the answer as a `seen` row with a new `campaign_id`. Unconfirmed candidates stay out of profiles.
- Status: `active` after `seen`. `closed` **only** by `sae profile close <campaign>` or when the user confirms a
  newer campaign in the same folder. Never on idle time (Fable B3).
- A closed campaign with a newer `seen` row (`last_date` after the close) is treated as active again by the fold
  (no extra row) and leaves every profile until closed again.
- Split: a campaign's first `labelled` event fixes its split by label order: #1–#5 are `tune` (they are the
  five campaigns of TEAM_PLAN V-1's leave-one-out calibration); #6, #10, #14 … are `sealed`; the rest `tune`. Effective split: a sealed campaign becomes `tune` once ≥ 2 newer sealed campaigns
  exist **and** it has served ≥ 10 sealed queries (`stage2_*` rows naming it, C10 §6). Rows are never rewritten.
- `excluded` (user; e.g. heavy mod-list change) and `deleted` remove the campaign from every profile and tuning set.
- Ironman campaigns have no observations in v1 (C6 §1.10: `ironman.sav` is parsed meta-only), so they never
  appear here (Fable M2).

Label manifest (architecture review S4). One model, two homes:
`<sae_dir>/profile/<key>/labels/<campaign_id>.json` for real campaigns, and `<recording_dir>/label.json` next
to a recorded session (C6 §3; the SynthAdapter writes it from its schedule; `sae profile label --recording` for
real recordings).

```python
class LabelSegment(Frozen):
    from_date: GameDate
    archetype: Archetype

class CampaignLabel(Frozen):
    schema_version: Literal[1] = 1
    campaign_id: CampaignId | None            # None for a recording that is not in the index (synth)
    source: Literal["human", "synth_schedule"]
    segments: tuple[LabelSegment, ...] = Field(min_length=1, max_length=16)   # strictly increasing from_date
    labelled_at: AwareDatetime
    blind: bool                               # True: labeller was not shown SAE's beliefs (`sae profile label` default)
    note: str | None = Field(default=None, max_length=256)
```

## C10 §2 — Campaign summary and profile

Feature set v1 (closed, all `legal`, all from `observation.player_legal` views with `in_contact = true`,
averaged across observers the same way `sae.core.features` does):

| Feature | Definition |
|---|---|
| `expansion_rate_early` | Δ`systems_owned` per game year, first 30 game years |
| `expansion_rate_late` | same, after year 30 |
| `border_fort_ratio` | mean `fortified_border_starbases / frontier_systems` (months with frontier > 0) |
| `first_war_year` | game years from start to the first month with `player_war_count > 0` (`None` if never) |
| `war_share` | months with `player_war_count > 0` / months observed |
| `fleet_power_cat_mean` | mean `fleet_power_cat` |
| `tech_cat_mean` | mean `tech_cat` |
| `economy_cat_mean` | mean `economy_cat` |

Reloads (Fable M1): within a campaign, observations are ordered by run `t_wall`; a game-date regression drops every
earlier-recorded observation dated after the reloaded date before continuing (the erased timeline is not learned).

```python
FeatureName = Literal["expansion_rate_early", "expansion_rate_late", "border_fort_ratio", "first_war_year",
                      "war_share", "fleet_power_cat_mean", "tech_cat_mean", "economy_cat_mean"]

class SummaryFeature(Frozen):
    value: float | None
    n_obs: int = Field(ge=0)

class CampaignSummary(Frozen):
    schema_version: Literal[1] = 1
    campaign_id: CampaignId
    builder_version: str
    game_versions: tuple[str, ...]
    policy_sha256: tuple[Sha256Hex, ...]
    start_date: GameDate
    last_date: GameDate
    features: dict[FeatureName, SummaryFeature]
    final_belief: dict[Archetype, float] | None

class FeaturePosterior(Frozen):
    hyper_mu: float
    hyper_sd: float = Field(gt=0)
    player_mu: float
    player_sd: float = Field(gt=0)
    n_eff: float = Field(ge=0)                       # decayed count, half-life 5 campaigns

class FoldScore(Frozen):                             # rolling origin: fit 1..k, score k+1
    k: int = Field(ge=1)
    log_score_delta: float                           # profile minus hyperprior; > 0 = profile better

class ProfileDoc(Frozen):
    schema_version: Literal[1] = 1
    player_key: str = Field(pattern=r"^[a-z0-9_]{1,32}$")
    mode: Literal["strict"]
    builder_version: str
    built_at: AwareDatetime
    source_campaigns: tuple[CampaignId, ...] = Field(max_length=10_000)
    archetype_alpha: dict[Archetype, float]          # Dirichlet over final beliefs, half-life 5 campaigns
    features: dict[FeatureName, FeaturePosterior]
    folds: tuple[FoldScore, ...] = Field(max_length=10_000)
    eligible: bool                                   # len(source_campaigns) >= 8 and mean(folds.log_score_delta) > 0
    drift_alarm: bool
    drift_outside: int = Field(ge=0, le=8)           # features outside the 90 % predictive interval, latest campaign

```

Maths (T9; stdlib `statistics`/`math` only, no NumPy, Fable M8): Normal–Normal partial pooling per feature with
designer hyperpriors (constants in `sae.profile`, HYP), decay weights `0.5 ** (age / 5)`; drift alarm when ≥ 3 of 8
features fall outside the 90 % predictive interval (shown in `sae profile show`; no automatic action).

## C10 §3 — Engine priors (`priors.json`) — DEFERRED with 019 Part B

Not to be accepted now (Fable plan review "what to cut": eligibility needs ≥ 8 closed real campaigns). Until
then `sae profile` writes no `priors.json` and the daemon reads no profile file. Design kept for re-submission:

```python
B0_FLOOR = 0.02

class EnginePriors(Frozen):
    schema_version: Literal[1] = 1
    player_key: str = Field(pattern=r"^[a-z0-9_]{1,32}$")
    mode: Literal["strict"]
    profile_sha256: Sha256Hex
    source_folders: tuple[FolderKey, ...] = Field(max_length=10_000)   # contamination check (019)
    n_campaigns: int = Field(ge=8, le=10_000)
    b0: dict[Archetype, float]          # validator: 4 keys, each ≥ B0_FLOOR, sum 1 ± 1e-9
```

v1 carries `b0` only (R1). Likelihood offsets (R2) and a transition matrix (R3) are cut from v1 (Fable cut list).
Every value is a number, an enum, a hash or a hashed key, so T7's privacy test can allowlist the whole file.
Daemon read rule (T4): at start only, ≤ 1 MiB, `player_key` must equal config; anything else → `invalid_profile`.

## Privacy
`sae profile show | delete [--campaign]`. `delete` removes `profile/<key>/` and appends `deleted` rows;
`show` prints the salt path so the user knows what makes the keys unlinkable. No save path, folder name, empire,
species, leader or player name is stored anywhere in `<sae_dir>/profile` or `campaigns.jsonl` (hashes only).

## Not in v1 (cut, with the reason)
`reputation` mode and every god-view feature (B2); R2 offsets, R3, R4, prototype matching, self-report; backfill
from old saves; raw-save retention; Ironman profiles; "response to pressure" (descriptive only, confounded).

## Migration
Additive; `profile.mode = "off"` keeps today's behaviour. C8 v1.2 (`v = 1`). C10 starts FROZEN v1 on acceptance.

## Follow-ups
architect: `src/sae/contracts/c10_profile.py`, C10 doc, C8 rows, schemas, `Sha256Hex`. T9: `src/sae/profile/**`.
T4: priors read (019). T2: `label.json` written by `sae synth --record`. T7: privacy allowlist test, reload test.
T8: user-guide privacy and fairness section.
