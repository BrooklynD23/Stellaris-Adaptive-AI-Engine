# Wave 3 design: campaign memory (carry)

2026-11-15, architect. Design only; no code, contract or git change. Built on the Wave 1 tree (`abstain` in
`core/strategy.py`, visibility-run fix in `features.extract`). Inputs: `IMPLEMENTATION_WAVES.md` Wave 3 rows and
§3/§7/§8/§9, `opt_wave_C_review.md` B1-B6, `opt_implementation_waves_review.md` B9-B11/B13, `engine-core.md`.

Rev 2 (2026-11-15): folds in the Fable gate `docs/status/opt_w3_design_review.md` (ACCEPT WITH CHANGES, B1-B8).
The map is in §15. The maintainer's three defaults are adopted (§14). §13 is my own attack pass, not the gate.

## 0. The design in 12 lines

1. No new file on disk. The carry source is the previous run's `runs/<run_id>/state.json`, which gains one block
   (`carry`) and one flag (`window_void`). `state_version` stays 1; old files load.
2. Flag `engine.carry_state` (C8, default `false`). Off: `EngineState.carry` is `None` and nothing below runs.
3. On: a new run starts in `hold`. Ticks run features + belief only (the existing `suspended` early return). No
   `strategy.select`, no batch.
4. First observation with a `save_folder_key`: the daemon scans at most 64 newest run dirs **of the same run mode**
   for a `state.json` with that key and hands it to the pure engine, which decides once.
5. Decision order: no prior -> `player_sid` differs -> C10 split predicate (regression > 60 months) -> regression
   > `commitment_months` -> carry. Policy sha differs -> carry belief + features, drop the window.
6. Carried slice = `features`, `belief`, `strategy`, `window_open`, `window_void`. Everything else (lifecycle,
   seq, intent ids, decision ids, epoch, governor mirror) stays the live run's.
7. No key after 12 held ticks -> fresh, `carry_refused=no_key`. An operator force ends the hold at once.
8. Regression of D months (in-session or at carry) uses one function: posterior decays `(1-rho)^D` toward uniform,
   uniform if D > `commitment_months`; `window_open=None`; the window's outcome gets `metrics["void"]=1`. A window
   opened after the load point is closed. This part (W3-1b) is **not** behind the flag.
9. One `health` event of new kind `carry` per decision: `carried_from=<run_id>` or `carry_refused=<reason>`.
10. The split predicate moves to `sae.contracts.c10_profile`; the daemon never imports `sae.profile` (CI-4).
11. Cost: +0.2 KB in `state.json`, +0 MiB resident, one scan of <= 64 small files per run (a few ms).
12. Identity still rests on UNVERIFIED A-46. A same-name new game inside the old campaign's last 24 months is
    carried wrongly. The maintainer accepted this hole behind the default-off flag; exact identity is a follow-up (§11, §14).

## 1. Code facts this design relies on (read 2026-11-15)

| Fact | Where |
|---|---|
| `save_folder_key` is `None` until the first parsed save, and is reset to `None` by every HELLO / date regression (`_bump`) | `adapters/assembler.py:142-152, 220-233, 252` |
| Evidence needs in-contact `player_legal` views, which exist only after a parsed save. So in live mode the key and the first evidence arrive in the same observation (unless the salt fails) | `assembler.py:277-292`, `features.py:125-127` |
| `run_tick` already has a "features + belief, no select, no batch" path: `if s.suspended: return` | `core/engine_tick.py:89-90` |
| `_reset` keeps the posterior and `window_open` across a regression; `strategy.reanchor` keeps remaining months | `core/engine.py:49-59`, `core/strategy.py:57-66` |
| A force/clear-force closes the window through `reopen=True`; `select` then emits `WindowClosed` | `core/strategy.py:49-54, 94-96, 129-131` |
| Replay and synth keys are `f-synth-` + sha256(run_id), so they never match across runs | `adapters/replay.py:94`, `synth.py:155` |
| Three reward readers exist: `tune/cli.py:_v3`, `telemetry/mapping.py:163-166`, `tools/console/views.sql:85`. `tune/fit.py` and `bench/gate/metrics.py` read no outcome today | grep `OutcomeEvent`, `reward` |
| `SaveWatcher` watches all of `save games`; `on_parse` rejects only saves more than 1 month ahead, then overwrites `_folder_key`. A save touched in another folder flips the key without a HELLO | `assembler.py:231-252`, `live.py:255` |
| There is no run retention; bench, gate, synth, replay and live runs share `runs/` | `daemon/disk.py:8` |
| Split rule today: `mi_of(current.last_date) - mi_of(seg.first_date) > 60` | `profile/index.py:26, 228` |
| `sae_p_expires` expires a pending batch, not applied state, so a hold does not lapse applied intents | `adapters/simulator.py:427` (synth model; real game UNVERIFIED) |

## 2. Data structures

### 2.1 Contracts (W3-A0, architect)

`src/sae/contracts/c10_profile.py` (C10 v1.4):

```python
SPLIT_GAP_MONTHS: Final = 60  # 5 game years (C10 section 1 rule 3)

def month_index(date: GameDate) -> int:            # year * 12 + month - 1; same value as core.dates.mi_of
def split_candidate(campaign_last_date: GameDate, run_first_date: GameDate) -> bool:
    """True when the run starts more than SPLIT_GAP_MONTHS earlier than the campaign's last date."""
    return month_index(campaign_last_date) - month_index(run_first_date) > SPLIT_GAP_MONTHS
```

`src/sae/contracts/c5_bus.py` (C5 v1.6, additive):

```python
class HealthKind: CARRY = "carry"                  # one per carry decision, component "engine"

class CarryRefusal(StrEnum):
    NO_PRIOR = "no_prior"              # no earlier state.json with this key
    NO_KEY = "no_key"                  # 12 held ticks without a keyed observation
    OPERATOR_FORCE = "operator_force"  # a C9 force arrived during the hold
    PLAYER_SID = "player_sid"          # player sid differs or is unknown
    SPLIT = "split"                    # split_candidate() fired
    REGRESSED = "regressed"            # regression > commitment_months (and <= 60)
    POLICY_CHANGED = "policy_changed"  # partial: belief + features carried, window dropped

VOID_KEY: Final = "void"
def outcome_is_void(p: OutcomePayload) -> bool:    # p.metrics.get(VOID_KEY) == 1.0
def format_carry(carried_from: RunId | None, parts: Sequence[str], refused: CarryRefusal | None,
                 prior: RunId | None = None) -> str
def parse_carry(detail: str) -> CarryDecision      # CarryDecision(carried_from, parts, refused, prior); ValueError
```

