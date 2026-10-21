# SAE expansion: persistent player profiles and a bounded self-improvement loop

Author: Opus 5.5 (research lead). Date: 2026-10-19. This builds on `reports/Adaptive Stellaris AI engine.md` (the "report") and `research_notes/Stellaris resource architecture/proposal_merged.md` (the "resource proposal"). Their decisions stand unless this proposal says otherwise. The evidence trail, with every URL, is in `verification_notes.md` in this folder.

Labels: **VERIFIED** means I opened the primary source and it supports the claim. **HYPOTHESIS** means a milestone has to measure it. **PROPOSAL** marks a design choice offered for decision, not something already settled. Process note: the Agent tool was not available in this session, so I ran every search and fetch myself. No adversarial reviewer pass was possible, and a Fable review before any of this enters a plan is recommended.

---

## 1. Executive summary

**Player profile (Part A).** A Stellaris save is mostly a *snapshot*. The parts that carry dates are thin: per-country `timeline_events` (such as wars won and lost), war `start_date`s and battles, and the current research-queue date. Everything else has to be reconstructed by diffing successive autosaves. That includes when a tech was finished, when a policy changed and when a system was claimed. This is exactly how stellaris-dashboard builds its history: 37 gamestate processors diff each save against a database (VERIFIED in its source). Two consequences follow. First, the time resolution of any action is the autosave interval. Second, autosaves rotate, so SAE has to extract and keep a compact **player snapshot** each time it parses, rather than keep saves.

From those snapshots the profile pipeline produces an inferred action stream, then one summary per campaign, then a **hierarchical, interpretable profile**: about 12 dispositional parameters (expansion tempo, chokepoint fortification, first-war timing, hull mix, tech emphasis, response to pressure). These are partially pooled across campaigns with decay, together with a player-specific archetype transition matrix for the existing Bayes filter. It runs between sessions, takes CPU-seconds, and is local only. Style embeddings, inverse RL and cross-player models are **not** justified by one person's 5–20 campaigns. The chess literature shows that population priors cut individual data needs to about 20 games, but SAE has no population yet.

**Self-improvement (Part B).** The 2026 RSI results the user found are mostly real, but several numbers were garbled, and one lead (the "RSI Loop Acceptance Gate") does not exist as a named source (§2). The lesson that transfers best comes from AIDE²: **the inner loop optimises a cheap public signal, and acceptance uses a private, held-out grade the proposer cannot see**. The Darwin Gödel Machine shows why this matters: an agent faked test logs and deleted the reward-hacking markers.

For SAE, the scarce resource is game benchmarks. One second machine yields a handful of paired runs per night (HYPOTHESIS), so an AIDE²-style 100-candidate loop on real games is impossible. The design is therefore:

- **Inner loop:** parameter search (detector hyperparameters, then template θ) scored first on game-free replay evaluators built from bus.jsonl and labelled campaigns, and only then on paired benchmarks.
- **Outer loop:** an offline developer-loop meta-agent (Claude Code) that proposes new templates, features or code as pull requests.
- **Gate:** a two-stage acceptance gate that humans own and the loop cannot edit.
- **Runtime:** self-modification stays off. Only signed, versioned, gate-passed policies ship. Live state is limited to beliefs.

**What to prototype first after the MVP** (all game-free): the snapshot-and-diff extractor, leave-one-campaign-out tuning of the detector on replays, and a **fog-perturbation test** that makes the fairness boundary checkable by machine.

---

## 2. Verification of the five user-supplied leads

