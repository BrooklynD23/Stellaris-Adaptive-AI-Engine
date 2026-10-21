# Advisor Review: Adaptive Stellaris AI Engine plans

Date: 2026-10-18. Reviewed: five research notes and three plans. I did not write any of them. Six web lookups were spent checking load-bearing claims (Paradox wiki: Effects, Variables, Console commands). Verdict up front: the research is honest about what it does not know, the architecture is sound in outline, and the plans are roughly three times too big for a first playable. The single biggest unexamined issue is not any one channel but the **influence ceiling** (section 3).

## 1. Inconsistencies between the three plans, with one reconciled choice each

| Topic | Integration plan | Engine core plan | Dev-env plan | Pick |
|---|---|---|---|---|
| Project prefix | `SAE`, `sae_*`, log prefix `SAE\|v1\|` | `adaptive_core/`, `adapters/stellaris/` | `SAI`, `sai_*`, `src/sai/`, `SAI\|v1\|<json-ish>` | **`sae`** everywhere (mod ids, log prefix, Python package `sae`). The integration plan is the one that touches the game, so it wins naming. |
| Mod event file | `events/sae_bridge_events.txt` | n/a | `mod/events/sai_telemetry.txt` | `events/sae_bridge_events.txt` |
| Log line format | pipe-delimited key=value | n/a | "json-ish" | **pipe key=value.** JSON braces inside a Clausewitz string are a quoting risk; key=value is trivially parsed and greppable. |
| `SET_ECON_PLAN` enum | balanced, alloys_rush, tech_rush, consumer_stability, expansion | balanced, military_industry, research, expansion | wireframe shows `econ_plan=alloys_hi` | **balanced, military, research, expansion.** Four, matching the four things an `ai_budget`/subplan flag can plausibly express. Drop `consumer_stability`; it is not a counter-strategy. |
| Posture | turtle, build_up, aggressive, total_war + intensity 0–1 | defensive, deterrent, offensive + target_id | n/a | **defensive, deterrent, offensive + target_id.** Drop intensity until a knob demonstrably scales with it. `target_id` is required for opinion-modifier compilation. |
| Tech intent | `PRIORITIZE_TECH_TREE` | `PRIORITIZE_TECH` | n/a | `PRIORITIZE_TECH`. Mark it **deferred**; no `ai_weight` hook for research is verified. |
| Fleet composition | per ship size (StarNet `str_var_ai_ship_data_factor_<size>`) | per role (artillery, torpedo, carrier, picket, brawler) | n/a | **Per ship size.** It is the only verified knob. Roles are a designer concept the game's auto-designer and NShip defines control at load time, so the core plan's role vocabulary cannot be compiled live. |
| Heartbeat | `NOOP_HEARTBEAT` | absent | absent | Keep it; it is the liveness test for the whole bridge. |
| Acks | APPLIED only on `ACK` log line; LOST after 2 months | tick loop has no ack concept | `acks()` iterator; timeout after "K months" | Ack is a first-class bus event; `K = 2` months; the tick loop reads `adapter.acks()`. |
| Adapter protocol | n/a | sync `observe(raw)`, `emit(ok)`, `healthy(obs)` | async `observations()`, `emit() -> EmitReceipt`, `acks()`, `clock()` | **Dev-env version** (async iterators plus acks). It is the one that can drive replay and synth adapters. |
| Audit store | n/a | `DecisionAuditRecord` JSONL via `telemetry/audit.py`, plus SQLite `MemoryStore` | bus.jsonl is the only store; audit record is a DuckDB view | **Bus is the single source of truth.** `DecisionAuditRecord` becomes the pydantic schema of one bus event type, not a separate writer. SQLite only for the cross-campaign profile (M4+). |
| Benchmark conditions | n/a | A static, B belief+Thompson over frozen library, C B+profile+offline θ, ±LLM | A vanilla, B rule-based fixed library (no bandit), C full engine incl. LLM, C' no LLM | **Dev-env ladder, renamed:** A vanilla; B belief + rule table; C B + bandit; D C + LLM. This ladder isolates each addition. The core plan's B bundles belief and bandit, which hides whether the bandit does anything (see 3). |
| Commitment length | posture change ≤ 1 per 6 months | 24 months (36 for war templates) | wireframe shows 12-month commitment, 5-year outcome horizon | 24 months commitment; reward window = commitment window. Delete the 6-month posture cooldown; the commitment already covers it. |
| Modes | Dev (live `run`) vs Release (save-parse out, between-session in, Ironman OK) | not addressed | only live/replay/synth adapters | Add `release` as an adapter mode in the dev plan and a `BetweenSessionEmitter` in the core. Otherwise the only mode anyone will ever test is the one that needs the console. |
| Run file name | `sae_in.txt` | n/a | n/a | **`sae_in.ini`.** The wiki states the file must use the INI extension (verified this pass). |

