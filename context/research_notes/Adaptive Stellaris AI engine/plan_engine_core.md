# Plan: Game-Agnostic Adaptive Engine Core

Date: 2026-10-18. Scope: the engine core (belief, strategy selection, optional LLM advisor, learning, audit) that sits behind a game adapter. Stellaris is the first adapter.

**Labels.** `[EV: note]` means the choice rests on evidence in a research note. `[HYP]` means it is a design hypothesis we have to test. Note keys:
- AO = `adaptive_opponents.md`
- FGA = `frontier_game_agents.md`
- FM = `frameworks_and_models.md`
- SI = `stellaris_integration.md`

No new web research was done for this plan.

**Design summary.** The core is a small CPU-only Python process with four layers:
1. A Bayesian archetype belief computed from fog-of-war-visible features (Synnaeve/BPR style).
2. A contextual Thompson-sampling bandit that picks among parameterised counter-strategies. It has a safety floor, which is a StarNet-like baseline, and fixed commitment periods.
3. An optional, gated local LLM that may only *propose* within the strategy library.
4. A validator plus audit log in front of every emitted Intent.

Learning across campaigns happens only offline, and only through versioned, evaluated policy releases.

---

## 1. Package layout and core interfaces

```
adaptive_core/                # game-agnostic, no game imports
  agent_core/
    types.py                  # pydantic models: Observation, Intent, BeliefState, DecisionAuditRecord
    protocols.py              # Protocols: GameAdapter, OpponentModel, StrategyLibrary, Selector, Advisor, Validator, MemoryStore
    tick.py                   # the tick loop (section 6)
    planner.py                # strategy -> per-tick Intents (deterministic expansion)
  opponent/
    features.py               # FeatureSpec registry, fog-of-war masking
    archetypes.py             # archetype library (params loaded from versioned YAML/JSON)
    belief.py                 # Bayesian filter, forgetting, change detection
    calibration.py            # temperature/tempering fit, reliability diagrams
  selection/
    library.py                # parameterised strategy templates + versioned param sets
    bandit.py                 # contextual Thompson sampling (Bayesian linear per arm)
    safety.py                 # safe-exploitation floor, commitment/hysteresis
    reward.py                 # reward shaping (relative power, war outcomes)
  advisor/                    # optional; import-guarded
    gate.py                   # novelty/uncertainty triggers, rate limits
    llm_client.py             # llama.cpp / Ollama backends, JSON-schema constrained
    prompts.py
  learning/
    profiles.py               # per-player persistent profile (priors)
    offline_search.py         # CMA-ES / Nevergrad over strategy params
    registry.py               # policy versions, promotion gates
  telemetry/
    audit.py                  # DecisionAuditRecord JSONL writer
    metrics.py                # calibration, regret, flip rate
    episodes.py               # Minari-compatible episode export
  memory/
    store.py                  # SQLite-backed MemoryStore
  experiments/
    conditions.py             # A/B/C configs
    runner.py                 # AI-vs-AI observer runs, seeds, reporting
adapters/
  stellaris/
    observe.py                # game.log tail + save parse -> Observation
    features.py               # Stellaris FeatureSpecs (chokepoints, fleet concentration...)
    intents.py                # Intent -> mod command file (`run` console file) [SI]
    legality.py               # Stellaris-specific Validator rules
    baseline.py               # StarNet-like / vanilla baseline strategy mapping
```

The engine core never imports from `adapters/`. A game adapter has to supply observations, feature extraction, the intent vocabulary and its legality rules, and a baseline. `[HYP]`

There are two observation channels: a mod-written `game.log`, read monthly, and save parsing, read at each autosave. Save parsing is the only route the community verifiably uses. Its latency is unverified. `[EV: SI]`