`health.detail` grammar for kind `carry`: pipe key=value, key order `carried_from|parts|carry_refused|prior`,
absent keys omitted. `prior` appears only on a refusal that found a prior run:

```text
carried_from=20261114-193000-live-0a1b|parts=belief,features,window
carried_from=20261114-193000-live-0a1b|parts=belief,features|carry_refused=policy_changed
carry_refused=split|prior=20261114-193000-live-0a1b
carry_refused=no_prior
```

`health.metrics` for kind `carry` (floats, all optional): `regress_months`, `gap_months`, `hold_ticks`,
`skipped_states`. Numbers live in `metrics`, ids and enums in `detail`.

`src/sae/contracts/c8_config.py` (C8 v1.8): `EngineConfig.carry_state: bool = False`. No preset sets it.

C6 (additive, +1 minor; not in the plan's 036 row): recording dir gains an optional file `folder_key` (one
`FolderKey`, LF). `ReplayAdapter` uses it instead of `f-synth-`; `LiveAdapter(record_dir=...)` writes it at the
first keyed parse. Absent -> today's behaviour.

### 2.2 Engine state (`src/sae/core/state.py`, T4)

```python
class CarryIdent(Frozen):
    save_folder_key: FolderKey | None = None
    player_sid: Sid | None = None
    policy_sha256: Sha256Hex | None = None      # daemon always fills it (built-in sha when no policy is loaded)

class CarryState(Frozen):
    phase: Literal["hold", "carried", "fresh"] = "hold"
    hold_ticks: int = Field(default=0, ge=0)
    ident: CarryIdent = CarryIdent()
    ident_epoch: int | None = None              # session_epoch at which ident was set or last confirmed (B1)
    foreign_epoch: int | None = None            # epoch in which a foreign save key was last reported (B1)
    carried_from: RunId | None = None
    refused: CarryRefusal | None = None         # set with phase "fresh", or "carried" + policy_changed

class CarryPrior(Frozen):                       # daemon -> engine, never stored
    run_id: RunId
    state: EngineState

class EngineState(Frozen):
    ...                                         # unchanged fields
    carry: CarryState | None = None             # None = flag off (and every pre-W3 file)
    window_void: bool = False                   # the open window's outcome must be marked void

class EngineCtx(Frozen):
    ...
    policy_sha256: str | None = None            # daemon: deps.policy.ref.sha256, else the built-in policy's sha
```

Constants in `core/carry.py`: `CARRY_HOLD_MAX_TICKS = 12`, `CARRIED_FIELDS = ("features", "belief", "strategy",
"window_open", "window_void")`. In `daemon/carrystore.py`: `CARRY_SCAN_MAX = 64`.

### 2.3 Functions

| Function | File | Pure | Order |
|---|---|---|---|
| `belief.forget(state, months, rho) -> BeliefState` (the `1/n + decay*(x - 1/n)` line of `update`, then `_norm`) | `core/belief.py` | yes | W3-1b |
| `regress(s, mi, d, cfg) -> EngineState` | `core/regress.py` (new) | yes | W3-1b |
| `carry.begin(cfg)`, `carry.lookup_key(s, obs)`, `carry.resolve(s, prior, obs, ctx)`, `carry.hold_step(s, ctx, date)`, `carry.holding(s)` | `core/carry.py` (new) | yes | W3-1 |
| `find_prior(runs_dir, key, run_id) -> tuple[CarryPrior \| None, int]` (`run_id` = this run: excluded, and its mode segment is the filter) | `daemon/carrystore.py` (new) | no (reads) | W3-1 |

## 3. Regression rule (W3-1b, not behind the flag)

`on_observation` computes `d = max(0, s.last_obs_mi - mi)` when `regressed` (0 when `last_obs_mi` is `None`).
`_reset` keeps its current body, then calls `regress(s, mi, d, cfg)` when `d >= 1`:

| Field | d <= `commitment_months` | d > `commitment_months` |
|---|---|---|
| `belief.posterior` | `forget(belief, d, rho)` = decay `(1-rho)^d` toward uniform | uniform |
| `belief.last_evidence_mi` | `min(old, mi)` | `min(old, mi)` |
| `features` | `features.reset_session` (already drops samples after `mi`) | same |
| `strategy` | `reanchor` (remaining months, never extended); plus `reopen=True` if the window opened after the load point (rule A) | `reanchor`, then `reopen=True` |
| `window_open` | `None` | `None` |
| `window_void` | rule B | rule B |

Rule A (review B3), evaluated on the strategy state **before** `reanchor` (which overwrites `commit_from_mi`):

```python
opened_after_load = s.strategy.commit_from_mi is not None and s.strategy.commit_from_mi > mi
strat = strategy.reanchor(s.strategy, mi)
if opened_after_load or d > cfg.commitment_months:
    strat = replace(strat, reopen=True)
```

A counter opened at 2211.01 in the abandoned branch does not survive a reload to 2210.01: `select` closes it on
this tick (void outcome) and re-decides from the decayed posterior. After an earlier reset `commit_from_mi` is the
reset month, not the true opening, so the rule can only close more windows, never fewer.

Rule B (review B4), sticky:

```python
window_void = s.window_void or s.strategy.commit_from_mi is not None
```

It is cleared in exactly one place: `run_tick`, where `strat.opened_by == did` (a new window opened). A second
regression inside the same window cannot clear it.

Effects:
1. d <= 24 and the window opened at or before the load point: the commitment stays; when its window closes, the
   outcome carries `metrics["void"]=1.0` and `reward=0.0`.
2. d > 24, or the window opened after the load point: `reopen=True` makes `select` close the window on this same
   tick (void outcome) and re-decide. From a uniform posterior that is a `below_threshold` abstention window, which
   D1 can pre-empt; from a decayed posterior still above the threshold it is a fresh `table` window.
3. An operator force survives both cases (`reanchor` keeps it; `select` checks `forced` first).
4. `engine_tick.run_tick`: when it emits an outcome and `s.window_void`, it copies the payload with
   `metrics | {"void": 1.0}` and `reward = 0.0`. `core/outcome.py` is not edited (W4-1 owns it next wave).
5. d = 0 (reload inside the same month, or forward load): nothing new happens; today's behaviour.

`rho` = 0.02 in builtin 0.1.0: d = 6 keeps 88.6 % of the distance from uniform, d = 24 keeps 61.6 %.

## 4. Carry state machine (W3-1, behind `engine.carry_state`)

`engine.initial_state(cfg)` returns `EngineState(carry=CarryState())` when `cfg.carry_state`, else `EngineState()`.