## 2. Load-bearing assumptions, ranked by risk × cost-if-wrong

1. **Knob efficacy: budgets, subplan flags and ship-size variables move AI behaviour enough to matter.** Highest risk, highest cost: if false, there is no product. Cheapest test: integration plan M0-7/M0-8 (one knob, one AI, A/B in `observe`, same seed, 15 years). Do this *before* writing any engine code. Fallback: between-session defines and full economic-plan overrides, which means per-campaign rather than live adaptation.

2. **StarNet exists for 4.4.** The GitHub tree is 3.10/3.11. Stellaris 4.0 rebuilt pops, jobs and districts into zones (the 4.4.4 notes even talk about a building's "primary zone"), so StarNet's economic plans and job-based triggers from 3.x are almost certainly broken, not merely stale. If no maintained 4.x fork exists, the `starnet` adapter profile and the StarNet-derived ship-mix knob both evaporate. Also: StarNet has no license, so nothing can be vendored, only driven. Cheapest test: M0-1 (look at the installed Workshop build). Fallback: vanilla profile only; design SAE's own `script_values` for ship mix.

3. **Console `run` as the live input channel.** Verified as a command; unverified for `effect every_country` scope, `.ini` vs `.txt`, and whether it runs mid-tick without stutter. Cost if wrong: live mode collapses to between-session. Test: M0-6, 30 minutes. Fallback: `event sae.50` with payload pre-staged in global variables.

4. **Keystroke injection.** Fragile, layout-dependent, and the dev plan builds its whole benchmark harness on it. Cost if wrong: no unattended A/B runs, which is how you prove anything. Test: M0-10. Fallback: the player presses a hotkey (dev mode), and benchmarks run with longer horizons and fewer, manually started runs. Honest recommendation: hotkey-only in v0, injection as an opt-in later.

5. **`log` effect writes to `logs/game.log` and flushes within seconds.** Exists in StarNet's code; not on the wiki Effects page (I read all of it). Variables printing via `[This.var]` is documented (Variables page), which de-risks M0-5. Cost if wrong: observation cadence drops to autosave frequency, which may be fine. Test: M0-3/4. Fallback: monthly autosave plus parse.

6. **Save-parse latency ≤ 3 s and the fields you need are in the save.** jomini handles Stellaris; latency is unmeasured. Low-to-medium risk; the fields (fleets, starbases, resources, tech) are all in the gamestate. Test: M0-11. Fallback: parse a subset of sections.

7. **Scripted AI profiles can express player archetypes for calibration.** Medium risk, and it is circular: fit the detector on vanilla AI wearing a "turtle" flag, then test it on the same. A human turtle looks different from a budget-tweaked AI turtle. Test: run one profile, parse the save, and eyeball whether `choke_fort` and `expansion_rate` separate from a default AI. Fallback: label 5–10 of your own campaigns by hand (you know how you played) and use them as the calibration set.

8. **Fog-of-war features are computable.** The integration plan's `SNAP`/`PLYR` lines emit god data for everyone; the core plan forbids the model from seeing it. Nobody owns the mask. A true mask is per (observer AI, target) intel level. Fallback (recommended for v0): a "diplomacy-screen legal" approximation — relative power categories, visible borders, observed starbases in bordering systems — computed from the save, with the strict mask deferred.

Good news from the lookups: `game_speed <n>` and `game_paused` are real console commands (the dev plan marks them [H] not found), so the harness does not need speed keystrokes. Bad news: there is no `save` console command on the wiki; the integration plan's channel 3 ("`run` a file containing `save sae_snap`") is probably impossible. Use autosaves.

## 3. Over-engineering, cuts, and what is missing

**Dev environment.** Rerun plus TensorBoard plus Phoenix plus OTel spans plus DuckDB plus a Parquet compactor plus a FastAPI/Preact SPA with a wireframe, a two-OS CI matrix, Git LFS fixtures, Minari export and d3rlpy. For one person, that is a month of plumbing before the first decision is made. Keep: JSONL bus with a pydantic envelope, DuckDB queries over it, the ReplayAdapter, the SynthAdapter, unit tests on the parsers and the Bayes update. Pick **one** viewer (Rerun, because it needs zero frontend code) and defer the SPA to whenever Rerun actually blocks you. Drop TensorBoard (duplicates Rerun scalars), Phoenix and OTel (no LLM in v0), Parquet compaction (a 200-year campaign is tens of MB of JSONL), Minari/d3rlpy (there is no offline RL plan that survives contact with 70 decisions per campaign), the Windows CI leg (test on your own PC), and the control plane beyond `pause` and `force_strategy`.

**Engine core.** Seven archetypes × three phases × nine features, tempered naive Bayes with fitted τ and ρ, CUSUM, Normal-Inverse-Gamma contextual Thompson sampling with LCB safety floor, exploration budget, hysteresis, per-player Dirichlet profile, CMA-ES over θ, a PSRO league option, and a promotion pipeline with four gates. The arithmetic kills most of this: a commitment of 24 months yields roughly 100 bandit pulls per 200-year campaign, split across 10–20 arms and a 15-dimensional context. The within-campaign bandit will never leave its prior. **In v0 the "bandit" is a lookup table from belief to counter-template.** The bandit earns its place only across campaigns (condition C), and CMA-ES only once a benchmark harness exists. Start with three archetypes (turtle, aggressor, builder), four or five save-derived features, a plain forgetful Bayes filter with a fixed ρ, and the commitment timer. Keep the safety floor as a rule ("never below baseline budget share X"), not as an LCB computation. Delete the ≤ 50 ms p99 tick target; the game tick is seconds and the engine runs out of process.

**Integration layer** is the plan closest to right-sized. Trim M0 to the six tests that gate everything (1, 2, 3+5 merged, 6, one of 7/8, 11). Drop the `vanilla`/`starnet` dual profile until M0-1 tells you which one you actually have.

**Missing: the influence ceiling.** None of the plans says what the engine cannot change, and this determines the honest pitch. Hardcoded: fleet pathing and target selection, engagement logic, war declaration timing (weights are data, the loop is code), ship design beyond size mix (auto-designer), crisis and fallen-empire behaviour. Scriptable live: budget shares, econ subplan selection, ship-size mix, starbase defense toggles, opinion modifiers, policies the AI may revert, rally/defend flags StarNet-style. So against a chokepoint turtle the engine can make the AI *bigger, earlier, with longer-range ships* and *not* make it route around or siege intelligently. It changes **when** and **with what** the AI fights, not **where** or **how**. The honest pitch is "a strategic director that reshapes the AI's economy, fleet doctrine and posture in response to how you play", not "an AI that outmanoeuvres you". Say that on the mod page and users will not feel cheated.

**Also missing:** (a) who the "self" is — there are N AI empires with different views; the core plan models one self versus one opponent. V0 should manage only AIs in contact with the player, with one belief about the player shared across them. (b) Legibility delivery: the engine cannot push text into the game; `legible_reason` must be pre-authored localisation keys selected by an enum the run file sets. (c) Country id in log lines: `[This.GetID]` is unverified and I doubt it exists for countries; emit `[This.GetName]` and map names to ids via the save.

## 4. Does the LLM layer earn its place in v0? No.

The core plan's own hypothesis 3 predicts the advisor does not improve strength. Its gate fires rarely, its output is a capped prior bump on an arm the table would have picked anyway, and the whole llama.cpp/Ollama/PydanticAI/Phoenix stack exists to support it. CivBench says frontier models under-monitor and do not execute plans; the plans fix that by taking monitoring and execution away from the model, at which point what is left for it to do is name an archetype from six numbers, which a Bayes filter does for free. SwarmBrain is cited for the split architecture, but it is an LLM making *frequent* macro decisions over a full API against built-in AI, not evidence that a *rare* advisor over a budget knob helps. Cut it from v0 and v1; revisit at M4 as condition D with a real A/B.

Where Claude (cloud, offline, in the developer loop) does earn its place now:
- **Mod code generation and review**: Clausewitz script, `script_values`, `ai_budget` entries, the on_action hooks, and the `.ini` compiler. Verify every effect name against the 4.4 wiki or StarNet, because models hallucinate Paradox syntax confidently.
- **Save-parse code**: jomini extraction for the ten fields you need, plus fixtures.
- **Replay analysis**: feed a campaign's bus and yearly save summaries, ask "what did the player do, when did it change, which decisions mattered". This is the cheapest way to author and refine archetype features and strategy templates.
- **Labelling** observer runs and your own campaigns for calibration ground truth, as CivBench used Haiku to label commitments.
- **Authoring** the legible-reason localisation strings and strategy template θ defaults.
- At runtime, never for decisions in v0. A later "commentator" that writes flavour text from the audit log is harmless and fun; keep it off the decision path.

## 5. Recommended v0 scope and milestones

Scope: single-player, non-Ironman, vanilla 4.4 AI (unless M0-1 finds a working StarNet 4.x fork), hotkey-driven `run`, save-parse plus optional log channel, three archetypes, three or four knobs, rule table, 24-month commitment, in-game flavour event on switch, JSONL bus and Rerun. No LLM, no bandit, no profile, no SPA.

- **M0 Feasibility (one weekend).** Six tests above. Exit: a script-emitted line reaches Python within 5 s; a `.ini` run file changes a variable on a chosen AI and is acked; one knob produces a visible A/B difference in 15 years of `observe`; a 4.4 autosave parses in under 3 s.
- **M1 Puppet (2–3 weeks).** Mod plus engine with a CLI: `sae set <country> posture offensive` → run file → ack → bus event. Save parser exporting ten fields. Exit: round-trip latency measured; three knobs demonstrated; bus replays through the ReplayAdapter.
- **M2 Detector (2–3 weeks).** Save-derived features, three-archetype Bayes filter, calibration on five hand-labelled campaigns of your own plus two scripted profiles. Exit: top-1 accuracy clearly above chance by year 30, a belief timeline in Rerun, and a written note on which features did the work.
- **M3 First playable (2–3 weeks).** Belief → counter-template table, commitment, legible flavour event, hotkey workflow, mod page text with the honest pitch. Exit: one full campaign without crash, every strategy switch explainable from the audit, and you can feel the difference.
- **M4 Evidence (ongoing).** `observe`-mode paired A/B harness using `game_speed`/`game_paused`, ≥ 10 paired runs per archetype, report with CIs. Then and only then decide whether the bandit (C) and advisor (D) earn their complexity.

Top three decisions you must make now:
1. **Baseline: vanilla 4.4 or a StarNet 4.x fork.** Depends on M0-1 and on whether a fork exists with a license you can live with.
2. **Input channel policy: hotkey-only dev mode, or ship keystroke injection.** This decides whether benchmarks can run unattended, and whether users see the mod as a tool or as automation.
3. **Fairness level in v0: strict per-AI intel mask, or the "diplomacy-screen legal" approximation.** Strict is the right principle and roughly doubles the mod and parser work; the approximation gets you to a playable sooner with a documented caveat.

## 6. Accuracy check: claims I believe are wrong or overconfident

- Integration §2 channel 3 and dev §5 step 5: a `save` console command. Not on the wiki; `check_save` is the only similar entry. Treat as absent.
- Integration §3.1: run file `sae_in.txt`. Wiki says `.ini` extension.
- Dev plan preamble and §5: speed and pause commands "not found". They exist: `game_speed [n]`, `game_paused`.
- Integration §0 and core §1: StarNet as a usable baseline for 4.4. Overconfident given the 4.0 economy rework; see assumption 2.
- Core §1 and §4: fleet composition by role, and `PRIORITIZE_TECH` via `ai_weight`. Neither has a verified live hook; the first contradicts the only verified knob.
- Core §3.2: contextual Thompson sampling learning anything within one campaign. Sample-size arithmetic says no.
- Core §4: SwarmBrain as support for a rarely-called advisor. It supports the planner/executor split, not rare advice.
- Core §1 and dev plan §1: 1–2 strategic decisions per month "recommended" by the frontier-agents note. That note labels it an inference, not evidence.
- Dev §5: 30–90 minutes per 100-year run at max speed. Optimistic for a standard galaxy; mid-game slows sharply. Use small galaxies and fewer empires for benchmarks.
- Integration §2: `[This.GetID]` for countries. Unverified and doubtful.
- Dev §3: an engine-side unit test "enforces" that god data never reaches the engine. A test on one process boundary is a convention, not an enforcement; it is fine, just do not call it a guarantee.
- Core §2.1: `fleet_conc` and `fleet_near_border` "computed per month". Fleet sightings are not cheaply scriptable and the save gives god positions; these features need the mask decision from section 5 before they can be claimed.

What the plans get right and should keep: the typed Intent → validator → compiler pipeline with acks and sequence numbers; additive-only modding with a unique prefix; the fog-of-war principle and Alien: Isolation framing; commitment and telegraphing; the ReplayAdapter and SynthAdapter idea; and the discipline of labelling every claim VERIFIED or HYPOTHESIS, which made this review possible.