```python
# agent_core/types.py
from pydantic import BaseModel, Field
from typing import Literal, Annotated, Union

class Observation(BaseModel):
    game: str; game_version: str; campaign_id: str
    t: int                                   # game month index
    self_state: dict[str, float]             # own empire: fully known
    visible: dict[str, dict[str, float]]     # per opponent id: ONLY fog-of-war-visible facts
    visible_mask: dict[str, dict[str, bool]] # which raw facts were actually observed this tick
    events: list[str] = []                   # war declared, peace, first contact...
    source: Literal["log", "save", "merged"]

class BeliefState(BaseModel):
    opponent_id: str
    probs: dict[str, float]                  # archetype -> P
    entropy: float
    changepoint_prob: float                  # P(run length reset)
    phase: Literal["early", "mid", "late"]
    feature_snapshot: dict[str, float | None]

class SetEconPlan(BaseModel):
    kind: Literal["SET_ECON_PLAN"] = "SET_ECON_PLAN"
    plan: Literal["balanced", "military_industry", "research", "expansion"]
class SetMilitaryPosture(BaseModel):
    kind: Literal["SET_MILITARY_POSTURE"] = "SET_MILITARY_POSTURE"
    posture: Literal["defensive", "deterrent", "offensive"]
    target_id: str | None = None
class PrioritizeTech(BaseModel):
    kind: Literal["PRIORITIZE_TECH"] = "PRIORITIZE_TECH"
    category: Literal["physics", "society", "engineering"]
    weight: float = Field(ge=0.5, le=2.0)       # bounded multiplier, not a raw script value
class FleetCompositionBias(BaseModel):
    kind: Literal["FLEET_COMPOSITION_BIAS"] = "FLEET_COMPOSITION_BIAS"
    role_weights: dict[Literal["artillery","torpedo","carrier","picket","brawler"], float]
class DiploStance(BaseModel):
    kind: Literal["DIPLO_STANCE"] = "DIPLO_STANCE"
    target_id: str
    stance: Literal["friendly","neutral","rival","hostile"]

Intent = Annotated[Union[SetEconPlan, SetMilitaryPosture, PrioritizeTech,
                         FleetCompositionBias, DiploStance], Field(discriminator="kind")]

class DecisionAuditRecord(BaseModel):
    t: int; campaign_id: str; policy_version: str
    belief: list[BeliefState]
    arm_scores: dict[str, float]             # posterior samples / means
    chosen_strategy: str; params: dict[str, float]
    committed_until: int; switch_reason: str | None
    advisor_called: bool; advisor_trigger: str | None; advisor_output_hash: str | None
    intents_proposed: list[Intent]; intents_emitted: list[Intent]
    rejections: list[tuple[str, str]]        # (intent kind, reason)
    latency_ms: dict[str, float]
    legible_reason: str                      # short, player-facing explanation
```

Intent fields are enums and bounded multipliers, never free-form script text. Grammar-constrained decoding guarantees an intent has the right shape. A validator is still needed to guarantee it is legal in the current state. `[EV: FM]`

```python
# agent_core/protocols.py
class OpponentModel(Protocol):
    def update(self, obs: Observation, prev: BeliefState | None) -> BeliefState: ...
    def predictive_loglik(self, obs: Observation) -> float: ...   # for surprise/novelty

class Strategy(Protocol):                 # parameterised template, e.g. "chokepoint_siege(θ)"
    name: str; param_space: dict[str, tuple[float, float]]
    def plan(self, obs: Observation, belief: list[BeliefState], θ: dict) -> list[Intent]: ...

class Selector(Protocol):
    def select(self, ctx: np.ndarray, t: int) -> tuple[str, dict, str]: ...  # arm, θ, reason
    def update(self, arm: str, ctx: np.ndarray, reward: float) -> None: ...

class Validator(Protocol):
    def validate(self, intents: list[Intent], obs: Observation) -> tuple[list[Intent], list[tuple[str,str]]]: ...

class Advisor(Protocol):
    async def propose(self, brief: "AdvisorBrief", deadline_s: float) -> "AdvisorProposal | None": ...

class MemoryStore(Protocol):
    def put_audit(self, rec: DecisionAuditRecord) -> None: ...
    def player_profile(self, player_key: str) -> "PlayerProfile": ...
    def outcomes(self, strategy: str, archetype: str) -> list[float]: ...   # Voyager-style skill stats
```