| State | Event | Guard | Next | Bus |
|---|---|---|---|---|
| (off) `carry=None` | any | flag off | (off) | none |
| `hold` | decision tick, obs unkeyed | `hold_ticks < 12` | `hold`, `hold_ticks += 1`; features + belief only | none |
| `hold` | decision tick, obs unkeyed | `hold_ticks >= 12` | `fresh`, `refused=no_key`; `select` runs on this tick | `carry_refused=no_key` |
| `hold` | C9 force (queued or applied) | - | `fresh`, `refused=operator_force` | `carry_refused=operator_force` |
| `hold` | keyed observation | daemon ran `find_prior` | `carry.resolve` -> `carried` or `fresh` | one `carry` event |
| `fresh` (`ident.key` unset) | keyed observation | - | `fresh`, ident and `ident_epoch` filled; no lookup, no swap | none |
| `carried` / `fresh` | keyed observation, key == `ident.key` | - | same; `ident_epoch = obs.session_epoch` | none |
| `carried` / `fresh` | keyed observation, key != `ident.key` | `obs.session_epoch != ident_epoch` | campaign switch: slice reset to defaults, then `carry.resolve` for the new key | one `carry` event |
| `carried` / `fresh` | keyed observation, key != `ident.key` | `obs.session_epoch == ident_epoch` | **foreign save**: ident and slice unchanged, no lookup | one `invalid_event` "foreign save key" per epoch (`foreign_epoch`), no `carry` event |
| any | regression in-run | - | same phase; §3 applies | none |

Rules:
1. Order inside `_Loop._on_obs`: `key = carry.lookup_key(state, o)`; if not `None`: `prior, skipped =
   find_prior(run_dir.parent, key, run_id)`, `state, out = engine.resolve_carry(state, prior, o, ctx)`, publish;
   then the normal `engine.on_observation`. The keyed save's evidence is applied on top of the carried belief.
2. `phase` never returns to `hold`. After `fresh`, no later swap (B2: never swap after this run has decided).
3. `--resume` / re-exec loads the run's own `state.json`, phase included; `hold_ticks` survives. If the loaded
   state has a `carry` block and `cfg.carry_state` is false, `_Loop.__init__` sets `carry=None` (one line), so a
   resumed hold never emits a `carry` event with the flag off (review B7).
4. The hold counts decision ticks, not dates, so reloads cannot stretch it.
5. Campaign switch mid-run drops the old window without an `outcome` (the window belongs to another campaign).
   Without this row the old campaign's ident would be saved over the new campaign's evidence.
6. A key change needs a session reset to count as a switch (review B1). A save written in another folder
   (Steam Cloud sync, a copied file) changes the key inside one epoch; it must not wipe belief and strategy.
   `lookup_key` returns `None` for it. Not closed by this rule: a foreign save that is the **first** keyed
   observation after a load or at run start is still taken as the campaign (§13 item 6).
7. The hold blocks every batch, governor knob-only batches included (same as a C9 pause). The game-PC run
   measures how long that lasts (§10 step 4).

### 4.1 `carry.resolve` decision (first match wins)

`P` = prior state, `L` = `mi(obs.game_date)`, `D` = `mi(P.last_obs_date) - L` (regression if > 0, gap if < 0).

| # | Condition | Result | `detail` |
|---|---|---|---|
| 1 | no prior (none found, or the only matches are unusable, §4.2 step 3) | fresh | `carry_refused=no_prior` |
| 2 | `obs.player_sid is None` or `!= P.carry.ident.player_sid` | fresh | `carry_refused=player_sid\|prior=<run>` |
| 3 | `split_candidate(P.last_obs_date, obs.game_date)` (D > 60) | fresh | `carry_refused=split\|prior=<run>` |
| 4 | D > `cfg.commitment_months` | fresh | `carry_refused=regressed\|prior=<run>` |
| 5 | policy sha differs | carried, parts `belief,features` | `carried_from=<run>\|parts=belief,features\|carry_refused=policy_changed` |
| 6 | otherwise | carried, parts `belief,features,window` | `carried_from=<run>\|parts=belief,features,window` |

Merge for rows 5-6, in this order:
1. Take `P`'s slice (`CARRIED_FIELDS`). Row 5: `strategy=StrategyState()`, `window_open=None`,
   `window_void=False` (an operator force stored in `P` is dropped with the window).
2. `features = features.reset_session(features, L)` (a new session is a HELLO; same anchor rules as in-session).
3. If D >= 1: `regress(slice, L, D, cfg)` (§3), rules A and B included. So a carried regression voids the window's
   outcome, and a window that `P` opened after the load point is closed on the first tick.
4. If D <= 0: `commit_until_mi` stays **absolute**. No `reanchor`: game time passed, the commitment ran. A window
   that expired during the gap closes on the first tick with a normal (non-void) outcome.
5. `state.model_copy(update=slice | {"carry": CarryState(phase="carried", ident=..., ident_epoch=obs.session_epoch,
   carried_from=..., refused=...)})`.
   No other field is touched. Hold-phase features/belief of this run are replaced (normally empty, §1 row 2).

Row 4 exists because after a regression past the window nothing useful is left (uniform posterior, no window,
samples cut), and because it is the only signal that catches "new game at 2200.01 after a 2204 campaign"
(D = 48, under the 60-month split rule). Belief forgetting over a forward gap needs no code: `belief.update` already
decays by `k = obs_mi - last_evidence_mi`.

### 4.2 `find_prior`

1. List `runs_dir`, keep names matching the `RunId` pattern **whose mode segment equals this run's mode**
   (`live`, `release`, `replay`, `synth`), drop this run's id, sort descending (UTC start time), then take the first
   `CARRY_SCAN_MAX` (review B2). So 64 bench or synth runs cannot push a live prior out of the window, and a
   replay of the user's own recording never becomes the prior of the live game.
2. Per dir: read `state.json` bytes; skip if the key string is not in the bytes (no JSON parse); else
   `load_state`; match when `state.carry is not None and state.carry.ident.save_folder_key == key`.
3. `StateFileError` / `OSError`, or a state whose `belief.posterior` length differs from `len(ARCHS)` -> count in
   `skipped_states`, log, continue with older runs. Never raises.
4. `# ponytail:` 64-run window; a campaign idle for more than 64 same-mode sessions of other play starts fresh
   (`no_prior`). Add a per-key index file only if a user hits it.

Runs made with the flag off have `carry=None` and are never a source. Turning the flag on gives memory from the
next session onward.

## 5. Interaction with D1 pre-emption and the 24-month commitment

