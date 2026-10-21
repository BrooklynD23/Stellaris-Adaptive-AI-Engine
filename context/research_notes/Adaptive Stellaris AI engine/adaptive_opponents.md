# Adaptive Opponents, Player Modeling, and Counter-Strategy Selection

Research date: 2026-10-18. Source quality note: most industry claims come from press coverage and NVIDIA/Wemade marketing pages; where a claim is self-reported or unverified it is flagged. Several fetches of primary Wemade pages returned no content, so Asterion details are thin.

## Key Question 1: What is MIR5 / Asterion and NVIDIA ACE, how does it learn from players, and what is shipped vs. demoed?

### Takeaway
Asterion (MIR5, Wemade Next) is a publicly announced adaptive AI boss built on NVIDIA ACE, first shown at CES 2025. Public sources describe it as changing patterns based on player tactics, but none I could fetch explains what player data is tracked, what the learning method is, or whether it is shipped. Treat it as an announced/demoed feature, not a verified shipped mechanism. NVIDIA's shipped-ish ACE language stack (Nemotron 4B on-device SLM) is documented for dialogue and function calling, not for opponent modeling.

### Cited Findings
- Wemade and NVIDIA announced an AI boss, "Asterion," for MIR5 in collaboration with NVIDIA; the announcement is dated January 2025 — [Wemade news](https://wemade.com/news/en108), [InvenGlobal](https://www.invenglobal.com/articles/19158/wemade-next-to-develop-an-ai-boss-in-mir5-in-collaboration-with-nvidia)
- Wemade's CEO Jeong-soo Park says Asterion "gets smarter with each interaction," so its patterns are meant to be less predictable (example given: a fire-breathing pose followed by a different move). The article does not explain what is tracked or how learning works — [InvenGlobal CEO interview](https://www.invenglobal.com/articles/19159/wemade-next-ceo-jeong-soo-park-shares-insights-on-mir5s-revolutionary-ai-boss)
- Asterion was shown at CES 2025 in NVIDIA's exhibition hall. The article does not say whether it was playable or only presented, and calls MIR5 an "upcoming game" — [InvenGlobal CEO interview](https://www.invenglobal.com/articles/19159/wemade-next-ceo-jeong-soo-park-shares-insights-on-mir5s-revolutionary-ai-boss)
- NVIDIA's ACE for Games page (metadata: created 2025-03-13, published 2026-10-08) lists MIR5 (Wemade Next) as an "AI boss that adapts to player tactics," alongside PUBG Co-Player Characters, inZOI Smart Zois, Total War: PHARAOH advisor, Streamlabs/Inworld sidekick, and Dead Meat. The page says these are "in use or being built" and does not state release status or dates — [NVIDIA ACE for Games](https://developer.nvidia.com/ace-for-games)
- The ACE for Games component list includes: NVIDIA Nemotron ASR (speech recognition, 25 languages, 140M/600M/streaming sizes); Chatterbox TTS and Qwen3 TTS; open-source NVIDIA Nemotron 9B and 4B SLMs "for game-agent tasks"; Qwen3.5 4B multimodal model "for on-device agentic workflows"; Audio2Face-3D and Audio2Emotion-3D; NVIGI in-game inference SDK; Game Agent SDK (Beta); UE5 plugins — [NVIDIA ACE for Games](https://developer.nvidia.com/ace-for-games)
- NVIDIA's on-device SLM is Nemotron-4 4B Instruct, distilled from Nemotron-4 15B and then pruned and quantized. It runs locally on GeForce RTX AI PCs and is also offered as a NIM microservice. It targets role-play, RAG, and function calling so characters can act on player instructions. It was announced at Gamescom in August 2024, and Mecha BREAK was named as running it locally — [NVIDIA blog, SLMming Down Latency](https://blogs.nvidia.com/blog/ai-decoded-gamescom-ace-nemotron-instruct)
- PUBG Ally (KRAFTON + NVIDIA ACE): announced, not live as of October 2025. Testing was planned for early 2026 through PUBG Arcade, starting with English, Korean, and Chinese players. Ally takes text and voice orders, reads the game world in real time, finds loot, drives vehicles, and fights with little or no constant player input. The article notes balance risk if AI teammates become too strong — [Windows Central](https://windowscentral.com/hardware/nvidia/pubg-adds-ai-squadmates-that-listen-loot-and-fight-like-real-players-powered-by-nvidias-ace-tech)
- NVIDIA's first ACE-powered in-game showcase was Mecha BREAK (announced 2024). Coverage describes it as a conversational mechanic NPC, not an opponent-modeling system — [NVIDIA GeForce news](https://www.nvidia.com/en-eu/geforce/news/mecha-break-nvidia-ace-nims-rtx-pc-laptop-games-apps/)

### Inferences
- Nothing I found says Asterion uses an SLM or LLM for its adaptation. "Learns from interactions" could be a classical learner, a policy library, or LLM-driven planning. Because the ACE stack's documented shipping components are speech, dialogue, and animation, the adaptive-pattern logic is most likely separate from the SLM. This is an inference.
- ACE is a presentation and dialogue layer. The opponent-adaptation claim is the part that needs independent verification.
- Any ACE model runs on a GPU-side RTX machine. A CPU-only Stellaris engine cannot use the Nemotron 4B stack as-is. Latency and VRAM budgets in the Nemotron docs make that clear.

### Gaps
- Whether Asterion is shipped, and in which build (Korean vs. global MIR5 launch) — no post-2025 source found. Wemade's own news pages returned no content on fetch.
- The data signals Asterion uses (tactic classes, damage patterns, dodge timing, etc.) — not public.
- Whether adaptation is per-session or cross-session, and whether it persists across sessions — not public.
- No independent evaluation of any ACE adaptive boss exists in what I could find.

---

## Key Question 2: Other shipped or researched adaptive opponents (AI Director, Alien: Isolation, DDA, Drivatar, Nemesis, PUBG Ally / inZOI)

### Takeaway
Shipped adaptive systems mostly work by tracking coarse, observable player state (team performance, pacing, proximity, line of sight, supply levels) and adjusting spawns, behaviour unlocks, or difficulty. Alien: Isolation is the strongest published design reference for fair, feel-smart adaptation: the AI reads the Director's state but must find the player itself. Drivatar is the best-documented example of learning from player data, but the public detail is thin.

### Cited Findings
- **Left 4 Dead AI Director (Valve).** Per a Valve designer interview (Kim Swift), the Director selects map configurations based on how well the team is doing, controls weather, manages horde structures and ambient mobs, and can send aggressive hordes to control pacing. The stated goal is horror-film-like pacing: quiet stretches that build tension before release. The article describes no intensity metric and no spawn formula — [Game Developer, Swift interview, 2009](https://www.gamedeveloper.com/game-platforms/q-a-valve-s-swift-on-i-left-4-dead-2-i-s-production-ai-boost). Original GDC talk by Michael Booth was not retrieved; gap.
- **Alien: Isolation (Creative Assembly).** Two-layer design. The Director tracks the player and maintains a "menace gauge" that rises with physical proximity, line of sight, and how fast the alien could reach the player. Once menace peaks, the Director pulls the alien back. The alien runs behavior trees of 100+ nodes; some branches unlock over progression, and unlock conditions never come from player deaths, so players cannot learn a hidden exploit. Some unlocks are time-based so the alien keeps pace with a player who improves. The Director knows where the player is, but the alien must work it out. Searching is deliberately imperfect (suboptimal order, backtracking), which creates an impression of doubt. Designers mark areas the alien never checks to avoid unfair situations. Creative Assembly's Andy Bray says the alien never cheats; it teleports only twice in a 12–18 hour campaign, both for cutscenes — [Tommy Thompson, Game Developer, 2017-10-31](https://www.gamedeveloper.com/design/the-perfect-organism-the-ai-of-alien-isolation)
- **Drivatar (Forza, Microsoft Research).** Learns from driving data; a Drivatar Learning System feeds a Drivatar Behaviour Model that drives AI cars. Racing lines are pre-generated from a compressed table and switched during the race for variability. The MSR slide deck reports no quantitative evaluation and does not name the model family — [Microsoft Research slide deck](https://www.microsoft.com/en-us/research/wp-content/uploads/2011/02/jqcandela2011.pdf). Xbox's Forza Horizon 2 Drivatar article (2014) describes cloud-sourced learning from players' driving styles; the page fetch failed, so I rely on search-result headlines only for the Forza 5 cloud claim — [Xbox News 2014](https://news.xbox.com/2014/09/30/games-forza-horizon-2-drivatars/)
- **Hamlet (Hunicke & Chapman) DDA.** Models health and ammo as supply and demand. Damage per step is treated as Gaussian (CLT); the CDF gives the probability of a health shortfall. Repeated shortfall signals "flailing." Reactive actions (accuracy, damage, health) act on engaged enemies and can break immersion; proactive actions (spawn type, order, health) are subtler and need follow-up. Cost estimates weigh interventions against progress, death count, and past interventions. The paper proposes heart-rate evaluation but reports no results. Limitations stated by the authors: enemies can look erratic, and the model assumes stationary damage statistics — [Hunicke & Chapman, Hamlet (Northwestern PDF)](https://users.cs.northwestern.edu/~hunicke/pubs/Hamlet.pdf)
- **Shadow of Mordor Nemesis system.** Named in the brief; I did not retrieve a primary source on its GDC 2015 presentation. Press pieces (GamesBeat) describe it as drawing from Burnout, football, and an architecture book. Gap.
- **PUBG Ally and inZOI Smart Zois.** Both use ACE. PUBG Ally is announced, with testing from early 2026 (see Q1). inZOI status not verified in this pass — gap.
- **Two-layer DDA literature.** A 2024-era systematic review (Solutions for dynamic difficulty adjustment in digital games: a systematic literature review) exists; I did not read it in full.

### Inferences
- The strong pattern across shipped and published systems is: a macro Director that manages tempo and pressure, plus a micro behaviour layer that is authored and unlocked over time. This is a good template for a Stellaris AI because the macro layer maps well to empire-level pacing.
- Alien: Isolation's "menace" concept (proximity + LOS + reachability, decaying after peaks) is a fairness-friendly design. It uses only observable signals and avoids player-death-triggered unlocks, which avoids teaching exploits.
- Hamlet's Gaussian supply/demand idea transfers to Stellaris economy-pressure models (e.g., predicted shortfalls in a resource stockpile) as a cheap, CPU-friendly trigger.

### Gaps
- No primary Nemesis source (GDC 2015 talk not retrieved).
- No primary Booth GDC 2009 source for L4D Director internals.
- Drivatar evaluation numbers not found.
- Whether any shipped DDA system is evaluated with rigorous user studies (not checked in this pass).

---

## Key Question 3: Opponent modeling algorithms (Bayesian, BPR/BPR+, type-based, plan/goal recognition, StarCraft, HMMs)

### Takeaway
Bayesian build-order and plan recognition from fog-of-war observations is the best-validated RTS-specific method. Synnaeve & Bessière's model is a learned, data-driven, very cheap inference engine, and its published cost numbers (about 0.01 s per inference, about 3 MB) are directly relevant to a CPU-side Stellaris AI. Bayesian Policy Reuse (BPR) is the cleanest framework for choosing among known opponent types, with BPR+ extending it to changing opponents.

### Cited Findings
- Synnaeve & Bessière (2011), "A Bayesian Model for Plan Recognition in RTS Games applied to StarCraft," arXiv:1111.3735 (AIIDE 2011 per the AAAI OJS listing). Model: a Bayesian program over build-tree variables; the build tree is observed partially through fog-of-war "seen building" flags; a coherence variable excludes build trees inconsistent with observations; timing per build tree is a discretized normal learned from replays. Trained on 8,806 replays of skilled StarCraft: Brood War players (about 1,000 per matchup across nine matchups). Learning takes about 0.1 s, inference about 0.01 s, and memory about 3 MB. The metric is a distance between predicted and actual build trees, not accuracy; reported values (~0.54 no noise, ~0.58 with 10% noise) are averages across matchups and the excerpt does not state which direction is better — [arXiv ar5iv](https://ar5iv.labs.arxiv.org/html/1111.3735), [AAAI OJS](https://ojs.aaai.org/index.php/AIIDE/article/view/12429)
- Bayesian Policy Reuse (BPR; Rosman et al., 2016 — foundational, not verified in this pass): maintain a belief over a library of known policies/opponent types, update it by Bayes' rule from observed rewards or signals, and select the best-response policy using belief-driven selection heuristics (BPR-PI, BPR-EI) — [Hernandez-Leal et al. 2016 cited in BPR+ paper](https://ar5iv.arxiv.org/html/1809.04240)
- BPR+ (Hernandez-Leal, Taylor, Rosman, Sucar, de Cote, 2016 AAMAS, citation per the arXiv record): extends BPR to multiagent settings to detect changes in an opponent's strategy, and can learn new policies online against previously unseen opponent policies. Designed for single-state repeated games. Earlier work assumes the opponent randomly switches among a class of stationary policies. The reported result is that BPR-EI performed best among BPR variants. Arxiv 1809.04240 is "Towards Efficient Detection and Optimal Response against Sophisticated Opponents" — [arXiv 1809.04240](https://ar5iv.arxiv.org/html/1809.04240)
- Albrecht & Stone (2018), "Autonomous agents modelling other agents: A comprehensive survey and open problems," Artificial Intelligence (AIJ). Surveys opponent/teammate modelling methods across sub-communities and their assumptions. Taxonomy details and open-problem list were not retrievable from the abstract page; the full text is at arXiv:1709.08071 — [arXiv 1709.08071](https://arxiv.org/abs/1709.08071)
- Deep BPR (He et al., NeurIPS 2018, "A Deep Bayesian Policy Reuse Approach Against Non-Stationary Agents") extends BPR to non-stationary opponents with deep policies — [NeurIPS 2018 abstract](https://proceedings.neurips.cc/paper/2018/hash/85422afb467e9456013a2a51d4dff702-Abstract.html). Listed from search results; not fetched.
- Hidden Markov models and type-based reasoning: no primary source fetched in this pass — gap.

### Inferences
- A Synnaeve-style model is the right scale for Stellaris: it is precomputed from logs or self-play, runs in milliseconds, and works from partial observation. The fog-of-war mask is natural to build in.
- For a Stellaris AI, BPR-style belief over a small library of human "playstyle archetypes" (turtle, tech rush, concentration) is a good fit. Each archetype becomes a fixed counter-policy, and belief selects which one to deploy. This avoids training large networks online.
- HMMs are plausible for phase tracking (early / mid / late game) but the phase boundaries are better handled by the Bayesian build model plus timers.

### Gaps
- Primary text for BPR (2016 Machine Learning journal) and BPR+ (AAMAS 2016) not read directly; claims above are from the arXiv secondary excerpt.
- HMM-based opponent modeling sources not retrieved.
- Albrecht & Stone open-problem list not retrieved.

---

## Key Question 4: Counter-strategy selection (LinUCB, Thompson sampling, EXP3, restricted Nash response, safe exploitation)

### Takeaway
For choosing among counter-strategies under a non-stationary adversary, Thompson sampling or UCB-style contextual bandits are the default. EXP3 is the adversarial-safe fallback. For game-theoretic exploitation, Restricted Nash Response (RNR) and newer safe-exploitation methods bound the risk of exploiting a wrong model. These are academic, not shipped in games.

### Cited Findings
- Li et al. (2010), contextual bandit for personalized news recommendation (arXiv:1003.0146; the LinUCB algorithm itself): 12.5% click lift over a context-free bandit on Yahoo! Front Page data with 33M+ events; the advantage grows when data is scarce. The fetched text did not name LinUCB or state its regret bound — [arXiv 1003.0146](https://web3.arxiv.org/abs/1003.0146)
- Ganzfried, "Safe Equilibrium," arXiv:2201.04266 (2022; venue not stated in fetched text). Defines an opponent as playing a payoff-minimizing strategy with probability ε_i and a best response otherwise. Safe equilibrium interpolates between Nash (ε≈0) and maximin (ε large). Existence holds for all ε in [0,1]; computing one is PPAD-hard for ε_i < 1. Exact 2-player solves run under a second up to 20 strategies per player; approximate solves handle 25 strategies in a fraction of a second — [arXiv 2201.04266](https://arxiv.org/pdf/2201.04266)
- Li & Huang (arXiv:2607.28520, submitted 2026-07-30; self-reported, no peer review shown): confidence-scheduled restricted responses (CS-RNR). It tracks action frequencies with anytime-valid confidence sequences, exploits only deviations whose intervals separate from equilibrium, builds a conservative opponent model, solves restricted responses over a pin-level grid, checks candidates with a full-tree best response, and commits a certificate bounding loss against the equilibrium reference. Results (Leduc, Liar's Dice, 5-rank Leduc) are from the authors' experiments only — [arXiv 2607.28520](https://arxiv.org/abs/2607.28520)
- "Safe opponent exploitation subgame refinement" (NeurIPS 2022) is a related line of work; not fetched in this pass — [NeurIPS 2022 PDF](https://proceedings.neurips.cc/paper_files/paper/2022/file/b12a1d1014e952e676f5d6931d03241a-Paper-Conference.pdf)
- Ganzfried & Sandholm, "Safe opponent exploitation" (ACM TEAC 2015) is the foundational safe-exploitation paper per the literature; not verified in this pass — gap.
- EXP3 (Auer et al., 2002, adversarial bandits) and Thompson sampling for contextual bandits (Agrawal & Goyal, 2013): not verified in this pass; cited from knowledge only. Treat as foundational background, not sourced.

### Inferences
- For Stellaris (many sequential decisions, partially observed, non-stationary human play), the practical stack is: Bayesian playstyle belief (Q3) → choose among precomputed counter-policies with a Thompson-sampling or UCB bandit over episodes → bound risk with a safe-exploitation constraint (e.g., never drop below a threshold of baseline AI strength).
- Restricted Nash response is the principled version of "counter the model, but don't get wrecked if the model is wrong." Full game-theoretic solving is infeasible for Stellaris; the useful idea is to limit how far the counter-policy deviates from a baseline, and to gate deviations on statistical confidence.
- The fairness constraint from Alien: Isolation (never use hidden or unobservable state; no player-death-triggered unlocks) can be enforced inside the bandit by only using features from fog-of-war visible state.

### Gaps
- No primary source for LinUCB regret or Thompson sampling bounds fetched.
- Stellaris-specific bandit evaluations do not exist in the public literature I found.
- The 2026 CS-RNR paper is a single self-reported preprint; treat with caution.

---

## Key Question 5: Fairness and fog-of-war (keeping the model to observable information; avoiding feeling like cheating)

### Takeaway
Fairness in adaptive AI is mostly a design constraint: restrict inputs to what a human could see, avoid unlocks triggered by player failure, and make mistakes look like reasoning rather than omniscience. Alien: Isolation is the clearest published statement of this approach.

### Cited Findings
- Alien: Isolation's designers state the alien "never cheats" and teleports only for cutscenes. The Director knows the player's position, but the alien must search. Designers mark no-check areas to avoid unfair situations — [Game Developer, Thompson, 2017](https://www.gamedeveloper.com/design/the-perfect-organism-the-ai-of-alien-isolation)
- Alien: Isolation's unlock conditions exclude player deaths to prevent players learning hidden exploits — same source.
- Hamlet DDA authors flag that reactive difficulty changes can make enemies "seem erratic," and that proactive changes can spiral into repeated corrections — [Hamlet PDF](https://users.cs.northwestern.edu/~hunicke/pubs/Hamlet.pdf)
- PUBG Ally coverage raises the concern that AI teammates could become too strong and require balancing — [Windows Central](https://windowscentral.com/hardware/nvidia/pubg-adds-ai-squadmates-that-listen-loot-and-fight-like-real-players-powered-by-nvidias-ace-tech)

### Inferences
- A Stellaris AI should only read fog-of-war state that the player has in principle: visible fleets, observed systems, and known empire stats (or derived estimates using the same detection rules).
- Opponent-type estimates should be displayed or hinted in logs or diplomacy text, so the player can see why the AI reacted (an explainability layer makes adaptation feel deliberate).
- Adaptation should be time-gated and capped (bounded change per N turns, bounded counter-strength) to avoid spirals.

### Gaps
- No peer-reviewed player-experience study on adaptive strategy fairness found in this pass. Needs a dedicated search (e.g., Yannakakis and collaborators on player experience modelling; not verified).

---

## Key Question 6: What makes adaptation feel smart vs. random to players?

### Takeaway
Adaptation feels smart when it is legible (the player can infer the cause), consistent with the AI's apparent intent, and paced (changes are telegraphed and not every move changes). It feels random when changes appear without cause, flip frequently, or reflect hidden state. Alien: Isolation's "imperfect search with backtracking" is the clearest published example of signalling intent.

### Cited Findings
- Alien: Isolation's designers aim for the feeling of powerlessness plus the sense that the player is still smarter than the alien; they use imperfect search order and backtracking to show doubt — [Game Developer, Thompson, 2017](https://www.gamedeveloper.com/design/the-perfect-organism-the-ai-of-alien-isolation)
- Alien: Isolation's Director uses a menace gauge that peaks and then retreats, so pressure has visible rhythm rather than constant escalation — same source.
- L4D Director aims for horror-film pacing, with stretches of quiet before a release — [Game Developer, Swift, 2009](https://www.gamedeveloper.com/game-platforms/q-a-valve-s-swift-on-i-left-4-dead-2-i-s-production-ai-boost)
- Hamlet's Comfort Zone and Discomfort Zone policies target a fixed experience band (around 50% health with plentiful supplies, versus scarce resources and accurate enemies) — [Hamlet PDF](https://users.cs.northwestern.edu/~hunicke/pubs/Hamlet.pdf)
- Asterion's pitch is that less predictable patterns create surprise. This is a marketing claim, not evidence of perceived intelligence — [InvenGlobal](https://www.invenglobal.com/articles/19159/wemade-next-ceo-jeong-soo-park-shares-insights-on-mir5s-revolutionary-ai-boss)

### Inferences
- Legibility plus telegraphing (e.g., a visible "Tactical shift" event or a diplomatic line citing the detected pattern) likely matters more to perceived intelligence than the sophistication of the underlying model. This is an inference from the design writing above, not a measured result.
- Pure randomness in counter-choice (e.g., a bandit that keeps exploring) will feel random; exploration should be limited to episodes when confidence is low, and shown as such.

### Gaps
- No controlled user study comparing legible vs. opaque adaptation was found in this pass.

---

## Summary of Shipped vs. Demo vs. Research

- **Shipped (verified or documented):** Left 4 Dead AI Director (2008–2009 shipped, pacing design); Alien: Isolation Director/behaviour-tree system (2014 shipped, Game Developer design write-up 2017); Forza Drivatar (shipped in Forza titles per Xbox news; internal model details thin); NVIDIA Nemotron-4 4B SLM as a developer NIM (documented; shipping in-game status of Mecha BREAK not confirmed here).
- **Announced / demoed (not verified as shipped):** Asterion / MIR5 AI boss (CES 2025 showcase, adaptive claims unverified); PUBG Ally (testing planned early 2026); inZOI Smart Zois; Total War: PHARAOH advisor.
- **Research:** Synnaeve & Bessière 2011 (StarCraft plan recognition); BPR (2016) and BPR+ (2016); Deep BPR (2018); Albrecht & Stone (2018 survey); Hamlet DDA (2004); Li et al. LinUCB (2010); Ganzfried Safe Equilibrium (2022); Li & Huang CS-RNR (2026 preprint, self-reported).

## Practical notes for a CPU-bound Stellaris engine

- No ACE/SLM component is usable CPU-side for opponent modeling; the Nemotron 4B SLM requires an RTX GPU. Use classical Bayesian models instead.
- The Synnaeve-style model's published footprint (about 3 MB memory, about 0.01 s inference) is a realistic reference for per-turn cost in a 4X engine.
- Recommended stack to research further: Bayesian playstyle belief (BPR-style over archetype library) → bandit or confidence-gated switching among counter-policies → safe-exploitation floor against a baseline AI → Alien-style legible Director for pacing.

## Unresolved gaps (priority order for follow-up)

1. Asterion/MIR5 primary source on learning method and shipping status (Wemade IR, MIR5 release notes).
2. Primary sources for BPR (2016 journal), HMM opponent modeling, and Thompson/EXP3 foundations.
3. Nemesis system primary source (GDC 2015 talk).
4. Player-experience studies on legibility of adaptive AI.
5. Full text of Albrecht & Stone 2018 for open problems.