The strategy library plays the role of a Voyager-style skill library, but every skill is human-authored and verified. The memory store records outcome statistics for each (strategy, archetype) pair. It does not store LLM-written code. `[EV: FGA]` for the skill-library idea. Restricting it to authored skills is `[HYP]`.

---

## 2. Opponent model

### 2.1 Features (fog-of-war visible only)

The model may use only what a human player in the AI's seat could see. This follows the Alien: Isolation design principle that the AI "never cheats". `[EV: AO]` Each feature is computed per opponent per month. When the underlying facts are not visible, the feature is set to `None`; it is never imputed from hidden state.

| Feature | Definition (visible data only) | Archetypes it separates |
|---|---|---|
| `choke_fort` | Count of visible starbases with defense modules, plus defense platforms, at systems with border degree ≤2 on the opponent's frontier, normalised by frontier length | turtle |
| `fleet_conc` | Herfindahl index of visible fleet power across the systems where it was seen. High means a doomstack. | aggression, turtle |
| `fleet_near_border` | Share of visible fleet power within 2 jumps of our border | early aggression |
| `tech_pace` | Rate of change of a visible tech proxy: ship component tiers seen in combat or scans, plus intel-level tech estimate if the game exposes it | tech rush |
| `expansion_rate` | Change in owned systems per year, from visible borders | wide |
| `colony_rate` | New colonies per year where visible | wide, tall |
| `diplo_density` | Pacts, federation membership, and defensive pacts that are visible through contact | federation-builder |
| `naval_cap_ratio` | Estimated visible fleet power divided by our own | all (context) |
| `war_decl_timing` | Months until their first war or claim against anyone | early aggression |

**Phase discretisation.** The game splits into early, mid and late phases, each defined by a month range. Every likelihood is conditioned on phase, in the same way that Synnaeve & Bessière condition build timings on game time. `[EV: AO]`

### 2.2 Archetype library

The initial set is K = 7 archetypes:
- `turtle_chokepoint`
- `tech_rush`
- `early_aggression`
- `wide_expansion`
- `federation_builder`
- `tall_economy`
- `balanced` (a catch-all that absorbs out-of-library behaviour)

Each archetype a stores per-phase likelihood parameters for every feature: a mean μ and standard deviation σ for Gaussians, or p for binary or count features. These parameters are fit offline from observer runs in which scripted AIs play each archetype (§5), and players' confirmed histories can be added later. The archetype set itself is `[HYP]`. Using a precomputed, CPU-cheap Bayesian model fitted from game logs is `[EV: AO]`: Synnaeve & Bessière report about 0.01 s per inference and about 3 MB of memory.

### 2.3 Bayesian update

The filter is a forgetful Bayes filter over a discrete archetype variable, with a fixed-share transition. Let b_t(a) = P(A_t = a | x_{1:t}). Each month:

```
prior_t(a)  = (1 − ρ) · b_{t−1}(a) + ρ / K                       # fixed-share forgetting (nonstationarity)
lik_t(a)    = Π_{f ∈ visible(t)}  p(x_{t,f} | a, phase_t) ^ τ      # naive Bayes over OBSERVED features only
b_t(a)      ∝ prior_t(a) · lik_t(a)
```

- **Missing features drop out of the product.** This is the analogue of Synnaeve & Bessière's coherence variable and fog-of-war "seen" flags. `[EV: AO]`
- **τ ∈ (0, 1] is a tempering exponent.** Features are correlated, so naive Bayes is overconfident. τ is fitted during calibration (§2.5). `[HYP]`
- **ρ (about 0.01–0.03 per month) models switching.** It bakes in BPR+'s assumption that an opponent switches between stationary policies. `[EV: AO]` The value of ρ is `[HYP]`.
- **Each month gets one update with a capped likelihood ratio.** This is a hedge against fog-noise spikes: a newly revealed doomstack should not snap the belief to one archetype. `log lik` is clipped to [−L, L] per feature. `[HYP]`