| Case | Behaviour |
|---|---|
| Carried abstention window | `abstain=True` kept. A confident counter tick in session 2 pre-empts it (C3 §6.1 rule 1) |
| Carried counter window | Held to its absolute `commit_until`; D1 never touches it; never extended |
| Carried window, 0 < D <= 24, opened at or before the load point | `reanchor`: remaining months from the load point, total <= 24; outcome void |
| Any window opened after the load point (in-session or carried) | Closed on the first tick, void; `select` re-decides from the decayed posterior |
| Hold | No window exists, so nothing to pre-empt or hold. First `select` runs on the first tick after resolve |
| Fresh after hold | Same as a flag-off first tick, only later: abstention window, D1 applies |
| Policy changed | Window dropped; the first tick re-decides under the new policy's table and templates |
| Safety floor | Static template check, untouched (vacuous today; do not cite) |

Reaction-time cost of the hold: zero in the normal case. A counter needs evidence, evidence needs a parsed save,
and that save brings the key (§1 row 2). The hold only removes the tick-1 baseline batch, which is the batch that
reverts the previous session's applied counters. The 12-tick cap bites only when no save is ever keyed (salt
failure, parser broken, Ironman), where there is no evidence either. Demo wording: §10.1.

## 6. Fog-of-war and fairness argument

1. Fog: the carried slice is built only from `Observation.player_legal` in-contact views (`features`, `belief`) and
   managed AIs' own metrics (`window_open`). `state.json` never held god data and the engine never opens
   `bus.god.jsonl` (C6 §4). Carry adds memory, not vision: the AI knows in session 2 what it legally saw in
   session 1, as a human opponent would.
2. Timeline: evidence dated after the loaded save belongs to a branch this game never had. Rule: regression D
   decays it `(1-rho)^D`; past `commitment_months` it is erased (in-session) or the carry is refused. This is the
   bound approved in the C review (B3), not exact: for D <= 24 an attenuated share of abandoned-branch evidence
   stays in the posterior. Samples after the load point are always dropped.
3. Reward: an outcome whose window spans a regression compares two timelines. It is marked void and excluded
   from V-3 and tune, never scored 0.
4. Commitment: a reload cannot extend a commitment (remaining months only), cannot hold one past a deep
   regression, and cannot keep one that was decided on evidence this timeline never showed (rule A).
5. Not covered: a forward load of another branch (later date, different history) looks like a gap and is carried
   in full. Undetectable without a mod-side campaign/branch id.

## 7. Bus, state file and migration

| Item | Rule |
|---|---|
| `health` kind `carry` | Exactly one per `carry.resolve` and one per hold end without a key. Component `engine`. Never emitted with the flag off |
| `outcome.metrics.void` | `1.0` on a void outcome; key absent otherwise. `reward` is `0.0` and must be ignored; readers call `outcome_is_void` |
| Cross-run window | With carry, an `outcome` in run B can have `window_from` before B's first event and the `decision_id` of a decision in run A. C5 text says so; the audit view joins on `carried_from` |
| `state.json` | New keys `carry` (default `null`) and `window_void` (default `false`). `state_version` stays 1. A pre-W3 file loads; `--resume` works |
| Downgrade | `state_version` stays 1, so old -> new is safe and new -> old fails closed: pre-W3 code rejects the unknown keys (`extra="forbid"`) with the misleading text "state file corrupt (invalid fields)", exit 1. W3-1b writes `window_void` for **every** user, flag on or off, so any W3 `state.json` is unreadable after a rollback. Nothing is lost: the fix is to start a new run instead of `--resume`. This note goes into the changelog and `docs/user_guide.md` (W3-5) |
| Flag off | `carry` is always `null`; no lookup, no event, no ident written; a resumed state's `carry` block is dropped |
| Void readers | Every reader of an outcome `reward` calls `outcome_is_void` (Python) or tests `metrics.void` (SQL). Today: `tune/cli.py`, `telemetry/mapping.py`, `tools/console/views.sql`. Contract test D-10b enforces it for future readers |
| Privacy | No new file. Deleting a run dir removes its carry state. `folder_key` in a recording is the salted HMAC already present in the bus |

## 8. Performance and memory (150 MiB daemon budget)

| Item | Estimate | Basis |
|---|---|---|
| `state.json` growth | +~220 bytes (`carry` block + `window_void`) on 8 KB (39 KB at 16 AIs) | field count |
| Resident growth | ~0. The slice replaces same-sized objects; `FeatureMemory.samples` stays capped at 48; lifecycle is never carried | `features.MAX_SAMPLES` |
| Growth over N chained sessions | none: state is bounded, not appended | test P-2 |
| `find_prior` | <= 64 same-mode reads of <= 39 KB with a substring pre-filter; at most 1 full `load_state` (~1-2 ms) per match. Once per run plus once per mid-run campaign switch. Normal case 1-2 reads | engine-core §3.1 sizes |
| Transient memory | one extra `EngineState` (< 1 MiB) during resolve | - |
| Per-tick cost | one `None` check when off; one enum compare when on | - |
| Blocking | the scan runs on the consumer task right after a save parse; Windows + Defender file-open cost is UNVERIFIED (budget: < 100 ms; M0 records it) | A-52 below |

## 9. Work orders (replaces the plan's Wave 3 rows)

Model: sonnet unless marked opus. Every order runs `uv run pytest -x -q`, `uv run mypy --strict`, `uv run ruff
check`. Implementers do not touch `changelog.d/`.