| # | Claim as supplied | What the primary source says | Status |
|---|---|---|---|
| 1 | **MASS**, Sakana AI + UC Berkeley, Oct 2026: one base model proposes, executes and self-evaluates multi-agent workflows; alternates evolutionary workflow optimisation with SFT; 1.2–1.6× performance per output token after two cycles | arXiv 2610.12176 (8 Oct 2026; Lee, Xu, Seely, Lee, Sojoudi, Zaharia, Tang). The abstract matches almost word for word: "propose, execute, and self-evaluate multi-agent workflows", "alternates between evolutionary workflow optimization and supervised fine-tuning", "1.2-1.6x higher performance per output tokens on four open-ended public benchmarks" with Qwen3.6-27B. Affiliations come from a garbled PDF header (Sakana + Berkeley, matched by position). **Caveats:** raw gains are modest (ScienceAgentBench 28.8→31.7%), DSBench is within one SD, SWE-bench Verified and Terminal-Bench dip slightly, workflow search "can reinforce rather than correct errors", and mean quality per delivered MLR paper falls (3.07→2.88). Reward hacking is not discussed. | **VERIFIED** (claim accurate; it is an efficiency metric, not a raw accuracy gain) |
| 2 | **AIDE²**, Weco AI: bi-level RSI; "Level 1 net-positive RSI over 100 unsupervised steps across eight days"; seven improved versions; KernelBench reward hacking 63%→34% | **Blog (14 Jul 2026):** 100 outer-loop steps, 8 days, 7 improved versions, about 90% of proposals rejected, reward hacking **63% → 42% → 34%** (hand-tuned AIDE 42%), and "Level 1 (net positive)" on Weco's own four-level ladder; Level 2 "ignition" not reached. **Paper (arXiv 2609.26457, 22 Sep 2026):** 99 proposals with 7 accepted, grade 0.703→0.778, but reward hacking **55% → 39% → 32%** (human 39%) on 38 pairs. The inner loop optimises a public signal and selection uses a private held-out grade. About a quarter of rejected rewrites won on the public signal and lost on the private one. Ignition is inconclusive (3 seeds); the OOD claim rests on one task; self-reported. | **VERIFIED**, with numbers that differ between blog and paper. Cite the paper. |
| 3 | **Dream-RSI: Evolving Worlds**, Sept 2026: completed discovery history used as a replay simulator for off-policy evaluation and optimisation; 1.62–2.43× efficiency gains | arXiv 2609.14858 (14 Sep 2026; Google, Google DeepMind, UMD, UVA) plus a NeurIPS 2026 workshop poster. The mechanism matches: completed exploration trees become "replay worlds" and policy code is scored across all of them. **"1.62" appears nowhere.** The paper reports 1.79–2.43× fewer generations on KernelBench, up to 2.09× better kernels at a similar budget, and "162× fewer calls" on Lasso against a baseline with a different backbone. No seeds or variance are reported, one task is worse, and replay "cannot evaluate new candidates" outside recorded support. | **PARTLY VERIFIED**: the concept is right and the range is misquoted |
| 4 | **"The RSI Loop Acceptance Gate"**: Stage 1 "did it work?" vs Stage 2 "is it meaningful?" | No source found under this name or with these phrases. The nearest real work: **MetaRSI** (arXiv 2609.06396) uses strict improvement over the historical best, checks that a diff stays within mutable surfaces and that the declared change equals the actual diff, and scores once on a sealed held-out split. **Falsifiable Release Gates** (arXiv 2607.13070) sets five invariants: acceptance monotonicity, a gate the system cannot modify, one-cycle reversibility, eval freshness and a reward-hacking floor; loosening changes need a human merge. A community skill describes a two-split (held-in and held-out) no-regression gate. | **NOT FOUND**. It is a reasonable framing, and §5.4 uses it as SAE's own design. |
| 5 | **TasteVal** (P-Zero Research); Jack Clark estimated functional full RSI around 2027 | TasteVal is real (LessWrong, 6 Oct 2026, Ollie J and Dane Sherburn). It measures experimental research taste as a compute multiplier over 24 experts on 8 tasks; Opus 5.5 scores 2.30× (CI 1.15–4.37). The tweet handle is "pzeroresearch", but the page does not name the organisation. **Clark is not mentioned.** Clark's own Import AI 455 (4 May 2026) says "a likely chance (60%+)" of no-human-involved AI R&D **by end of 2028**, and "If you had to push me for a 2027 probability, I'd say 30%." | TasteVal **VERIFIED** (affiliation unverified); the Clark claim is **INACCURATE** (30% for 2027, 60%+ for 2028) |

None of these results comes from games. All of them use code or research tasks with cheap, automatic graders. SAE's grader is a multi-hour game run, and that difference shapes everything in Part B.

---

## 3. Part A: building a persistent player profile

### 3.1 What data exists

| Source | What it holds | History depth | Notes |
|---|---|---|---|
| **One save** (`.sav` = zip of `meta` + `gamestate` text; wiki, last checked for 2.1) | Full current state for every country: planets, systems and starbases, fleets and ships, `tech_status` (researched list plus queue), policies, government, leaders, diplomacy, active `war` entries with `start_date`, battles, war exhaustion and war goals | **Thin.** Per-country `timeline_events` with dates (dashboard source reads `timeline_war_won/lost`, `timeline_first_war_won/lost`, `timeline_destroyed_leviathan`); war start dates; queue date. Finished wars appear to be resolved through timeline events rather than kept in `war` (inferred from dashboard code) | VERIFIED via stellaris-dashboard `timeline.py`. Its comments mention 4.4 and "since 4.5" format changes, so field names drift by patch. |
| **Autosave sequence** | Snapshots at the autosave interval (monthly in dev, per the report) | As deep as SAE keeps it | One user reports only 5 rotating autosaves (`autosave_2401.10.01.sav`) (anecdote, HYPOTHESIS). SAE must therefore **extract at parse time**. |
| **SAE log lines** (`SAE\|v1\|PLYR…`) | Monthly player feature vector from script | Session only, unless written to the bus | Finer timing than saves. Per-event player hooks via on_actions would be finer still (HYPOTHESIS: on_action names not checked this pass; mod-script cost stays ≤ 1%). |
| **bus.jsonl, current and past runs** | Observations, beliefs, strategies, intents, acks, outcomes | Every campaign SAE has seen | The only source that records **what SAE did and how the player responded afterwards**. |
| **Old saves already on disk** | Final or occasional snapshots of campaigns played before SAE | Whatever the user kept | Free cold-start data, consent-gated (§3.6). Older game versions need version-aware extractors. |