```python
def update(self, obs, prev):
    b = (1 - self.rho) * prev.vec + self.rho / K
    for f, x in self.features(obs).items():
        if x is None: continue
        ll = np.clip(self.loglik[f](x, phase(obs.t)), -L, L)   # shape (K,)
        b = b * np.exp(self.tau * ll)
    b /= b.sum()
    cp = self.changepoint.step(self.predictive_loglik_vec(obs, prev.vec))
    if cp > CP_THRESH: b = 0.5 * b + 0.5 * self.profile_prior    # soft reset toward player prior
    return BeliefState(probs=dict(zip(ARCH, b)), changepoint_prob=cp, ...)
```

### 2.4 Change detection

The filter tracks the predictive surprise s_t = −log Σ_a prior_t(a)·lik_t(a) and feeds it to a one-sided CUSUM:

```
g_t = max(0, g_{t−1} + s_t − μ_s − k)
```

An alarm fires when g_t > h. Here μ_s is the running surprise mean.

When the alarm fires:
1. Blend the belief back toward the player prior.
2. Mark a "commitment-break eligible" flag for the selector (§3.3).
3. Make the LLM gate eligible (§4).

CUSUM is used because it is O(1) and transparent. Bayesian online changepoint detection is the upgrade path. `[HYP]` The need to detect changes at all comes from BPR+ and Deep BPR's treatment of non-stationary opponents. `[EV: AO]`

### 2.5 Calibration

1. In observer runs, scripted opponents follow known archetypes, and some of them switch archetype mid-game.
2. Fit τ and ρ to minimise the multiclass Brier score of b_t against the ground-truth label at each month.
3. Report expected calibration error, a reliability diagram per phase, and detection delay in months after a scripted switch.

The promotion gate is `[HYP]`: ECE ≤ 0.10 and median detection delay ≤ 18 months. A belief that is badly calibrated is worse than having no belief, because the bandit context and the safety floor both consume its probabilities.

---

## 3. Counter-strategy selection

### 3.1 Library

Each strategy template has 3–8 continuous parameters θ, and each template expands deterministically into Intents (`planner.py`). Initial templates:

| Template | Counters | Example θ |
|---|---|---|
| `baseline` | — (safety floor; maps to vanilla/StarNet-like behaviour) | none |
| `siege_breaker` | turtle_chokepoint | artillery/torpedo weight, timing of first strike, engineering tech weight |
| `outscale_econ` | turtle, tall | research weight, expansion weight, defensive posture threshold |
| `early_punish` | tech_rush, wide | aggression start month, fleet commit fraction |
| `hold_and_counterpunch` | early_aggression | border defense share, counterattack power ratio |
| `contest_space` | wide_expansion | outpost race weight, claims priority |
| `wedge_diplomacy` | federation_builder | rival target, pact-seeking with fed outsiders |

Building the strategic layer as a small set of parameterised macro choices, with deterministic execution underneath, follows SwarmBrain's split: LLM or strategic macro on top, scripted micro below. That design reached a 76% win rate against Hard built-in AI. `[EV: FGA]` The template list itself is `[HYP]`.

### 3.2 Contextual Thompson sampling with a safe floor

**Arms.** Each arm is a (template, θ-version) pair. Within one policy version, θ is fixed at live time; θ only changes through offline search (§5). Keeping arms discrete means the bandit chooses among roughly 10–20 arms.

**Context.** The context vector is

```
c_t = [b_t (K), entropy, phase one-hot, naval_cap_ratio, econ_ratio, at_war, 1]
```