| Id | Team | Files (exclusive) | Spec | h |
|---|---|---|---|---|
| W3-A0 | architect (opus) | `contracts/**`, `src/sae/contracts/**`, `tests/contract/**` | Proposal 036: §2.1. C8 v1.8, C5 v1.6, C10 v1.4, C6 +1 minor (`folder_key` file). States which part is behind the flag (§3 is not) and the downgrade rule (§7). New UNVERIFIED rows (§10). Reader contract test D-10b. Schemas re-exported | 5.5 |
| W3-0 | T7 | `tests/integration/test_w3_flag_off.py`, `tests/integration/golden/w3_flag_off.json` | **Only on the commit where Waves 1-2 are merged** (hash supplied by the coordinator; the implementer runs no git) and before any W3 source edit: 6-year synth run (seed 2, turtle). Store: that commit hash, sha256 of the normalised bus, the per-event-type counts, the normalised `state.json`. Normaliser = `tests/unit/t6/test_fsync_synth.py::_normalised` rules plus drop `run_started.payload.{contracts,config_sha256,engine_version,platform,python}`; state: pop `carry` (assert `None`) and `window_void` (assert `False`). On a mismatch the test prints the event-type count diff first | 2 |
| W3-1b | T4 (opus), after W3-A0 and W3-0 | `src/sae/core/engine.py`, `core/engine_tick.py`, `core/state.py` (`window_void` only), `core/belief.py` (`forget`), `core/regress.py` (new), `tests/unit/t4/test_regress.py`, `tests/unit/t4/golden/w3_engine_pre.json`, expectations in `tests/unit/t4/test_engine.py` | **Step 0, same merged commit, before any source edit:** capture the engine-level golden (§12 G-1). Then §3 with rules A and B | 5.5 |
| W3-1 | T4 (opus), after W3-1b and W3-4b | `src/sae/core/carry.py` (new), `core/engine.py`, `core/engine_tick.py`, `core/state.py`, `src/sae/daemon/carrystore.py` (new), `daemon/loop.py`, `tests/unit/t4/test_carry.py`, `test_carrystore.py`, `test_carry_sessions.py` | §2.2, §4 (epoch-gated switch, foreign-save event, mode filter, flag-off resume line). Imports nothing from `sae.profile` / `sae.tune` | 10.5 |
| W3-2 | T9, after W3-A0 | `src/sae/profile/index.py`, `src/sae/tune/cli.py`, `tests/unit/t9/` | `index.py` imports `split_candidate` and `SPLIT_GAP_MONTHS` from contracts (keeps the name importable); `_v3` skips `outcome_is_void` and prints the skipped count | 2 |
| W3-4 | T2 | `src/sae/adapters/synth.py`, `tests/unit/t2/test_synth_effect.py` | Reacting synth, §9.1 | 4 |
| W3-4b | T2, after W3-A0 | `src/sae/adapters/replay.py`, `adapters/live.py`, `adapters/recording.py`, `tests/unit/t2/test_replay_folder_key.py` | Optional `folder_key` file in a recording: replay uses it as the fixed key; live recorder writes it once at the first keyed parse. Absent or invalid -> `f-synth-` and one `invalid_event` | 2 |
| W3-5 | T8 | `docs/handoff/GAME_PC_AGENT.md`, `docs/m0_checklist.md`, `docs/user_guide.md` | §10 game-PC protocol; rollback note (§7 Downgrade); Demo v3 statements (§10.1) copied into the demo notes | 2 |
| W3-6 | T6, after W3-A0 | `src/sae/telemetry/mapping.py`, `tools/console/views.sql`, `tests/unit/t6/` | Void outcome: no `outcome/reward` scalar, text "void". `views.sql:85`: `outcome_reward` is NULL when `payload->'metrics'->>'void' = '1.0'`, new column `outcome_void` (review B5). `carry` health event as a text log; view column `carried_from` | 2 |

Total 35.5 h (rev 1: 31 h; plan: 26 h). Changes against the plan:

| Change | Why | h |
|---|---|---|
| W3-3 dropped | `bench/gate/metrics.py` reads no outcome today; nothing to skip. The duty moves to W4 (below) and to test D-10b | -1 |
| W3-2 edits `tune/cli.py`, not `tune/fit.py` | `_v3` is the only tune outcome reader | 0 |
| W3-0 added | The flag-off golden must exist before the first edit; `tests/integration/**` is T7's | +2 |
| W3-4b added | Demo v3 and the two-session test need a real-shaped key through `sae replay` | +2 |
| W3-6 added | Telemetry and the console view would show void rewards as 0 and hide the carry event | +2 |
| W3-A0 4 -> 5.5 | C6 line, detail grammar helpers with `prior`, `CarryRefusal`, reader contract test | +1.5 |
| W3-1b 4 -> 5.5; file set grows | Void mark in `engine_tick.py`; `window_void` in `state.py`; decay helper in `belief.py`; rules A and B; engine golden | +1.5 |
| W3-1 10 -> 10.5; file set changes | `daemon/loop.py` is required (the plan omitted it); `statefile.py`, `cli.py`, `wiring.py`, `__init__.py` need no edit; epoch gate, mode filter, flag-off resume | +0.5 |
| W3-5 1 -> 2 | Records 5 new assumptions; rollback note in the user guide; demo statements | +1 |

Rev 1 -> rev 2 (review): W3-A0 +0.5, W3-0 +0.5, W3-1b +1, W3-1 +1.5, W3-5 +0.5, W3-6 +0.5 = +4.5 h.

Amendment to `IMPLEMENTATION_WAVES.md` Wave 4 rows (review B6; that file is not edited by this design, its owner
applies it): W4-1 and W4-3 each gain "every new reader of an outcome `reward` calls `outcome_is_void`; test
D-10b stays green".

### 9.1 Reacting synth (W3-4; Wave 4 depends on it, B13)

```python
SynthAdapter(..., effect: Mapping[ReasonKey, float] | None = None)
```

1. `effect[key]` = extra fractional fleet growth per month for a managed AI whose applied `SimCountry.reason` is
   that key. The reason code is the only per-strategy signal in the simulated game: `outscale_siege` and
   `early_punish` share (`military`, `offensive`) and differ only by `reason_key`.
2. State: `self._bonus: list[float]` (one per AI, start 1.0). Each month, before the fleet line:
   `self._bonus[k] *= 1 + effect.get(reason_key_of(ai.reason), 0.0)` for managed AIs.
   `ai.fleet = round(self._ai_fleet[k] * (1 + 0.01 * i) * self._bonus[k], 1)`.
3. Default `None`: bonus stays exactly 1.0, `x * 1.0 == x`, so the run is byte-equal to today.
4. Nothing else changes: naval cap stays `fleet / 80` (so `fleet_share` stays flat; W4-1 reads raw fleet power);
   the player's fleet keeps tracking the AI average. No CLI flag, no C8 change; tests and W4 pass the kwarg.
5. Sizing: `{ReasonKey.TURTLE_SIEGE: 0.004}` gives +10.05 % over a 24-month window.

## 10. UNVERIFIED assumptions and the game-PC run (W3-5)

Ids are provisional (A-49 is the last row in `contracts/README.md` §5 today); 036 assigns final numbers.

| Id | Assumption | Design depends on it for | If false |
|---|---|---|---|
| A-46 | One folder per campaign; a folder name is reused only by a same-name new game | identity | wrong carry; keep the flag off |
| A-50 | At session start the first parsed save arrives within 12 game months of the first HB (autosave interval setting) | hold resolves by key | every session ends `no_key`; carry never happens |
| A-51 | `player_sid` is stable across sessions of one campaign (stored in the save). It is also almost always the same value in every single-player game, so it separates nothing in practice | rule 2 | every session refused `player_sid` |
| A-52 | Opening <= 64 `state.json` files takes < 100 ms on Windows with Defender | §8 | lower `CARRY_SCAN_MAX` or add an index |
| A-53 | Applied intents (econ flag, posture modifier) persist in the save across a quit and load | "no batch before keyed" is harmless | the hold leaves AIs at baseline until resolve |
| A-54 | Every load of a save produces a HELLO (epoch bump) before any HB | regression and switch detection (the B1 epoch gate depends on it) | in-run campaign switch missed and reported as a foreign save |

