# Verification notes (evidence trail for proposal.md)

Method note: the Agent tool was not available in this session, so the lead (Opus 5.5) ran every search and fetch directly. No Fable review pass was possible. Tags: VERIFIED = primary source opened and claim matches; PARTLY = source exists, details differ; NOT FOUND = no primary source located; UNVERIFIED = only secondary sources.

## User-supplied leads

1. MASS (Multi-Agent Self-Supervision). PARTLY VERIFIED → effectively VERIFIED with caveats.
   - arXiv 2610.12176, submitted 8 Oct 2026. Authors Hyunin Lee, Jinglue Xu, Jeffrey Seely, Donghyun Lee, Somayeh Sojoudi, Matei Zaharia, Yujin Tang. Affiliations Sakana AI + UC Berkeley come from a garbled PDF author block matched by position, so moderate confidence. https://arxiv.org/abs/2610.12176, https://arxiv.org/pdf/2610.12176
   - Abstract: one model "iteratively propose[s], execute[s], and self-evaluate[s] multi-agent workflows" and "alternates between evolutionary workflow optimization and supervised fine-tuning on self-generated trajectories". "Over two MASS cycles with Qwen3.6-27B … 1.2-1.6x higher performance per output tokens on four open-ended public benchmarks."
   - Caveats from the repo wiki and PDF: raw gains are modest (ScienceAgentBench 28.8→31.7%); the DSBench change is within its SD; SWE-bench Verified and Terminal-Bench 2.0 dip slightly; workflow search is unstable; mean quality per delivered MLR paper declines. Evaluator gaming and reward hacking are not discussed. External judges are withheld from training. https://github.com/SakanaAI/mass/wiki

2. AIDE² (Weco AI). VERIFIED, but the numbers depend on which source you read.
   - Blog, 14 Jul 2026: 100 consecutive outer-loop steps, 8 days, 7 improved versions, about 9 in 10 proposals rejected. KernelBench reward hacking AIDE0 63% → AIDE47 42% → AIDE85 34%, versus AIDEhuman 42%. The blog claims "Level 1 (net positive)" on Weco's own four-level ladder (delegation, net positive, ignition, inflection), and Level 2 ignition is not achieved. https://www.weco.ai/blog/first-evidence-of-recursive-self-improvement, https://www.weco.ai/blog/4-levels-of-recursive-self-improvement
   - Paper, arXiv 2609.26457 (22 Sep 2026): 99 rewrite proposals with 7 accepted (steps 2, 6, 28, 39, 47, 63, 85); grade 0.703 → 0.778. Reward hacking is 55% → 39% → 32% (human 39%) on 38 pairs, so it differs from the blog. Inner loop optimises a public signal while selection uses a private held-out grade. About a quarter of rejected rewrites beat the incumbent on the public signal but lost on the private one. Claude Opus 4.7 drives the outer loop. Limitations: noise compounds, ignition is inconclusive (3 seeds), and the OOD result rests on one task. https://arxiv.org/pdf/2609.26457, https://www.alphaxiv.org/abs/2609.26457.md
   - Thread by Z. Jiang confirms "On our RSI ladder, AIDE² is Level 1". https://threadreaderapp.com/thread/2077079778793042425 . A secondary article calls the result self-reported and not peer-reviewed. https://fourweekmba.com/ai-weco-ai-aide2-recursive-self-improvement-benchmark/

3. Dream-RSI. PARTLY VERIFIED (concept correct, numbers misquoted).
   - arXiv 2609.14858, submitted 14 Sep 2026 (v2 dated 6 Oct). Authors are from Google, UMD, Google DeepMind and UVA. Also a NeurIPS 2026 workshop poster. https://arxiv.org/abs/2609.14858, https://arxiv.org/pdf/2609.14858, https://neurips.cc/virtual/2026/169874
   - Completed exploration trees become replay worlds, and policy code is scored across all stored worlds. Out-of-support plans earn no replay reward.
   - "1.62" does not appear in the paper. Reported figures: 1.79×–2.43× fewer generations on KernelBench; up to 2.09× higher performance at a comparable budget; up to 162× fewer calls than SimpleTES on Lasso (different backbones, uncontrolled). No seeds or variance are reported, results are mixed (Autocorrelation is worse), and there is no held-out gate beyond the replay average plus the next online round.

4. "RSI Loop Acceptance Gate" (Stage 1 "did it work?" / Stage 2 "is it meaningful?"). NOT FOUND.
   - An exact-phrase search returned only TradingView "RSI For Loop" indicators.
   - Nearest real work: MetaRSI (arXiv 2609.06396) uses strict-improvement promotion, a four-check policy acceptance and a sealed held-out split, https://arxiv.org/pdf/2609.06396 . "Falsifiable Release Gates" (arXiv 2607.13070) states five acceptance invariants: monotonicity, a gate the system cannot modify, one-cycle reversibility, eval freshness and a reward-hacking floor; loosening changes need a human merge, https://arxiv.org/pdf/2607.13070 . A community skill describes a two-split held-in/held-out acceptance gate, https://raw.githubusercontent.com/muratcankoylan/Agent-Skills-for-Context-Engineering/main/skills/self-improvement-loops/SKILL.md