**Model.** Each arm has its own Bayesian linear regression on reward r, giving Thompson sampling with Normal-Inverse-Gamma posteriors. `[EV: AO]` names Thompson/UCB contextual bandits as the default choice and EXP3 as the adversarial fallback. The notes mark these foundations as not re-verified, so this choice is backed by the literature only at "background" level.

```python
def select(self, c, t):
    if t < self.committed_until and not self.break_eligible: return self.current
    base = self.arms["baseline"]
    samples = {a: arm.sample_w() @ c for a, arm in self.arms.items()}
    # safe floor: an arm is eligible only if its pessimistic advantage over baseline ≥ −δ
    elig = [a for a, arm in self.arms.items()
            if arm.lcb(c, q=0.1) - base.mean(c) >= -self.delta
            and self.budget.allows(a)]
    if self.max_belief(c) < 0.45: elig = [a for a in elig if a in self.robust_arms]  # low-confidence -> robust only
    best = max(elig or ["baseline"], key=samples.get)
    if best != self.current and samples[best] - samples[self.current] < self.hysteresis:
        best = self.current
    self.commit(best, t + self.min_commit[best])
    return best, self.arms[best].theta, self.reason(best, c)
```

The safe floor is restricted-Nash-like in spirit: deviate from the baseline only when there is confidence the deviation is not harmful. On top of that sits a conservative exploration budget: the cumulative estimated reward must stay at or above (1 − α) times the baseline's cumulative reward. The idea of limiting deviation from the baseline and gating on confidence is `[EV: AO]` (Ganzfried safe equilibrium; CS-RNR, which is a preprint). The specific LCB form is `[HYP]`. Full game-theoretic solving is not feasible for Stellaris. `[EV: AO]`

### 3.3 Commitment

- **Minimum commitment.** Hold each choice for 24 game months, or 36 for war-plan templates. `[HYP]`
- **Early break conditions.** A commitment can end early only on one of:
  - a CUSUM alarm
  - a war declared on us
  - the safety floor being violated: realised reward falls below baseline − 2δ over 6 months
- **Hysteresis margin.** A switch also needs to clear a hysteresis margin.
- **Telegraphing.** Every switch emits a `legible_reason`, e.g. "Detected fortified chokepoints; shifting to siege doctrine". The mod can surface it as flavour text.

Both the hysteresis and the telegraphing respond to the same evidence. Hamlet warns that reactive changes "seem erratic". Legible, paced adaptation is what makes it feel smart, and constant exploration feels random. `[EV: AO]`

### 3.4 Reward

The reward r is computed per commitment window [t0, t1], using only data the AI legitimately has: its own state plus fog-of-war-visible opponent estimates.

```
r = w1 · Δ log(P_self / P̂_opp)            # relative power; P = fleet power + λ·econ + μ·tech proxy
  + w2 · (systems_gained − systems_lost) / N
  + w3 · war_score_outcome ∈ {−1, 0, +1}    # only if a war ended in-window
  − w4 · exploit_penalty
r = clip(r, −1, 1)
```

**Exploit resistance.** All of this is `[HYP]`.
1. Reward depends on relative power, not on player actions that could be farmed. One example of farming: the player repeatedly baits a war, then makes peace.
2. War-outcome credit is capped at one war per window.
3. `exploit_penalty` fires when the same arm repeatedly loses in the same sequence pattern. That pattern is cheap to detect from the audit log.
4. No unlock or counter is ever triggered by a player failure. This follows Alien: Isolation's rule of excluding death-triggered unlocks. `[EV: AO]`

**Offline evaluation reward.** Experiments use the full save, which gives true power. The live learner never sees it. This keeps live play fair while evaluation stays accurate. `[HYP]`

---

## 4. Optional LLM strategic advisor

**Role.** The advisor is a rarely-called consultant. It is not the decision layer.

CivBench shows frontier LLMs as sole decision-makers winning 3 of 23 runs. The failures it identifies are:
- under-monitoring state
- a reflection–action gap
- $31–229 of API cost and 2–8 hours of wall-clock time per game

