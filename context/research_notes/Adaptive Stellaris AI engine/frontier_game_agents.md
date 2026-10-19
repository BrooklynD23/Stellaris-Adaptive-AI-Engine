# Frontier Game-Playing Agents: Evidence and Transfer to a Symbolic Grand-Strategy Game (Stellaris)

Research date: 2026-10-18. Scope: evidence on LLM/agentic game players, weighted toward 2024-2026, with transfer assessment for a symbolic 4X/grand-strategy game driven by an external process. Items marked **[unverified]** were not checked against the primary text in this session. Items marked **[memory]** are foundational work recalled from training data, not re-fetched; treat their numbers as needing a check before citation.

Confidence scale: High = primary source fetched and claim checked; Medium = abstract or secondary source only; Low = recalled or single secondary source.

---

## 1. What SIMA 2 demonstrated, and newer DeepMind work

### Takeaway
SIMA 2 (Nov 2025) is a Gemini-based embodied agent that reasons about instructions and improves through a Gemini-labelled self-play loop, but the primary blog gives no quantitative success rates and its inputs are still pixels and keyboard/mouse. Its generalization claims are demonstrated qualitatively in new games and Genie 3 worlds. Newer 2026 DeepMind game-agent work could not be verified from a primary source in this session.

### Cited Findings
- SIMA 2 is a Gemini-powered agent that "reason[s]" about instructions and can describe its intended actions; a Gemini model is embedded as the agent's core — [DeepMind blog](https://deepmind.google/blog/sima-2-an-agent-that-plays-reasons-and-learns-with-you-in-virtual-3d-worlds/) (fetched; High for the claim as stated by DeepMind).
- Initial training used human demonstration videos with language labels plus Gemini-generated labels; after that, agents learn new games "exclusively through self-directed play" — [DeepMind blog](https://deepmind.google/blog/sima-2-an-agent-that-plays-reasons-and-learns-with-you-in-virtual-3d-worlds/) (High, as stated).
- Self-improvement mechanism (as described by the blog's figure caption): Gemini supplies an initial task and an estimated reward for the agent's behavior; resulting episodes go into a bank of self-generated experience used for later training generations. The blog says the agent improves on previously failed tasks "entirely independently of human-generated demonstrations and intervention" — [DeepMind blog](https://deepmind.google/blog/sima-2-an-agent-that-plays-reasons-and-learns-with-you-in-virtual-3d-worlds/) (High for what is claimed; the blog gives no detail beyond the caption, so the loop is not reproducible from it).
- Generalization: evaluated on games not trained on (ASKA, MineDojo), and in Genie 3-generated 3D worlds, where DeepMind says it could "sensibly orient itself" and act toward goals — [DeepMind blog](https://deepmind.google/blog/sima-2-an-agent-that-plays-reasons-and-learns-with-you-in-virtual-3d-worlds/) (High as a claim; qualitative, no numbers).
- Performance: the blog states SIMA 2 is "significantly closer to that of a human player" than SIMA 1 on an "expanded, and much more difficult" evaluation set, but gives no percentages in the text; success rates appear only in charts/captions — [DeepMind blog](https://deepmind.google/blog/sima-2-an-agent-that-plays-reasons-and-learns-with-you-in-virtual-3d-worlds/) (High that numbers are absent from text; Medium on the claim).
- Stated limitations: struggles with very long-horizon complex tasks; "relatively short memory of its interactions" due to limited context window; precise low-level keyboard/mouse control and robust visual 3D understanding remain open; described as "fundamentally a research endeavor" with research-preview access only — [DeepMind blog](https://deepmind.google/blog/sima-2-an-agent-that-plays-reasons-and-learns-with-you-in-virtual-3d-worlds/) (High).
- Release date reported by TechCrunch as 2025-11-13 — [TechCrunch](https://techcrunch.com/2025/11/13/googles-sima-2-agent-uses-gemini-to-reason-and-act-in-virtual-world) (URL/date from search result; Medium).
- Genie 3 (world model) announced 2025-08-05 per TechCrunch headline — [TechCrunch](https://techcrunch.com/2025/08/05/deepmind-reveals-genie-3-a-world-model-that-could-be-the-key-to-reaching-agi) (headline only; Low-Medium).
- 2026 secondary coverage (StartupHub) describes SIMA 2 as a conversational companion playing No Man's Sky, Valheim, Hydroneer, and an EVE Universe research partnership (EVE Online, EVE Vanguard, EVE Frontier) starting in offline instances, linking a DeepMind post titled "From Atari to EVE Online" — [StartupHub](https://www.startuphub.ai/ai-news/ai-research/2026/deepmind-s-game-ai-evolves.md) (Low: secondary, no dates or metrics, and the article contains claims that look unsupported, e.g., a "Genie" FAQ item not in the main text and benchmark scores of unknown provenance).

### Inferences
- The self-improvement loop has two parts that matter for a non-visual game: (a) a model that proposes tasks and scores episodes, and (b) a replay bank that feeds later training. For Stellaris the analogue is an LLM or heuristic that proposes objectives and scores outcomes from state snapshots; the training half is not needed if the policy is hand-built.
- SIMA 2's gains are on pixel-based 3D games. Nothing in the primary blog shows the loop working on symbolic state, so it should not be cited as evidence that self-improvement transfers to Stellaris.

### Gaps
- No primary 2026 DeepMind game-agent paper or blog was retrieved. The EVE/"From Atari to EVE Online" post was not fetched and is unverified.
- No success-rate numbers for SIMA 2 vs SIMA 1 or vs humans were obtainable from the text.
- No SIMA 2 technical report was retrieved to confirm the loop details.

---

## 2. LLM agents in Minecraft and similar open-world games

### Takeaway
The Minecraft line (Voyager, JARVIS-1, Optimus-1, MineDojo) establishes three reusable mechanisms: an executable skill library, an automatic curriculum, and memory plus self-reflection. Their evidence is strongest for tech-tree progression in an open world; they are not evaluated on a bounded, symbolic, multi-agent strategy game. Skill libraries and reflection are the most plausible transfers to Stellaris, but transfer is unproven.

### Cited Findings

**Voyager (2023)**
- Three components: automatic curriculum for exploration, a growing skill library of executable code, and iterative prompting using environment feedback, execution errors, and self-verification; works with GPT-4 via black-box queries without fine-tuning — [arXiv 2305.16291](https://arxiv.org/abs/2305.16291) (fetched; High for description).
- Reported results vs prior SOTA: about 3.3x more unique items, about 2.3x longer distances, tech-tree milestones up to 15.3x faster — [arXiv 2305.16291](https://arxiv.org/abs/2305.16291) (High as reported by authors).
- Venue: NeurIPS 2023 per [NeurIPS page](https://neurips.cc/virtual/2023/79179) (Medium; the arXiv abstract page did not list venue).
- The arXiv page does **not** give numeric comparisons to ReAct, Reflexion, or AutoGPT in the text retrieved; these were not verified (Gap). [memory] Voyager's paper reports such baselines as failing to generalize to new worlds; check the paper's table before citing.
- Code: a GitHub repo exists (search results show a mirror); license not verified **[unverified]**.
- Compute: GPT-4 API calls per iteration; per-game cost and latency not retrieved (Gap).
- Failure modes: not retrieved from the abstract page (Gap).

**JARVIS-1 (2023)**
- Built on pre-trained multimodal LMs that turn visual observations and instructions into plans, executed by goal-conditioned controllers, with a multimodal memory combining pre-trained knowledge and in-game experience — [arXiv 2311.05997](https://arxiv.org/abs/2311.05997) (fetched; High for architecture).
- Claims 200+ Minecraft tasks; near-flawless on short tasks; about 5x reliability vs prior SOTA on ObtainDiamondPickaxe. No absolute success rates in the excerpt (Medium; abstract claim).
- Venue/version: arXiv 2311.05997v3, Nov 2023; no venue listed on the page (High).
- Relevance: the memory-based planner is visual-input at the top; the executor is a controller. The memory design is symbolic-friendly.

**Optimus-1 (2024)**
- Attempted fetch of arXiv 2308.12735 and 2308.03028 returned unrelated papers (molecular structures; HVAC GPT-4 control). The Optimus-1 paper ID was not confirmed. **[unverified]** Optimus-1 is described in memory as a hierarchical directed knowledge graph, hybrid multimodal memory, expert-policy executor, and reflective self-improvement (NeurIPS 2024). Confirm the paper and numbers before citing.

**MineDojo (2022)**
- [memory] A NeurIPS 2022 Datasets & Benchmarks paper providing a large Minecraft task suite and internet-scale video/wiki data. Not fetched this session. Gap.

### Inferences
- Skill libraries (Voyager) map well to Stellaris as stored, parameterised command sequences (e.g., "consolidate a sector," "fortify border") that are verified against game state before reuse. This is a plausible transfer; no grand-strategy paper has been retrieved that demonstrates it.
- Reflection and memory: Optimus-1 and JARVIS-1 both use memory of past episodes. In Stellaris, the transferable part is structured memory of past decisions and their outcomes (text or table), not image memory.
- Automatic curriculum (Voyager) is less relevant: Stellaris has no need to discover a tech tree with exploration pressure; the curriculum is the game's own objectives.
- The Minecraft evidence is mostly single-agent, unbounded-horizon exploration; Stellaris is multi-agent, adversarial, and driven by economic and diplomatic state. Expect smaller transfer.

### Gaps
- No primary-source numbers for Voyager baselines (ReAct, Reflexion, AutoGPT) were retrieved.
- Optimus-1 primary source not verified.
- MineDojo and any 2025-2026 Minecraft agent work (beyond SIMA 2) were not surveyed in depth.

---

## 3. LLM agents in strategy games (4X, RTS, diplomacy)

### Takeaway
2024-2026 evidence for LLMs in 4X is thin and largely negative. CivBench (Sept 2026) is the most relevant recent result: frontier models won 3 of 23 runs in Civilization VI, with failures attributed to under-monitoring of state and a gap between stated plans and actions. StarCraft II text-based LLM agents beat built-in AI at mid-to-hard levels with several-second to multi-second inference, showing the strategic-layer-plus-scripted-reflex design works in an RTS. CICERO shows strong Diplomacy performance on a human league, but at a cost of heavy engineering and a non-LLM planner.

### 3a. CivBench (2026) — most relevant recent 4X benchmark
- Benchmark: CivBench, a long-horizon benchmark for tool-mediated agents in Civilization VI, open source, released through MCP — [arXiv 2609.02459](https://arxiv.org/pdf/2609.02459) (fetched; High for stated facts; submitted 2 Sept 2026 per alphaXiv listing).
- Interface: FireTuner protocol over TCP; 76 MCP tools (state queries 27, unit actions 10, governance 10, city management 9, lifecycle 8, diplomacy 7, religion/culture 5); a 29-function narration layer turns visual game state into structured text; agent sees only what it queries — [arXiv 2609.02459](https://arxiv.org/pdf/2609.02459) (High).
- Results (23 admissible runs): Claude Opus 4.6 2/4 GC wins/defeats, 0/2 SF; GPT-5.4 0/6, 0/2; Gemini 3.1 Pro 1/5; Kimi K2.5 one exploratory run. Total wins 3/23, all tech victories on Ground Control. Fisher's exact p = 0.488 — no significant difference — [arXiv 2609.02459](https://arxiv.org/pdf/2609.02459) (High; the authors call it a pilot, not a ranking).
- Cost and latency: about $31–229 API cost per full game; 2–8 hours wall-clock per game on a consumer machine; 300+ turns and thousands of tool calls per game; 5–15 tool calls per turn — [arXiv 2609.02459](https://arxiv.org/pdf/2609.02459) (High).
- Failure modes: (1) under-monitoring ("sensorium effect") — state available but not queried; agents checked victory progress every 30–75 turns vs a recommended 20; in 7 of 20 detectable defeats, no victory query in the 20 turns before loss; (2) reflection–action gap: many near-term commitments in reflections not executed within 10 turns; (3) delayed-payoff lag: no model reached the playbook benchmark of 3 cities at T50 (Claude 2.83, GPT 2.83, Gemini 2.67, Kimi 2.00) — [arXiv 2609.02459](https://arxiv.org/pdf/2609.02459) (High).
- Caveats stated by authors: small uneven sample; shared playbook is a confound; no random or scripted baseline; only 21% of pre-harness runs reached natural conclusion; labeller shares model family with one evaluated agent (Claude Haiku 4.5 labelled commitments, 92% agreement, κ = 0.879) — [arXiv 2609.02459](https://arxiv.org/pdf/2609.02459) (High).
- Code: stated as open source; repository not checked **[unverified]**.

### 3b. CivRealm (ICLR 2024)
- Civilization-inspired environment for learning and reasoning; both RL ("tensor") and LLM ("language") agent interfaces; the abstract describes the game as "imperfect-information general-sum game with a changing number of players" — [arXiv 2401.10568](https://arxiv.org/abs/2401.10568) (abstract fetched; High for abstract claims). Venue ICLR 2024 per [ICLR proceedings](https://proceedings.iclr.cc/paper_files/paper/2024/hash/76d2f8e328e1081c22a77ca0fa330ca5-Abstract-Conference.html) (High).
- Findings: RL agents show reasonable performance in mini-games; LLM agents evaluated but no models or numbers in abstract; both paradigms "struggle to make substantial progress" on the full game — [arXiv 2401.10568](https://arxiv.org/abs/2401.10568) (High for the claim, Medium for detail since abstract only).
- Code: repo github.com/bigai-ai/civrealm and PyPI package civrealm 0.1.1 appear in search results (Medium).
- Gap: model names, win rates, and failure modes not retrieved; full paper needed.

### 3c. SAG-Agent (Oct 2025)
- Agent on Civilization V (Prince, Earth, Russia) and Slay the Spire, using screenshots only (CLIP encoding, Segment Anything for UI elements, template matching), with a state-action graph memory and GPT-4o reasoning — [arXiv 2510.15259](https://arxiv.org/pdf/2510.15259) (fetched; High for description).
- Civ V results (3 episodes each): SAG-Agent 107.33 ± 6.55 turns survived, 13.33 ± 1.25 technologies; BottomUp baseline 59.67 turns, 8.66 techs; GPT-4o prior-assisted 14.67 turns, 1 tech — [arXiv 2510.15259](https://arxiv.org/pdf/2510.15259) (High as reported). Cost figures (2.15–3.03) are in unstated units **[unverified]**.
- Ablation: removing similarity-based state merging dropped Civ V from 65 to 15 turns in a 100-step setting — [arXiv 2510.15259](https://arxiv.org/pdf/2510.15259) (High as reported).
- Caveats: only 3 episodes per game; "statistically significant" claimed without a test; authors say knowledge "remains tied to specific environments" and generalizes poorly; no code release mentioned — [arXiv 2510.15259](https://arxiv.org/pdf/2510.15259) (High).
- Relevance: screenshot-based — not needed for Stellaris. The graph-of-states memory idea is still transferable in symbolic form.

### 3d. SwarmBrain / StarCraft II with LLM strategic layer (2024)
- Architecture: an LLM "Overmind" sets macro strategy from text; an "SC2 Brain" converts strategies to commands of form (Unit, Location)->(Action)->(Target); a text memory stores past strategies; a Command Center parses commands with regex and holds infeasible ones until prerequisites are met. A non-LLM rule-based state-machine "Swarm ReflexNet" handles fast micro — [arXiv 2401.17749](https://arxiv.org/pdf/2401.17749) (fetched; High for description).
- Setup: Zerg vs built-in Terran AI on "Automaton LE"; difficulty Very Easy to Hard. Results: 100% win at Very Easy through Medium Hard; 76% (23/30) at Hard — [arXiv 2401.17749](https://arxiv.org/pdf/2401.17749) (High as reported; single map).
- Latency: first LLM inference ~10 s before match start; GPT-4 about 3 inferences per game minute vs about 6 for GPT-3.5; GPT-4 roughly 2x inference time. Token/monetary cost not reported — [arXiv 2401.17749](https://arxiv.org/pdf/2401.17749) (High).
- Failure modes at Hard (7 losses): 3 command execution failures, 2 economic missteps, 2 poor army compositions; hallucinated or ambiguous commands (nonexistent upgrade, vague coordinates); positional errors; no visual input so misses wall-offs; individual micro unreliable so group commands used — [arXiv 2401.17749](https://arxiv.org/pdf/2401.17749) (High).
- Model naming in paper is inconsistent ("gpt-4.5-turbo", "gpt-4.0-turbo") — noted by fetch summary (Medium).
- Code: not verified **[unverified]**.

### 3e. TextStarCraft II / "Large Language Models Play StarCraft II" (2023-2024)
- Text-based SC2 environment; LLM agents with a Chain of Summarization (single-frame and multi-frame summarization) — [arXiv 2312.11865](https://arxiv.org/abs/2312.11865) (abstract fetched; High for description).
- Claims: LLM agents can beat built-in AI at the Harder (Lv5) level; human experts judged performance close to an average player with about eight years of experience. The abstract gives no win-rate numbers, models, latency, or cost (Medium; abstract only).
- Relevance: the text-observation interface is the closest published analogue to a symbolic Stellaris state feed.

### 3f. CICERO (Diplomacy, Science 2022)
- Results from Meta AI page: 40 games vs 82 anonymous humans on webDiplomacy.net (Aug–Oct 2022); average 130 messages per game; more than 2x the average score of opponents; top 10% of participants who played more than one game — [Meta AI CICERO page](https://ai.meta.com/research/cicero/diplomacy/) (fetched; High as reported by Meta). Note the source is the vendor's own page.
- Architecture (dialogue model + strategic planning with a piKL-regularized planner) is from memory **[memory]**; the Science DOI fetch failed, so this was not verified in this session.
- Stated limitations not retrieved.
- Relevance: a diplomacy/negotiation layer, not a 4X economy. Heavy engineering for a dialogue-first system; a Stellaris AI empire does not need natural language negotiation.

### 3g. PokéLLMon (2024)
- Not searched or fetched in this session. Gap. [memory] An LLM-based Pokémon battle agent using knowledge retrieval and action consistency; not a 4X game. Low relevance.

### 3h. AlphaStar (2019)
- [memory] DeepMind's AlphaStar (Nature 2019) reached Grandmaster level in StarCraft II via league-based self-play, with supervised pretraining on human replays and a transformer-based policy over raw interface features. Not fetched in this session; verify numbers before citing. Relevance: league-based self-play is an RL method for an RTS with a large action space and is the reference for "training a strong agent" versus LLM-based planning. It requires a training budget far beyond LLM inference.

### Inferences
- Across LLM 4X and RTS attempts, the consistent bottleneck is state monitoring and commitment, not generation: CivBench shows frontier models have state available and still under-query it. For Stellaris, the external process should push state summaries and trigger alerts rather than rely on the agent to poll.
- Cost and latency: CivBench's per-game cost ($31–229) and multi-hour wall clock indicate that a per-turn LLM call inside Stellaris would be expensive for every decision; a low-frequency strategic layer with scripted or RL control at the tactical layer is the realistic design.
- The text-observation and planner-plus-scripted-executor designs (SwarmBrain, TextStarCraft II) are most transferable; screenshot agents (SAG-Agent) are not needed.

### Gaps
- No 2025–2026 LLM benchmark on Stellaris itself was found.
- No win-rate data for LLM agents vs Stellaris AI or vs Paradox AI.
- CivRealm LLM results and specific models not retrieved.
- PokéLLMon and AlphaStar numbers not verified this session.

---

## 4. Hierarchical designs: LLM planner + conventional or RL executor

### Takeaway
Multiple independent results support a split architecture: an LLM handles infrequent strategic decisions, a deterministic or RL controller handles frequent low-level execution. The best-documented example in this ledger is SwarmBrain (LLM strategy, rule-based micro). Optimus-1 and JARVIS-1 use a similar split in Minecraft. Evidence is strongest for RTS execution; it is weaker for long-horizon economic play.

### Cited Findings
- SwarmBrain: LLM strategic layer + non-LLM rule-based micro layer; 76% win vs Hard built-in AI; failure modes are command-execution and economy, not micro — [arXiv 2401.17749](https://arxiv.org/pdf/2401.17749) (High).
- CivBench: the LLM is the only decision layer; the tool set (76 MCP tools) is itself the executor; failures are at strategy monitoring, not at tool use — [arXiv 2609.02459](https://arxiv.org/pdf/2609.02459) (High).
- JARVIS-1: pretrained multimodal LM planner + goal-conditioned controllers — [arXiv 2311.05997](https://arxiv.org/abs/2311.05997) (High).
- SAG-Agent: LLM reasoning over a graph of learned skills; execution remains screen-based — [arXiv 2510.15259](https://arxiv.org/pdf/2510.15259) (High).

### Inferences
- The split matches Stellaris well: strategy (diplomacy, expansion priority, war targets) at 1–2 decisions per in-game month, and tactical actions (fleet moves, colonization orders) handled by heuristics or a learned policy.
- The CivBench failure of under-monitoring argues for the external process to own state-triggered replanning, not the LLM.

### Gaps
- No controlled comparison (LLM-only vs hybrid) was found on a 4X game.
- No evidence was found on the optimal LLM call frequency for a turn-based 4X.

---

## 5. Which approaches need vision or keyboard, versus symbolic state

### Takeaway
Stellaris does not need vision or keyboard control. The most relevant evidence (CivBench, SwarmBrain, TextStarCraft II, CivRealm language agents) uses symbolic or text state. Vision-based and keyboard-based systems (SIMA 2, SAG-Agent, JARVIS-1) add engineering burden without benefit for this task, except where they demonstrate general reasoning ideas.

### Cited Findings
- Symbolic/text-state systems: CivBench (76 MCP tools, text narration of game state) [arXiv 2609.02459](https://arxiv.org/pdf/2609.02459); SwarmBrain (python-sc2 state converted to text, no visual input) [arXiv 2401.17749](https://arxiv.org/pdf/2401.17749); TextStarCraft II [arXiv 2312.11865](https://arxiv.org/abs/2312.11865).
- Vision/keyboard systems: SIMA 2 (pixels, keyboard/mouse; precise control still open) [DeepMind blog](https://deepmind.google/blog/sima-2-an-agent-that-plays-reasons-and-learns-with-you-in-virtual-3d-worlds/); SAG-Agent (screenshots, CLIP, SAM) [arXiv 2510.15259](https://arxiv.org/pdf/2510.15259); JARVIS-1 (visual observations) [arXiv 2311.05997](https://arxiv.org/abs/2311.05997).
- Interface cost for symbolic play: CivBench's FireTuner protocol supports only one connection, limiting multi-agent setups — [arXiv 2609.02459](https://arxiv.org/pdf/2609.02459) (High). This is a Civ VI constraint; Stellaris has no equivalent published interface in this ledger.

### Inferences
- For Stellaris, the external-process design can read the save file or game state via a mod/console bridge (engineering question, not researched here) and write back orders. No pixel or keyboard path is needed. This is an inference from the task framing, not verified against Stellaris's modding API.
- The vision-based and keyboard-based results mainly validate general reasoning/memory ideas (SIMA 2's self-directed improvement, SAG-Agent's state graph), not the interface.

### Gaps
- Stellaris modding and console API capabilities were not researched (outside this ledger's scope).

---

## Transfer ledger (summary)

| Entry | Date | Game / setting | Symbolic? | Demonstrated | Compute / latency | Transfer to Stellaris | Confidence |
|---|---|---|---|---|---|---|---|
| SIMA 2 | Nov 2025 | 3D commercial and research games | No (pixels, KB/M) | Qualitative generalization, Gemini-labelled self-improvement | Not given | Low (loop idea only) | Medium |
| CivBench | Sept 2026 | Civ VI (tool-mediated) | Yes (76 MCP tools, text) | 3/23 wins; under-monitoring; reflection–action gap | $31–229 and 2–8 h per game | High (failure modes, tool design) | High |
| SAG-Agent | Oct 2025 | Civ V, Slay the Spire | No (screenshots) | Higher turns and techs than baselines; 3 episodes | Cost unit unstated | Medium (state graph memory) | Medium |
| CivRealm | ICLR 2024 | Civ-inspired | Yes (both RL and LLM interfaces) | LLM and RL struggle on full game (abstract only) | Not given | Medium (benchmark design) | Medium |
| SwarmBrain | Dec 2023 / 2024 | SC2 vs built-in AI | Yes (text) | 100% win to Medium-Hard; 76% at Hard | ~10 s initial; 3–6 inferences per game-minute | High (planner + reflex split) | Medium-High |
| TextStarCraft II | 2023–2024 | SC2 text environment | Yes (text) | Beats Lv5 built-in AI (abstract) | Not given | High (interface analogue) | Medium |
| CICERO | Science 2022 | Diplomacy | Yes (text dialogue) | Top 10% in 40 human games (vendor page) | Not given | Low | Medium |
| Voyager | May 2023 (NeurIPS 2023) | Minecraft | Partly (code action, visual state) | 3.3x items, 15.3x faster milestones | GPT-4 per call | High (skill library, feedback) | High (for claims as reported) |
| JARVIS-1 | Nov 2023 | Minecraft | No (visual) | ~5x reliability on diamond pickaxe | Not given | Medium (memory) | Medium |
| Optimus-1 | 2024 | Minecraft | No (visual) | Not verified | Not verified | Medium (KG and reflection) | Low (unverified) |
| AlphaStar | Nature 2019 | SC2 | No (raw features) | Grandmaster level [memory] | Large training budget | Low (training regime) | Low (memory) |
| PokéLLMon | 2024 | Pokémon battles | Yes | Not searched | Not searched | Low | Low |

---

## Overall gaps for the report writer

- No primary 2026 DeepMind game-agent paper was found beyond SIMA 2 (Nov 2025) and secondary 2026 coverage.
- No LLM benchmark specifically on Stellaris was found; CivBench (Civ VI) and SAG-Agent (Civ V) are the nearest.
- Voyager baselines, Optimus-1, MineDojo, CICERO architecture, AlphaStar and PokéLLMon numbers were not verified in this session.
- Code and license availability: only CivRealm (repo and PyPI) and CivBench (stated open source) were checked; others are unverified.
- Latency and cost are reported in only three papers (CivBench, SwarmBrain, SAG-Agent with unstated units).