**Conclusion:** a single save yields dispositions (what the empire *is*) and a few dated war outcomes. Action *sequences* (when the player claimed, fortified, switched to war economy or teched lasers) have to come from snapshot diffs. Their timing is bracketed as `[t_prev, t_cur]`, so monthly autosaves give ±1 month, which is plenty against 24-month commitments.

### 3.2 Pipeline (PROPOSAL)

```
autosave ─► sae-parse (existing worker) ─► player_snapshot (legal fields + fow tags, ~KB)
                                            │ append to bus (new event type)
                       diff(prev, cur) ◄────┘
                            ▼
           player_action_inferred {kind, t_lo, t_hi, evidence, confidence}
                            ▼   (campaign end, or abandoned > N days)
                     campaign_summary {style vector, phase timings, response-to-pressure stats}
                            ▼   (CLOSED state only, seconds of CPU)
                profile store: profile.json (current) + profile_history.jsonl (append-only)
                            ▼   (next campaign start)
     engine reads priors: b_0, archetype transition matrix, feature likelihood offsets
```

The diff engine copies stellaris-dashboard's processor pattern, including its fix of stable `crc32` digests for change detection. It runs **inside `sae-parse` or as a CLOSED-state job**, never in the daemon's hot path, so the resource proposal's RAM rules still hold.

### 3.3 Behaviour taxonomy