`[EV: FGA]` The engine therefore owns monitoring and triggers, and the LLM's output becomes a bounded proposal that the deterministic layer executes. `[EV: FGA]`

**Gate.** The advisor is called only if at least one trigger fires, and calls are rate-limited to at most one per 12 game months. Triggers:
- a CUSUM alarm
- belief entropy above H* for 6 or more months, i.e. the opponent is out of library
- predictive log-likelihood below the 1st percentile of the calibration runs (novelty)
- a war-declared event
- the end of a commitment window while the top-2 arm samples are within ε of each other

All gate values are `[HYP]`.

**What the advisor may do.** It may return a `AdvisorProposal`, which contains:
- `archetype_hint`: a distribution over the existing archetypes, or `"novel"` plus a description
- `recommended_arm`: chosen from the eligible arms only
- a bounded θ nudge of ±10% inside the arm's validated range
- a `legible_reason` of at most 140 characters

**What it may not do:**
- emit raw Intents or script commands
- choose an ineligible arm, which would bypass the safety floor
- change rewards or the library
- access hidden state; its brief is built from the same fog-masked features
- block the tick

```python
class AdvisorProposal(BaseModel):
    archetype_hint: dict[str, float] | Literal["novel"]
    recommended_arm: str
    theta_nudge: dict[str, float] = {}          # each |v| ≤ 0.10
    legible_reason: str = Field(max_length=140)

# advisor/llm_client.py — llama.cpp server: response_format=json_schema; Ollama: format=schema, temperature=0
proposal = await asyncio.wait_for(advisor.propose(brief, deadline_s=BUDGET), BUDGET)   # PydanticAI output_type=AdvisorProposal
proposal = post_validate(proposal, eligible_arms, theta_bounds)   # reject → audit + ignore
```

**How the proposal is used.** The selector treats the proposal as a prior bump on the recommended arm's sampled score, capped at +κ. It is not a command. `[HYP]`

**Stack.**
- Inference: llama.cpp with a JSON-schema-to-GBNF grammar, or Ollama with `format` set to the schema, temperature 0, and the schema repeated in the prompt. llama.cpp does not show the schema to the model, and Ollama does not document whether decoding is constrained, so PydanticAI's post-validation is mandatory. `[EV: FM]`
- Schema: keep it flat. llama.cpp's JSON-schema conversion has gaps for nested `$ref` and for `anyOf` combined with properties. `[EV: FM]`
- Models: Gemma 4 E4B (Apache-2.0) is the first candidate and Qwen3.5 4B is the second, at Q4. Both still need benchmarking on target hardware; VRAM and throughput are unverified. `[EV: FM]`
- Hardware: the advisor defaults to CPU or a small slice of GPU so it does not starve the game. `[HYP]`

**Latency budget.**
- Core tick (observe, belief, select, validate, emit): ≤ 50 ms of CPU. `[HYP]`; the Synnaeve-scale inference reference is `[EV: AO]`.
- The advisor runs asynchronously. It is launched at a trigger, and its proposal is accepted only if it arrives within D = 6 game months; at fast speed that is probably about 10–30 s of wall time, which is `[HYP]` and needs measurement.
- A late proposal is logged and discarded.
- SwarmBrain shows multi-second LLM inference is tolerable for a strategic layer. `[EV: FGA]`

**No-LLM fallback.** With the advisor off, the engine behaves exactly as Condition B or C. The `advisor/` package is import-guarded, and everything else must pass its tests without it.

---

## 5. Learning across campaigns

**Per-player profile** (SQLite, keyed by a local player hash). Contents:
- an archetype prior: a Dirichlet over the final beliefs of past campaigns, decayed with half-life of 5 campaigns
- per-(arm, archetype) reward summaries
- typical transition timings

The profile initialises b_0 and the bandit posteriors with a widened variance, so that stale knowledge cannot lock in a strategy. The profile stays local; nothing is uploaded. `[HYP]` (BPR-style priors over known types: `[EV: AO]`).