Game-PC protocol (flag on via config, recording on, one empire name "W3 Test"):

1. Session 1: new game, play 5 game years, quit. Session 2: load the latest autosave, play 2 years, quit.
2. Session 3: load a save 1 year older than session 2's end. Session 4: 3 years older. Session 5: 10 years older.
3. New game, same empire name, started while the first campaign's last date is (a) under 2 years and (b) over
   2 years ahead of 2200.01.
4. Record per session: the `carry` health line; which came first, HB or parsed save; game months from the first
   HB to the first keyed observation (= how long knob-only batches were blocked); `player_sid`; the save folder
   names on disk; scan wall time; whether the managed AIs still showed the applied plan before the first batch; the
   share of outcomes that are void (a normal "continue" loads an autosave older than the quit date, so D of 1-12
   and a void carried window are expected); any `invalid_event` "foreign save key".
5. Pass to enable the flag (plan §5 "Turn `engine.carry_state` on"): session 2 `carried_from` session 1 with the
   same strategy; session 3 carried with `regress_months=12`; session 4 `carry_refused=regressed`; session 5
   `carry_refused=split`; step 3 (b) refused; step 3 (a) recorded as the known hole (§13 item 1).
6. Keep the five recordings (they now hold `folder_key`) as the real live-shape fixture replacing the synth one.
   They contain full saves: keep them out of git, or trim them first.

### 10.1 Demo v3 statements (review B8; say all six)

1. `engine.carry_state` is off by default. What ships to everyone in this wave is only the reload fix (W3-1b).
2. Campaign identity rests on A-46, which is UNVERIFIED until the game-PC run.
3. `player_sid` is almost always the same value in single player, so identity is in practice the folder key plus
   the game date.
4. Reload fairness is a decay bound, not exact: a posterior of 0.95 is still 0.68 after 24 abandoned months
   (`0.25 + 0.98^24 * 0.70`).
5. Carry is state persistence, not learning. The engine remembers its belief and commitment; it does not get
   better at reading the player.
6. Every result shown is a replay of one synth recording. Nothing has run in the game yet. The hold can delay the
   first reaction by up to 12 months when no save is ever keyed.

## 11. Deliberately left out

| Left out | Why | Add when |
|---|---|---|
| Exact campaign identity | Follow-up proposal, maintainer-approved as a follow-up. It must compare two sources, both UNVERIFIED: a mod nonce in HELLO (C1, T1, needs a mod release) and a save-side galaxy id read by the parser (T3, no mod release, works under Ironman) | after M0-11 data |
| Refuse when D >= 1 and the loaded date is within 12 months of the campaign's first date (review note 7) | Narrows the known hole but the maintainer accepted the hole as is; a legal reload there loses little | game-PC step 3 (a) shows the hole is hit |
| Exact posterior restore from stored `(mi, posterior)` points (review note 9) | Replaces the decay bound approved in C-review B3; needs the maintainer | the 0.68 residual is judged too high |
| Filtering a foreign save's evidence out of the belief | The assembler (T2) already replaced `_save`; B1 only protects identity and the slice | game-PC records foreign-save events |
| Hold after an in-session reload | Costs reaction after every reload; the wrong-campaign ticks are at most one autosave interval and are today's behaviour | game-PC shows campaign switches without a daemon restart |
| Per-key carry index or `runs/carry/` file | Scan of 64 dirs is cheap; no new privacy surface | A-52 fails |
| Game-version check in identity | Not in B1; policy pin already reports mismatches | a patch changes feature meaning |
| Carrying an operator force when the window is dropped | Operator can re-issue; avoids a half-carried strategy state | requested |
| `carry` in `sae ctl status` (C9) | The bus event is the audit; no C9 change | operators ask |
| Outcome for the window dropped on a mid-run campaign switch or policy change | It belongs to another campaign or policy; an absent row is safer than a void row with the wrong ids | tune needs the count |
| Ident written while the flag is off | Would break flag-off byte equality | never; one session of warm-up is acceptable |
| Config key for the 12-tick cap or the 64-run window | Constants until a user needs them | - |

## 12. Test list

Unit, T4 `test_regress.py` (W3-1b):

| Id | Test |
|---|---|
| R-1 | In-session reload 30 years earlier (C-review validation 3): posterior exactly uniform; an `outcome` on the same tick with `metrics["void"] == 1.0`, `reward == 0.0`; next window is `below_threshold`, `abstain=True` |
| G-1 | Engine golden, captured at step 0 on the merged Wave 1-2 commit (review B7): a hand-built observation sequence through `engine.on_observation` with a same-month HELLO (d = 0), a pause and resume, a force and clear-force, and a normal window close. Stored: every draft and the final state in `tests/unit/t4/golden/w3_engine_pre.json`. After W3-1b the same sequence must reproduce it (state compared after popping `carry`, `window_void`) |
| R-2 | D = 6, window opened before the load point: posterior == `1/4 + 0.98**6 * (p - 1/4)` per archetype (abs 1e-12); `window_open is None`; `window_void`; `commit_until` = load month + remaining months; when the window closes the outcome is void |
| R-2b | Review B3, in-session: counter window opened at 2211.01 (D1 pre-emption), reload to 2210.01 (D = 13). Assert: an `outcome` on the reload tick, void; `strategy_selected` re-decided from the decayed posterior (reason `table` or `below_threshold`, never `commitment_hold`); the old `commit_until` is gone |
| R-3 | D = 24 decays, D = 25 is uniform (boundary) |
| R-4 | D = 0 (same-month reload, `DATE_REGRESSED` set): drafts and state equal the G-1 golden for that step; no void |
| R-5 | Operator force survives D = 6 and D = 30 |
| R-6 | Void flag clears when the next window opens; that window's outcome is not void |
| R-6b | Review B4: two regressions (D = 3, then D = 2) inside one window. `window_void` stays `True` after the second; the closing outcome is void |
| R-7 | Property: for any posterior and D >= 0, `forget` output sums to 1, max entry is non-increasing in D |
| R-8 | A pre-W3 `state.json` (no `carry`, no `window_void`) loads and round-trips |

Unit, T4 `test_carry.py` (pure engine, hand-built observations):