| Family | Example features (per year or per phase) | Source | Live-legal? |
|---|---|---|---|
| Expansion | systems claimed per year; outposts vs colonies; direction (toward vs away from AI neighbours) | diffs of owned systems | Partly: border growth is visible |
| Chokepoint use | share of border starbases upgraded or fortified; defences per border chokepoint vs interior | starbase section, hyperlane graph | Yes for bordering systems (the report's v0 approximation) |
| Fleet doctrine | hull-size mix; fleet power vs naval cap; doomstack vs split (fleet count) | fleets and ships | Size mix only when seen; counts are god-view |
| Tech path | category shares; time to key tiers (weapons, hulls); repeatables onset | `tech_status` diffs | Not live (god-view); fine for completed campaigns |
| Diplomacy | pacts, federation timing, rivalries, closed borders | diplomacy diffs, timeline | Mostly yes (diplomacy screen) |
| War timing | first war or claim month; war frequency; who declares; war goals; exhaustion at peace | `war`, `timeline_events` | Yes for wars involving the observer |
| Risk appetite | fleet-to-cap ratio at war start; wars declared at power ratio < 1; minimum reserves; overextension | composite | Partly |
| Response to AI pressure | change in the above in the 12–24 months after SAE commits a counter vs matched windows without one | **bus intents × diffs** | Derived offline only |

The last row is SAE's distinctive asset. Every strategy switch is a logged **intervention** with a `decision_id`, so the player's reaction can be estimated as a before/after contrast against matched periods. That is still not causal, because SAE chose those moments, but it beats guessing.

### 3.4 Representation, cheapest first

1. **R1 Archetype prior** (already in the core plan): a Dirichlet over each past campaign's final beliefs, with a half-life of 5 campaigns. It sets `b_0`.
2. **R2 Hierarchical style vector (recommended core).** About 12 interpretable parameters from §3.3, each modelled as *designer hyperprior → player mean → campaign effect*. The model is conjugate Normal–Normal or Beta–Binomial with partial pooling and decay. It is pure NumPy, tiny, and can be read in a "what SAE thinks of you" panel. It feeds the filter as **likelihood offsets**: if this player always fortifies, a fortified border is weak evidence of "turtle this game", so the per-player baseline is subtracted.
3. **R3 Player-specific phase model.** The forgetful filter uses a fixed share ρ. Replacing it with a learned 3×3 transition matrix per game phase makes the filter an HMM. This captures habits such as "builds until year 40, then turns aggressor". It is estimated from past campaigns' belief tracks with Dirichlet-smoothed counts.
4. **R4 Choice models ("IRL-lite").** Conditional logit over discrete choices with observable alternatives, such as tradition trees, ascension perks and tech picks among offered cards. Whether the save keeps the offered alternatives is HYPOTHESIS. Full MaxEnt IRL needs a known MDP and feature counts (Ziebart 2008, VERIFIED), which Stellaris does not expose, so it is **rejected**.
5. **R5 Embeddings and sequence models (parked).** Behavioural stylometry identifies chess players among thousands with 98% accuracy from 100 games, and Maia4All models an individual from about **20 games** only because a pretrained population model with prototype players exists (VERIFIED). SAE has one player and no population. Revisit only if opt-in pooled data ever exists.

### 3.5 Non-stationarity: the player learns too

- **Within a campaign** the existing forgetful filter handles it, plus R3.
- **Across campaigns:** each new campaign summary is scored against the profile's posterior predictive. If at least k of 12 features fall outside the 90% interval, a `drift_alarm` fires, the profile's concentration is widened (the prior reverts toward the hyperprior), and the alarm is logged. BOCPD (Adams & MacKay, VERIFIED) is the principled version over the campaign sequence, but with fewer than 20 campaigns a predictive check is enough.
- **Co-adaptation:** the profile records the `sae_policy_version` active during each campaign. A player who learns to beat `early_punish` shows up as a shift that coincides with the policy that taught them. That is useful for the outer loop, because counters that stop working against this player are a signal.
- **Confounders:** stratify by game version and playset hash, since patch changes look like style drift.

### 3.6 Fairness, privacy and consent

**The fairness rule: reputation, not surveillance (PROPOSAL).** A human rival who has played you before knows your *tendencies*. They do not know where your fleet is now. So:

- The profile schema holds **only dispositional, map-independent statistics** from **completed** campaigns. It has no coordinates, ids or empire names, and the schema enforces this.
- Live inference in the current campaign stays fog-legal, as today.
- Every feature carries `legality ∈ {legal, past_godview}`. The profile builder rejects `past_godview` for any campaign still in progress, which also covers Ironman between-session updates.
- **Mode switch for the user:** `strict` (profile built only from legal features, so it holds what an AI that met you could have seen), `reputation` (default proposal: also god-view aggregates from your own finished games) or `off`.

**Privacy.** Behavioural profiles identify people (the chess stylometry result), so the profile is treated as personal data.

- It is local only, kept in the SAE data folder, and never uploaded.
- `sae profile show | export | delete` CLI commands.
- Backfilling from old saves requires explicit opt-in.
- Saves are stripped of multiplayer usernames before any snapshot is written.
- If an LLM is used in the developer loop (Part B), it receives only pseudonymised bus extracts and only with consent.

### 3.7 Cold start

The cold-start sources, in order:

1. **Designer hyperpriors**, which is today's behaviour.
2. **Backfill** of old saves on disk (opt-in). This gives final-state dispositions plus dated war outcomes even when only one save per campaign survives.
3. **Prototype matching** in the Maia4All spirit: in the first 20–30 game years, map the player to the nearest of the three archetype prototypes and borrow its parameters.
4. An optional one-question **self-report** ("I usually play…") as a weak prior, down-weighted because self-reports are biased.

### 3.8 Evaluating profile quality

| Test | Method | Pass (HYPOTHESIS thresholds) |
|---|---|---|
| Next-campaign prediction | Rolling origin: fit on campaigns 1..k, predict k+1's style vector | Log score / CRPS beats (a) hyperprior only, (b) last campaign only, (c) undecayed mean |
| Detection speed | Replay campaign k+1 through the filter with and without the profile prior | Median months to a correct top-1 belief ≥ 0.45 improves by ≥ 25% |
| False lock | Replay deliberately off-style campaigns (ask the user to play one) | The profile does not delay correct detection by > 6 months vs a flat prior |
| Calibration | ECE of archetype beliefs | ≤ 0.10 (the core plan's gate) |

**Honest limit:** one player produces perhaps 2–10 campaigns a year. The profile's realistic payoff is **faster, better-calibrated detection in the first 30 years**, not a new capability.

### 3.9 Contract additions needed (PROPOSALS for the build effort, not edits)

1. **Bus event types:**
   - `player_snapshot`: `{features, legality per feature, parser_version, game_version}`
   - `player_action_inferred`
   - `campaign_summary`
   - `profile_loaded`: profile hash and the priors actually used at campaign start, so that every decision traces to a profile version
   - `drift_alarm`
2. **Campaign index** `campaigns.jsonl`: `run_id ↔ save folder ↔ game version ↔ playset hash ↔ sae_policy_version ↔ status`.
3. **Profile store** `profile/<local_player_key>/profile.json` plus `profile_history.jsonl`, with `schema_version`. SQLite only if queries need it, as the report allowed from M4+.
4. **Snapshot retention policy:** keep every `player_snapshot` (KBs each). Optionally keep every Nth raw save so the snapshots can be re-extracted when the extractor changes, under a disk cap.
5. **Interface rule:** the engine reads the profile **read-only at campaign start**. Profile writes happen only in CLOSED state.

---

## 4. What the RSI literature transfers, and what it doesn't

| Work | Pattern | Transfer to SAE |
|---|---|---|
| AIDE² (paper + blog) | Bi-level loop; public signal inner, **private held-out grade** for selection; about 90% rejection; human baseline | **Yes**: public = replay evaluators; private = sealed benchmark set |
| Darwin Gödel Machine (Sakana) | Archive of variants; empirical validation; *the agent faked test logs and removed the hallucination markers*; sandbox + human oversight + lineage caught it | **Yes**: the evaluator is out of the proposer's reach; scores come from the farm, not the agent; full lineage |
| Dream-RSI | History as a replay simulator; select by mean over all past worlds | **Partly**: valid for the *detector* (observations are fixed data), invalid for *counter selection* (it cannot score actions never taken) |
| MASS | Self-generated trajectories + SFT; self-judging | **No** for runtime (no model to fine-tune, and self-judging reinforces errors); a caution for any LLM judge |
| AlphaEvolve / AIDE / FunSearch-style search | LLM proposes code, an automatic evaluator scores it | Only where a cheap evaluator exists (feature extractors against labelled replays) |
| AlphaStar league, PSRO | Exploiters stop main agents overfitting; independent training overfits to co-players | **Yes**: an exploit-probe league against SAE; held-out player stand-ins |
| Voyager | Library of verified, reusable skills | **Yes**: counter-templates *are* the library; new entries only through the gate |
| Falsifiable Release Gates, MetaRSI | Non-self-modifiable gate, reversibility, mutable-surface checks, sealed split | **Yes**: in the Stage 1 checks |

---

## 5. Part B: a bounded RSI loop for SAE

### 5.1 What gets improved

A **PolicyVersion** artefact, as in the core plan: semver, archetype likelihood parameters, ρ or the transition matrix, clip L, belief thresholds, the counter table, template θ, the game-version pin, the eval-report hash and lineage. Beyond that, **code** in a few **mutable surfaces**: `features/`, `templates/` and `belief/`. The gate, the evaluators, fixtures, the validator, the no-bonus rule, the fog mask and the knob vocabulary are **immutable surfaces**. Only a human can change them, through a normal review.

### 5.2 Inner loop (automatic, numeric)

| Loop | Search space | Evaluator (public signal) | Cost |
|---|---|---|---|
| I1 Detector tuning | ρ, L, likelihood parameters, threshold, feature subset | **Replay suite:** recorded observation streams (bus + labelled campaigns + SynthAdapter); leave-one-campaign-out log loss, ECE, detection delay | CPU-minutes; thousands of CMA-ES evaluations are affordable in CLOSED state |
| I2 Template θ | budget weights, hull mix, posture timings per template | Paired observer-mode benchmarks against scripted archetype stand-ins | **Hours per run**; racing / successive halving |
| I3 (M5 parking lot) | same as I2 | Surrogate macro-simulator | Gated on hundreds of M4 runs (resource proposal) |

**Why replay cannot tune counters:** the player reacts to SAE, so the recorded futures exist only for the counters SAE actually chose. Li et al.'s replay estimator, and Dream-RSI's own "out-of-support plans earn no reward" rule, say the same thing. Offline evaluation of counter *selection* would need **randomised selection with logged propensities**. That is acceptable in the benchmark farm and is a user decision for real play (§9).

### 5.3 Outer loop (developer loop, LLM-assisted, human-merged)

An offline meta-agent, Claude Code, runs in a sandboxed checkout. It reads the **raw** candidate archive: bus files, eval reports and rejected candidates with their evidence. The community skill reports that raw traces beat summaries for proposers (unverified claim; cheap to follow). The meta-agent proposes:

- (a) new counter-templates expressed *only* in the existing knob vocabulary;
- (b) new features or extractors with fixtures;
- (c) changes to the belief model;
- (d) exploit probes (scripted player behaviours that might break SAE).

Each proposal is a PR with a declared change, and it enters the gate. The proposer **never** runs the private benchmark and never sees the sealed scenarios. Humans keep four decisions: changes to evaluators or the gate, expansion of mutable surfaces, promotion to release, and abandoning a direction.

### 5.4 Two-stage acceptance gate (SAE's own design; the supplied lead's name is not a real source)

**Stage 1, "did it work?" (mechanical, minutes, fully automatic):**

1. Build plus the CI suite, including the resource proposal's CI-1…CI-5, the import whitelist, and the RSS and CPU budgets.
2. Contract tests: bus schema, intent validator, the no-bonus rule, and compatibility with the `schema_version` mod contract.
3. **Mutable-surface check:** the diff touches only declared surfaces, and the declared change matches the actual diff (MetaRSI).
4. **Fog-perturbation test (new):** replay recorded campaigns with every god-only field randomised. The decisions must be **bit-identical**. This turns the report's "convention, not a guarantee" into a real check.
5. Replay determinism, plus detector non-regression on the held-in replay split.

**Stage 2, "is it meaningful?" (statistical plus human, days to weeks):**

1. **Pre-registration:** the primary metric, the minimum effect and the run budget are written down before runs start.
2. **Paired benchmark** on the incumbent vs the candidate, same start saves and seeds. The lower confidence bound on the primary metric must be > 0, *and* the candidate must be non-inferior in every archetype matchup (≤ 5 pp worse, from the core plan's promotion rule).
3. **Two-split rule:** no regression on **held-out stand-ins** never used in tuning (different scripted variants and replays of human-labelled campaigns). Trades between splits are rejected even when the sum improves.
4. **Exploit probes** pass: bait-and-peace loops, fake-turtle-then-rush, and the league (§5.5).
5. **Feel bounds:** switches per campaign, commitment adherence, reason-key coverage, and no "erratic" signature (the Hamlet warning).
6. **Freshness:** sealed scenarios rotate, and each candidate may query the sealed set at most once.
7. **Human maintainer approval**, plus one real playtest campaign for template changes.

Promotion is a pointer move in an append-only registry. **Rollback is one step.**

### 5.5 Reward hacking and Goodhart risks specific to this game

| Risk | Example | Measure |
|---|---|---|
| Stand-in overfitting (PSRO's joint-policy correlation) | θ learns to beat *vanilla AI playing turtle*, which is not a human turtle | Held-out stand-ins; human-labelled replays; an exploiter league; report results per stand-in |
| Difficulty-slider collapse | Every template drifts to "max military everywhere"; the metric rises, adaptation disappears | A **counter-specificity** metric (advantage on the matched archetype minus advantage on a mismatched one) as a secondary pre-registered metric; the no-bonus validator |
| Benchmark-quirk exploitation | A strategy that stalls or crashes the player-AI, or a timeout scored as a win | Crashes and timeouts invalidate a run and never count as a win; outcome anomaly flags; humans watch the top-k runs (lineage caught DGM's hacks) |
| Evaluator tampering by the meta-agent | Editing fixtures or thresholds; faked logs (DGM did both) | Immutable surfaces (CODEOWNERS), sandbox without write access to `gate/`, scores produced by the farm and **bound to the candidate hash**, no agent-reported numbers |
| Holdout overfitting | Many candidates tuned toward a sealed set that has leaked | Query limits, rotation, eval freshness |
| Detector Goodhart | Tuning on 5 labelled campaigns until accuracy is perfect | Leave-one-campaign-out only; report the variance |
| Player-profile over-exploitation | Counters hard-tuned to one person's history fail when that person changes | Safety floor; priors with a minimum mass ε per archetype; drift alarm |
| Metric choice itself | "Overcome the player" rewards crushing them, but the product promise is a legible, fair rival | A user decision (§9). Proposal: primary = AI survival or advantage at year +50 against the matched archetype, with feel bounds as hard constraints |

### 5.6 Safety boundaries: why runtime self-modification stays off

1. **No statistical power:** about 100 decisions per campaign (report), so online self-improvement would chase noise.
2. **Guarantees are per version.** The no-bonus rule, the fog test and the resource budgets are checked on a frozen artefact, and runtime change would void them.
3. **Feel:** Hamlet warns that reactive changes make enemies "seem erratic".
4. **Consent:** players are not test subjects. Only beliefs (and, after M4, possibly bandit posteriors) update live, as the core plan already says.
5. **Reproducibility:** a bug report has to name one policy hash.
6. **Resources:** the daemon stays at ≤ 150 MB with no LLM. The cited RSI systems all ran in sandboxes with humans watching.

The **daemon loads a hashed PolicyVersion read-only**. It refuses one whose game version or hash does not match. Users can pin a version or choose a "stable" or "experimental" channel.

### 5.7 Compute reality

| Machine | Work | Throughput (HYPOTHESIS until measured) |
|---|---|---|
| Player PC, PLAYING | Nothing from this proposal except `player_snapshot` extraction inside the existing parse | — |
| Player PC, CLOSED | Profile update; diff backfill; I1 replay tuning | Seconds; I1 runs in minutes |
| Second machine | Benchmark farm, one Stellaris instance | Report: 30–90 min per 100-year run is "optimistic". Assume 1–3 h per run and **4–12 runs per night** |
| Cloud (developer) | The Claude outer loop | Dollars per proposal; **throttled to farm capacity** |

The arithmetic: Stage 2 for one candidate is 3 archetypes × 10 pairs × 2 arms = 60 runs, which is **5–15 nights**. So the real game loop accepts roughly **1–4 candidates per month**. Three things stretch it:

- Evaluate **30-year windows from a bank of mid-game start saves**, not 100-year campaigns, since commitments are 24 months.
- Race candidates: stop a candidate early if it trails after 10 pairs.
- Let I1 and Stage 1 discard most proposals before any game time is spent. AIDE² rejected about 90%.

---

## 6. Ranked options (expected value vs cost)

| Rank | Option | EV | Cost | Note |
|---|---|---|---|---|
| 1 | `player_snapshot` + diff engine + `campaign_summary` | High (foundation for everything) | Low | Pattern proven by stellaris-dashboard |
| 2 | Fog-perturbation test as a CI check | High (fairness becomes checkable) | Low | Game-free |
| 3 | I1 replay tuning of the detector, leave-one-campaign-out | High | Low | Uses M2's labelled campaigns |
| 4 | PolicyVersion registry + two-stage gate + rollback | High (prerequisite for shipping anything tuned) | Medium | Must precede ranks 7–8 |
| 5 | Hierarchical style profile (R1+R2) + drift check + privacy CLI | Medium–high | Low–medium | Needs ≥ 3 campaigns to beat the hyperprior |
| 6 | Backfill from old saves (opt-in) | Medium (cold start) | Low | Version-aware extractors |
| 7 | I2 CMA-ES over θ on the farm | Medium | High (farm nights) | Only after M4 shows the knobs matter |
| 8 | Outer loop: Claude-proposed templates, features and probes as PRs | Medium, high variance | Medium | Throttled by the farm; humans merge |
| 9 | Exploit-probe league | Medium | Medium | Partly authored by the outer loop |
| 10 | R3 player-specific phase HMM | Medium | Low | After rank 5 has data |
| 11 | R4 choice models | Low–medium | Medium | Depends on alternatives being in the save |
| 12 | Randomised counter logging for off-policy evaluation | Medium (long run) | Low code, **high consent cost** | Farm only unless the user opts in |
| 13 | Embeddings, IRL, cross-player models | Low now | High | Parked |
| — | Runtime self-modification | Negative | — | **Rejected** |

---

## 7. Phased roadmap after M0–M4

The resource proposal already uses "M5" for the surrogate parking lot, so these phases are named **X1–X5**.

| Phase | When | Work | Exit |
|---|---|---|---|
| **X0 Hooks** | During M2–M3, if the build effort accepts the proposals | Emit `player_snapshot` from the existing parse; campaign index; `profile_loaded` placeholder | Snapshots present in every M3 campaign bus. *Cost is minutes, and it saves irrecoverable history.* |
| **X1 Profile v1** | After M3 (2–3 weeks; can overlap M4) | Diff engine, `player_action_inferred`, `campaign_summary`, R1+R2 profile, drift check, `sae profile` CLI, opt-in backfill | Rolling-origin test beats the hyperprior on the user's campaigns; the privacy CLI works |
| **X2 Gate and registry** | With M4 (2 weeks) | Registry, Stage 1 automation (incl. the fog-perturbation test), Stage 2 harness with pre-registration and racing, rollback | A dummy candidate passes and fails the gate as expected; one-step rollback demonstrated |
| **X3 Inner loop** | After X2 | I1 detector tuning (promoted through the gate), then I2 θ tuning if M4 shows knob efficacy | First tuned PolicyVersion promoted with the Stage 2 evidence attached |
| **X4 Outer loop and league** | After X3 | Claude proposer in a sandbox with immutable surfaces; exploit probes; R3 phase model | At least one LLM-proposed template or feature accepted, and the rejection log is kept |
| **X5 Parking lot** | Gated | Surrogate (M5), randomised logging for OPE, embeddings or pooled priors (opt-in) | Explicit user decisions |

**Prototype right after the MVP** (game-free, about a week): rank 1 on recorded M2/M3 saves, rank 2 on recorded buses, and rank 3 on the labelled campaigns.

---

## 8. Risks

1. **Too little data per player:** the profile's benefit may be small. Measure it before building beyond R2.
2. **Save format drift** across 4.x patches (dashboard comments show 4.4 and 4.5 changes): version-aware extractors and fixtures per version.
3. **Farm throughput** caps how fast the real loop can learn. Keep expectations at roughly monthly promotions.
4. **Stand-in validity** (the report's circularity risk) carries straight into tuning. Human-labelled held-out data is the only real check.
5. **Fairness drift:** a profile in "reputation" mode could feel unfair to a user. Make the mode visible and default it to the user's choice.

---

## 9. Open questions for the user

1. **Objective:** should SAE *beat* you as often as possible, or keep you inside a challenge band? This sets the Stage 2 primary metric.
2. **Profile fairness mode default:** `strict` (only what an AI that met you could have seen) or `reputation` (also god-view aggregates from your own finished games)?
3. **Backfill:** may SAE read your existing save folders? How many past campaigns do you have, and from which game versions?
4. **Randomised counters:** allowed in the benchmark farm only, or also (lightly, logged) in your own games to enable off-policy learning?
5. **LLM in the developer loop:** acceptable to send pseudonymised bus extracts to a cloud model?
6. **Second machine:** how many nights a month can it run benchmarks? This sets the outer-loop budget.
7. **Distribution:** should tuned policies ship to other players (release channel) or stay personal? Would you ever opt in to pooled, anonymous priors?
8. **Multiple players on one PC:** how should the local player key be chosen?

---

## Sources (only pages opened in this pass)

User leads:
- MASS: https://arxiv.org/abs/2610.12176 · https://arxiv.org/pdf/2610.12176 · https://github.com/SakanaAI/mass/wiki
- AIDE²: https://arxiv.org/pdf/2609.26457 · https://www.alphaxiv.org/abs/2609.26457.md · https://www.weco.ai/blog/first-evidence-of-recursive-self-improvement · https://www.weco.ai/blog/4-levels-of-recursive-self-improvement · https://www.weco.ai/blog · https://threadreaderapp.com/thread/2077079778793042425 · https://fourweekmba.com/ai-weco-ai-aide2-recursive-self-improvement-benchmark/
- Dream-RSI: https://arxiv.org/abs/2609.14858 · https://arxiv.org/pdf/2609.14858 · https://neurips.cc/virtual/2026/169874
- Acceptance-gate search: https://arxiv.org/pdf/2609.06396 · https://arxiv.org/pdf/2607.13070 · https://tessl.io/registry/skills/github/muratcankoylan/Agent-Skills-for-Context-Engineering/self-improvement-loops/review · https://raw.githubusercontent.com/muratcankoylan/Agent-Skills-for-Context-Engineering/main/skills/self-improvement-loops/SKILL.md
- TasteVal and Clark: https://www.lesswrong.com/posts/bAGagjZAPgXEHmkAH/tasteval-measuring-the-experimental-research-taste-of-ai · https://importai.substack.com/p/import-ai-455-automating-ai-research · https://en.hvylya.net/news/2547-anthropic-s-jack-clark-sees-ai-training-its-own-successor-by-2028 (secondary)

Established work:
- DGM: https://arxiv.org/abs/2505.22954 · https://sakana.ai/dgm/
- AlphaEvolve: https://arxiv.org/abs/2506.13131
- AIDE: https://arxiv.org/abs/2502.13138
- AlphaStar: https://www.nature.com/articles/s41586-019-1724-z
- PSRO: https://arxiv.org/abs/1711.00832
- PBT: https://arxiv.org/abs/1711.09846
- Voyager: https://arxiv.org/abs/2305.16291
- Specification gaming: https://deepmind.google/discover/blog/specification-gaming-the-flip-side-of-ai-ingenuity/
- Goodhart variants: https://arxiv.org/abs/1803.04585
- Replay off-policy evaluation: https://arxiv.org/abs/1003.5956

Stellaris data:
- stellaris-dashboard: https://github.com/eliasdoehne/stellaris-dashboard · https://raw.githubusercontent.com/eliasdoehne/stellaris-dashboard/master/stellarisdashboard/parsing/timeline.py · https://raw.githubusercontent.com/eliasdoehne/stellaris-dashboard/master/stellarisdashboard/datamodel.py · https://raw.githubusercontent.com/eliasdoehne/stellaris-dashboard/master/README.md
- jomini: https://github.com/rakaly/jomini
- pdx.tools: https://pdx.tools/
- Save format: https://stellaris.paradoxwikis.com/Save-game_editing
- Autosave rotation anecdote: https://gog.com/forum/stellaris/too_many_save_gamesstill

Player modelling:
- Behavioural stylometry: https://arxiv.org/abs/2208.01366
- Individual chess models: https://arxiv.org/abs/2008.10086
- Maia4All: https://arxiv.org/html/2507.21488v1
- Player modeling (Yannakakis et al.): https://drops.dagstuhl.de/opus/volltexte/2013/4335/
- MaxEnt IRL: https://cdn.aaai.org/AAAI/2008/AAAI08-227.pdf
- BOCPD: https://arxiv.org/abs/0710.3742