**Offline evolutionary search.**
1. For each template, run CMA-ES (pycma) or Nevergrad over θ.
2. Fitness is the mean evaluation reward over AI-vs-AI observer runs against scripted archetype opponents and the baseline, across seeds and maps, minus a variance penalty.
3. The search runs between sessions or overnight, never while the user is playing.

pycma is described as practical for tuning up to a few hundred continuous parameters on CPU, and Nevergrad as easy to use but less actively maintained. `[EV: FM]`

**What evolution may and may not do.** Strategies are human-authored templates with typed θ. Evolution tunes θ only. It does not generate new code. This is safer than Voyager's LLM-written skills for a game shipped to players. `[HYP]` (skill-library concept `[EV: FGA]`)

**Optional league layer.** Later, a PSRO-style meta-game over the arms × archetypes payoff matrix can be added using OpenSpiel. This works only on that abstraction, not on the full game. `[EV: FM]`

**Versioning and promotion.** A `PolicyVersion` contains:
- semver
- archetype params
- τ and ρ
- arm θ table
- bandit hyperparameters
- the game version it is pinned to (save formats change between versions `[EV: FM, SI]`)
- an evaluation report hash

A version is promoted only if all of the following hold:
- calibration gates pass (§2.5)
- win rate against the baseline does not fall below the incumbent's in more than 1 of 7 archetype matchups (paired seeds)
- no matchup loses more than 5 percentage points against the baseline
- exploit-probe scenarios pass, i.e. scripted bait-and-peace loops do not lower performance