| Id | Test |
|---|---|
| C-1 | Flag off: `initial_state().carry is None`; 24 ticks emit no `carry` event |
| C-2 | Hold: 5 unkeyed ticks -> 5 `belief_update`, 0 `strategy_selected`, 0 batches, `hold_ticks == 5` |
| C-3 | Hold cap: tick 13 unkeyed -> exactly one `carry_refused=no_key`; `strategy_selected` on that tick |
| C-4 | Force during hold -> `carry_refused=operator_force`; forced strategy selected on the next tick |
| C-5 | Row 6: session-2 strategy == session-1 last strategy; `commit_until` equal in absolute months; `carried_from` == prior run id; parts `belief,features,window` |
| C-6 | Negative, split: prior ends 2270, obs 2200.01 -> fresh, `carry_refused=split` |
| C-7 | Negative, new game: prior ends 2204.06, obs 2200.01 (C-review validation 2) -> fresh, `carry_refused=regressed` |
| C-8 | Negative, `player_sid` differs, and `player_sid=None` -> fresh, `carry_refused=player_sid` |
| C-9 | Policy sha differs: belief and features equal the prior's, `strategy == StrategyState()`, `window_open is None`, detail has both `carried_from` and `carry_refused=policy_changed` |
| C-10 | Carried regression D = 6: decay as R-2, window outcome void |
| C-10b | Review B3, carried: prior opened a counter at 2211.01 and ended 2211.06; session 2 loads 2210.06 (D = 12). Carried (`parts=belief,features,window`), but the first tick closes the window void and re-decides |
| C-11 | Gap of 8 months, window still open: `commit_until` unchanged (not shifted by 8). Gap past `commit_until`: first tick closes it with a non-void outcome |
| C-12 | Carried abstention window keeps `abstain=True`; a confident turtle tick pre-empts it (B10) |
| C-13 | Merge whitelist: for every name in `EngineState.model_fields` outside `CARRIED_FIELDS + ("carry",)`, the value after `resolve` equals the value before. A new field fails this test until classified |
| C-14 | `observe_only` ctx with carried state: no batch, intents audited `mode` / `ironman` (carry cannot clear it) |
| C-15 | Campaign switch mid-run: **epoch bump**, then a keyed observation with a new key -> slice reset, one `carry` event, `ident.key` is the new key, `ident_epoch` is the new epoch |
| C-15b | Review B1: same epoch, a keyed observation with a different key -> features, belief, strategy and ident unchanged; no `carry` event; exactly one `invalid_event` "foreign save key" over 5 such observations; the key flipping back changes nothing |
| C-15c | Epoch bump, then the **same** key: no lookup, no event, `ident_epoch` updated |
| C-16 | `fresh` by `no_key`, key arrives at tick 20: ident filled, no event, strategy state untouched |
| C-17 | Exactly one `carry` event per run in C-5..C-9 |
| C-18 | Same save seen at the end of session 1 and the start of session 2 (`legal_as_of` equal): no second evidence step |

Unit, T4 `test_carrystore.py`:

| Id | Test |
|---|---|
| S-1 | Newest matching run wins; own run id excluded; non-run-id dir names ignored |
| S-2 | Corrupt, missing and version-2 `state.json` are skipped and counted; an older valid match is returned |
| S-3 | 100 same-mode run dirs: at most 64 opened (count via a spy); the 65th-newest match is not found. 100 `-synth-` dirs in front of one `-live-` match: a `-live-` run still finds it |
| S-5 | Review B2: a newer `-replay-` run with the same key is ignored by a `-live-` run, which gets the older `-live-` prior |
| S-6 | A matching state whose posterior has 3 entries is skipped and counted, never returned |
| S-4 | A `state.json` with `carry=null` (flag-off run) never matches |
| P-1 | `state.json` with a full `carry` block at 16 managed AIs is < 64 KB |
| P-2 | 10 chained carried sessions: `state.json` size of session 10 within 5 % of session 2 |

Daemon and adapters:

| Id | Owner | Test |
|---|---|---|
| D-1 | T4 `test_carry_sessions.py` | C-review validation 1, log-first: one synth recording with `folder_key` = `folder_key(FIXED_SALT, "W3 Test")`; run A = replay `stop_at=2203.01.01`, run B = replay `from_=2203.02.01`, flag on. B: no `intent_emitted` before the first keyed observation; one `carried_from=<A>`; first selected strategy == A's last; `commit_until` == A's |
| D-2 | T4 same file | Save-first order with a scripted fake adapter (first observation keyed): resolve happens before the first tick; same assertions |
| D-3 | T4 same file | `observe_only` adapter + carried state: zero emits |
| D-4 | T4 same file | Re-exec mid-hold (`--resume` path): `hold_ticks` continues, one `carry` event in total |
| D-4b | T4 same file | Review B7: a mid-hold `state.json` resumed with `carry_state=false` -> `state.carry is None`, zero `carry` events, `select` runs on the first tick |
| D-5 | T2 `test_replay_folder_key.py` | File present -> observations carry that key after the first parse and `None` before; reset to `None` by a HELLO; absent -> `f-synth-`; invalid text -> `f-synth-` + `invalid_event` |
| D-6 | T2 `test_assembler.py` | Real path: `salt_fn` fixed + `folder="W3 Test"` -> key == `folder_key(salt, "W3 Test")`, equal across two assembler instances |
| D-7 | T2 `test_synth_effect.py` | `effect=None` run equals the W3-0 golden; `{TURTLE_SIEGE: 0.004}` -> in-contact managed AI fleet at the end of the first counter window >= 1.10 x the no-effect run, same seed |
| D-8 | T7 `test_w3_flag_off.py` | Flag off, 6-year synth: normalised bus sha, per-event-type counts and `state.json` equal the golden; the golden names the merged Wave 1-2 commit (B10, review B7) |
| D-9 | T7 `test_ci4_imports.py` (unchanged) | Green. `carrystore` and `c10_profile` import only contracts and `statefile` |
| D-10 | architect `tests/contract` | `split_candidate` equals `profile/index.py` behaviour on its fixtures at 59, 60, 61 months; `format_carry` / `parse_carry` round-trip every doc example, `prior` included, key order fixed; `EngineConfig().carry_state is False`; C5/C8/C10 doc examples validate |
| D-10b | architect `tests/contract` | Review B6: scan every file under `src/sae` and `tools/console` except `core/outcome.py` and `contracts/`. A file whose text has both `outcome` and `reward` must also have `outcome_is_void` or `void`. Non-vacuity: the scan finds at least `tune/cli.py`, `telemetry/mapping.py`, `tools/console/views.sql` |
| D-11 | T9 | Index fixtures unchanged after the import swap; `_v3` over a bus with 2 real + 1 void outcome reports n=2 and "1 void skipped" |
| D-12 | T6 | Void outcome yields no `outcome/reward` scalar; `carry` event mapped; view returns `carried_from`. Review B5: on a synth bus with one void and one real outcome the audit view gives `outcome_reward IS NULL, outcome_void = true` for the void row and the reward with `outcome_void = false` for the real one |