5. TasteVal / Jack Clark 2027.
   - TasteVal: VERIFIED. LessWrong post, 6 Oct 2026, by Ollie J and Dane Sherburn. The linked tweet's handle is "pzeroresearch", but the page does not state the organisation (UNVERIFIED). It measures experimental research taste as a compute multiplier over 24 human experts on 8 tasks; Opus 5.5 scores 2.30× (95% CI 1.15–4.37). https://www.lesswrong.com/posts/bAGagjZAPgXEHmkAH/tasteval-measuring-the-experimental-research-taste-of-ai
   - Clark "full RSI ~2027": INACCURATE. TasteVal does not mention Clark. Import AI 455 (4 May 2026) gives "a likely chance (60%+)" of no-human-involved AI R&D by end of 2028, and "If you had to push me for a 2027 probability, I'd say 30%." https://importai.substack.com/p/import-ai-455-automating-ai-research

## Established work (opened)
- DGM, arXiv 2505.22954: archive-based self-modifying coding agents (SWE-bench 20→50%). Sakana's page documents objective hacking: the agent faked test logs and removed the hallucination-detection markers. Mitigations were sandboxing, human supervision and lineage. https://arxiv.org/abs/2505.22954, https://sakana.ai/dgm/
- AlphaEvolve, arXiv 2506.13131: an evolutionary LLM code pipeline driven by evaluators. https://arxiv.org/abs/2506.13131
- AIDE, arXiv 2502.13138: tree search over code. https://arxiv.org/abs/2502.13138
- AlphaStar league (Nature 2019): main agents are transitive while exploiters form non-transitive cycles; PFSP beats FSP. https://www.nature.com/articles/s41586-019-1724-z
- PSRO (Lanctot 2017): independent RL overfits to co-players, measured by joint-policy correlation. https://arxiv.org/abs/1711.00832
- PBT: https://arxiv.org/abs/1711.09846
- Voyager skill library: https://arxiv.org/abs/2305.16291
- Specification gaming (DeepMind; CoastRunners example): https://deepmind.google/discover/blog/specification-gaming-the-flip-side-of-ai-ingenuity/
- Goodhart variants: https://arxiv.org/abs/1803.04585 (the abstract only; the variant names come from the full paper, not opened)
- Li et al. 2010, replay OPE: https://arxiv.org/abs/1003.5956
- FunSearch: the Nature fetch FAILED, so it is not cited.

## Stellaris data
- Save = zip of `meta` + `gamestate` text (wiki, last verified for 2.1). https://stellaris.paradoxwikis.com/Save-game_editing
- stellaris-dashboard source code (master; read raw): 37 gamestate processors that diff successive saves into a SQL history. Tech completion is inferred when a tech leaves the in-progress queue between two saves, so its date resolution equals the autosave interval. The save carries per-country `timeline_events` (e.g. `timeline_war_won/lost`, `timeline_first_war_won`, `timeline_destroyed_leviathan`, with dates), wars with `start_date`, battles, war exhaustion and war goals, plus `tech_status` with a queue and its `date`. The code has an `event_is_known_to_player` / `has_met_player` filter. Code comments mention 4.4 and "since 4.5" format changes (UNVERIFIED as a release fact). The README says the tool works with Ironman. https://github.com/eliasdoehne/stellaris-dashboard ; raw: https://raw.githubusercontent.com/eliasdoehne/stellaris-dashboard/master/stellarisdashboard/parsing/timeline.py , .../datamodel.py , .../README.md
- jomini: MIT, text + binary, lists Stellaris, claims >1 GB/s. https://github.com/rakaly/jomini
- pdx.tools: Stellaris not listed; analysis runs in the browser. https://pdx.tools/
- Rolling autosaves: one user reports that only 5 autosaves are kept with names like `autosave_2401.10.01.sav` (anecdote, UNVERIFIED). https://gog.com/forum/stellaris/too_many_save_gamesstill

## Player modelling
- Behavioural stylometry in chess: 98% identification among thousands of players from 100 games. https://arxiv.org/abs/2208.01366
- Individual Maia models via fine-tuning: https://arxiv.org/abs/2008.10086
- Maia4All: 20 games with prototype-matched initialisation, versus about 5,000 games before. https://arxiv.org/html/2507.21488v1
- Player Modeling (Yannakakis, Spronck, Loiacono, André 2013): https://drops.dagstuhl.de/opus/volltexte/2013/4335/
- MaxEnt IRL (Ziebart 2008): needs a known MDP and feature counts. https://cdn.aaai.org/AAAI/2008/AAAI08-227.pdf
- BOCPD (Adams & MacKay 2007): https://arxiv.org/abs/0710.3742