**Live behaviour.** During a game, only the bandit posteriors and the beliefs update. Live updates never change θ, archetype likelihoods, or code. `[HYP]` (task constraint; also supported by Hamlet's spiral warning `[EV: AO]`)

Episodes are exported in Minari format so that offline RL (d3rlpy) stays possible later. `[EV: FM]`

---

## 6. Tick loop

```python
async def tick(obs_raw, S):                       # called once per game month
    t0 = now()
    obs = S.adapter.observe(obs_raw)              # log + latest save merge; fog mask applied in adapter
    if not S.adapter.healthy(obs):                # stale/parse fail -> keep last intents, audit, return
        return S.audit.write(stale_record(obs))

    # 1. belief (per opponent), engine-owned monitoring (anti "sensorium effect")
    beliefs = [S.opp.update(obs, S.beliefs.get(o)) for o in obs.visible]
    S.beliefs.update({b.opponent_id: b for b in beliefs})
    alarms = S.monitor.check(obs, beliefs)        # CUSUM, war events, safety-floor breach

    # 2. reward for an expiring/broken commitment
    if S.commit.ending(obs.t) or alarms.break_eligible:
        S.selector.update(S.commit.arm, S.commit.ctx, S.reward.window(S.commit, obs))

    # 3. collect advisor result if one is pending and on time
    hint = S.advisor_slot.poll(deadline_t=S.advisor_slot.issued_t + D_MONTHS)

    # 4. select / keep strategy
    ctx = S.context(obs, beliefs)
    arm, theta, reason = S.selector.select(ctx, obs.t, alarms=alarms, hint=hint)

    # 5. maybe launch advisor (non-blocking)
    if S.advisor and S.gate.should_call(obs, beliefs, alarms, S.selector):
        S.advisor_slot.launch(S.advisor.propose(S.brief(obs, beliefs), deadline_s=BUDGET), obs.t)

    # 6. plan -> validate -> emit (bounded change per tick)
    proposed = S.library[arm].plan(obs, beliefs, theta)
    proposed = S.rate_limiter.diff(proposed, S.last_emitted)   # only changed intents, max N/tick
    ok, rejected = S.validator.validate(proposed, obs)          # schema, ranges, legality, cooldowns
    S.adapter.emit(ok)                                          # mod applies via run-file channel [SI]

    S.audit.write(DecisionAuditRecord(t=obs.t, belief=beliefs, chosen_strategy=arm, params=theta,
        intents_proposed=proposed, intents_emitted=ok, rejections=rejected,
        legible_reason=reason, latency_ms={"tick": ms_since(t0)}, ...))
    S.last_emitted = ok
```

**How the loop addresses CivBench's failures.** `[EV: FGA]`
- **Under-monitoring.** The engine's `monitor` checks every alarm every month instead of waiting for a planner to query state.
- **Plan/action gap.** The chosen strategy deterministically expands into intents each tick. The audit records any proposed intent that was not emitted, so the gap can be measured directly.

**Validator layers.**
1. Pydantic schema.
2. Numeric ranges.
3. Adapter legality: the target exists and is in contact, the econ plan is unlocked, and so on.
4. Cooldowns: at most one change of posture per 6 months.
5. Fairness: no intent may reference an unobserved entity.

---

## 7. Evaluation: conditions and metrics

| Condition | Description |
|---|---|
| **A: Static** | Baseline strategy only (vanilla or StarNet-like mapping). No belief-driven switching. |
| **B: Fixed-library adaptive** | Belief and Thompson-sampling selection over the frozen library and θ, with the safe floor. No cross-campaign profile, no offline search. Variant **B+LLM** adds the advisor. |
| **C: Learning adaptive** | B plus the persistent player profile plus offline-tuned, promoted θ versions between campaigns. Variant **C+LLM**. |

**Protocol.**
1. **AI-vs-AI observer runs.** Scripted archetype opponents, including mid-game switchers, play on fixed seeds and maps. Conditions are paired on identical seeds, with N ≥ 30 per matchup per condition. `[HYP]` This gives statistical power that CivBench's 23-run pilot lacked; its authors report non-significant differences. `[EV: FGA]`
2. **Human pilot.** Each player plays a series of campaigns under counterbalanced conditions (A/B/C order randomised). After each campaign they answer a short questionnaire.

**Metrics.**
- **Strength:**
  - win/survival rate against the baseline
  - relative-power trajectory (area under log P_self/P_opp)
  - systems held at years 50, 100 and 200
  - war outcomes
- **Belief quality:**
  - Brier score and ECE per phase
  - top-1 archetype accuracy
  - change-detection delay and false-alarm rate
- **Selection:**
  - regret against a hindsight-best arm per matchup, from the paired-seed matrix
  - safety-floor violations (windows where reward fell below baseline − 2δ)
  - strategy flip rate per 100 months
- **Exploit resistance:** performance against the bait-and-peace probe and the "fake turtle then rush" probe.
- **Advisor:**
  - call rate
  - proposals on time and validated
  - Δ outcome compared with paired no-LLM runs
  - latency p50/p95
  - frame-time impact on the game
- **Engineering:**
  - tick latency p99 ≤ 50 ms
  - intent rejection rate
  - plan/action gap (proposed but not emitted)
  - stale-observation rate
- **Player experience**, all `[HYP]` because the notes found no controlled study (gap in AO):
  - perceived adaptiveness
  - perceived fairness ("felt like cheating", 1–7)
  - legibility ("I understood why it changed")
  - fun

**Hypotheses to test.**
1. B beats A against archetype opponents and is not worse against `balanced`.
2. C beats B by the third campaign against the same player.
3. B+LLM does not beat B on strength, but improves legibility ratings.

All three are `[HYP]`.

---

## Open risks

- **Observation latency and intent application.** Autosave cadence and console `run` behaviour are unverified (SI). The monthly loop has to tolerate observations that are years stale, and the `healthy()` path handles that.
- **Feature availability.** Whether the mod can export visible starbase modules and fleet sightings cheaply is unverified. Without them, `choke_fort` and `fleet_conc` degrade to sparse signals.
- **StarNet as a baseline.** Its status and license are unknown (SI). The default floor is a vanilla-AI mapping until that is checked.
- **Asterion/MIR5.** It is used only as motivation. Its adaptation method is unpublished. `[EV: AO]`