Game PC (skipped by the `game` marker, run from `docs/handoff/GAME_PC_AGENT.md`): §10 steps 1-6 (C-review
validation 4).

## 13. Self-adversarial pass: top 5 ways this still goes wrong

| # | Failure | Caught by | Residual |
|---|---|---|---|
| 1 | Wrong identity: a same-name new game whose first keyed date is within 24 months before, or any time after, the old campaign's last date is carried in full (belief + a counter commitment from another game) | C-7 covers only D > 24. Game-PC step 3 (a) measures it. `carried_from` makes it visible | **Open.** No test can catch it: the inputs are identical to a legal reload. Bounded by forgetting (`rho`) and the 24-month window; flag stays off until M0-11; exact fix is the mod nonce (§11) |
| 2 | Merge corrupts the live run: a future `EngineState` field (or `lifecycle`, `next_intent_id`) is copied from the prior and the mod sees a reused seq | C-13 fails closed on any unclassified field; D-1 checks seq continuity through the bus checker | none known |
| 3 | Cross-timeline rewards reach tune: a path sets `window_open` from another timeline without `window_void` (carried regression, deep regression, second regression, future refactor of `_reset`), or a reader scores void as 0 | R-1, R-2, R-6, R-6b, C-10; D-11, D-12 and D-10b prove the readers skip | A forward load of another branch is not detectable (§6 item 5) |
| 4 | Hold starves the game: phase stuck in `hold` (key never arrives, counter not persisted over re-exec, reloads resetting the cap), so no batch is ever sent | C-3 (cap by ticks), C-4, D-4, C-16 | If A-50 is false every session waits 12 months; game-PC step 4 measures it before the flag is enabled |
| 5 | Flag-off drift: W3-1b or W3-1 changes default behaviour, `state.json` bytes or the daemon import graph | D-8 golden on the merged Wave 1-2 commit, G-1 engine golden (reload d = 0, pause, force, window close), R-4, R-8, D-4b, D-9 | W3-1b is an intended unflagged change on a regression with D >= 1; neither golden covers that on purpose. R-1..R-6b carry that weight |
| 6 | Found by the gate: state from another campaign or an abandoned branch acts on the live game: a foreign save flips the key (B1), a non-live run becomes the prior (B2), a window opened after the load point survives (B3) | C-15, C-15b, C-15c; S-3, S-5; R-2b, C-10b | A foreign save that is the first keyed observation of an epoch is still taken as the campaign. Its evidence enters the belief in every case (assembler, T2). Game-PC step 4 counts foreign-save events |

Also checked, no change needed: a stale `state.json.tmp` (never read); two daemons (CURRENT lock); a prior that
is itself mid-hold (its `ident.key` is `None`, never matches); the same save parsed in both sessions (C-18).

## 14. Maintainer decisions (adopted 2026-11-15)

| # | Decision | Where |
|---|---|---|
| 1 | Accept the same-name new-game hole behind the default-off flag; exact identity is a follow-up proposal | §11, §13 item 1 |
| 2 | Refuse a carry-time regression of 25-60 months outright | §4.1 row 4 |
| 3 | Keep W3-6 (telemetry) in Wave 3 | §9 |

No open question remains for the maintainer. Two review notes need one if they are ever wanted: note 7 (narrow
the hole) and note 9 (exact posterior restore), both in §11.

## 15. Review changes (gate `docs/status/opt_w3_design_review.md`)

| Item | Change | Sections |
|---|---|---|
| B1 | `ident_epoch` (+ `foreign_epoch`) in `CarryState`; campaign switch only when `session_epoch` changed; same epoch -> one `invalid_event`; tests C-15, C-15b, C-15c | §2.2, §4 table and rule 6, §12, §13 item 6 |
| B2 | `find_prior` filters by run mode before the 64 cut; tests S-3, S-5 | §0, §2.3, §4.2, §8, §12 |
| B3 | Window opened after the load point gets `reopen=True`, in-session and at carry; tests R-2b, C-10b | §3 rule A, §4.1 merge 3, §5, §6, §12 |
| B4 | Sticky `window_void`, cleared only where a new window opens; test R-6b | §3 rule B, §12 |
| B5 | `views.sql:85` is a third reward reader: NULL when void, column `outcome_void`; D-12; W3-6 grows | §1, §7, §9, §12 |
| B6 | Reader contract test D-10b; duty added to W4-1 / W4-3 (amendment text, §9); `format_carry` gains `prior`, key order fixed, `CarryDecision.prior` | §2.1, §7, §9, §12 |
| B7 | Golden only on the merged Wave 1-2 commit, with hash and event counts; engine golden G-1 before any edit, R-4 compares to it; resume with the flag off sets `carry=None` (D-4b) | §4 rule 3, §9 (W3-0, W3-1b), §12 |
| B8 | Six Demo v3 statements | §10.1 |
| Note 1 | Daemon always fills `policy_sha256` (built-in sha, never `None`) | §2.2 |
| Note 2 | Prior with a wrong posterior length is skipped; test S-6 | §4.2, §12 |
| Note 3 | Downgrade behaviour and rollback note | §7, W3-5 |
| Notes 4, 5, 10 | Void share, knob-only block and recordings-out-of-git in the game-PC protocol | §4 rule 7, §10 |
| Note 11 | Vacuous clause dropped from D-9 | §12 |
| Notes 6, 7, 8, 9 | Not adopted; recorded with the trigger for each | §11 |

## 16. Closure-check edits (rev 2.1, gate GO 2026-11-16)

These override the matching text above (`docs/status/opt_w3_design_review.md`, closure check).

1. **E1, D-10b ships xfail.** W3-A0 lands D-10b as `pytest.mark.xfail(strict=True, reason="until W3-2 and W3-6")`.
   Today it trips on `src/sae/telemetry/mapping.py`, `src/sae/tune/cli.py` and `tools/console/views.sql`. The
   architect removes the marker at wave integration, after W3-2 and W3-6 land.
2. **E2, golden capture.**
   - The coordinator supplies the merged Wave 1-2 commit hash **and** states that the tree is clean at capture.
   - W3-1b step 0 reads "on the merged commit plus W3-A0, before any T4 source edit".
   - The W3-0 normaliser pops `carry` and `window_void` with a default, since neither key exists at capture.
3. **B1 residual (W3-5, text only).** Once a foreign key is taken first in an epoch, the real campaign's key is
   ignored for the rest of that epoch and `state.json` is saved under the foreign ident. §13 item 6 says this.
   §10 step 5 adds: "zero `foreign save key` events at the game-PC run, else `engine.carry_state` stays off".
   Not blocking for code; blocking for turning the flag on.
4. **`foreign_epoch`:** accepted (one int inside `carry`).
