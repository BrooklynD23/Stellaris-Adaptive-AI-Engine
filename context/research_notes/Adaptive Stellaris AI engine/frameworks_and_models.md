# Open-source frameworks, libraries and models for an adaptive Stellaris AI engine

Research date: 2026-10-18. Maintenance status is taken from PyPI upload dates and GitHub pages fetched on that date. GitHub's API was not reachable from this session (403 for the repos tried), so GitHub "last commit" and "latest release" data is mostly missing. Where a license comes only from memory, it is flagged as unverified.

## Comparison table

Release dates are the latest PyPI upload unless noted. "Hardware" is the typical need for the component itself, not the game.

| Area | Component | Latest version / date | License | Maintenance (Oct 2026) | Hardware | Fit for external, pausable grand-strategy process |
|---|---|---|---|---|---|---|
| (a) Env API | Gymnasium | 1.4.0, 2026-10-05 | MIT | Active | CPU | Good for single-agent envs. Step/reset model maps to a monthly tick, but Gym's step assumes a synchronous simulator. |
| (a) Env API | PettingZoo | 1.27.0, 2026-08-13 | MIT | Active (Farama) | CPU | Good for multi-agent (AEC or Parallel). AEC fits turn-based play. Note the env version suffix (`_v0`) convention. Officially Linux/macOS only. |
| (b) Training | Stable-Baselines3 | 2.9.0, 2026-06-15 | MIT | Active | CPU or one consumer GPU | Practical default for single-agent PPO/SAC/DQN. Works with Gymnasium. Not built for multi-agent self-play. |
| (b) Training | RLlib (Ray) | Ray 2.59.0, 2026-10-02 | Apache-2.0 | Active | Multi-node capable; works on one PC but heavy | Strongest for multi-agent and population training. Operational overhead is high for one PC. |
| (b) Training | CleanRL | 1.2.0, 2023-05-22 (PyPI) | MIT | PyPI release is old. Repo activity not verified. | CPU or one GPU | Single-file reference implementations. Useful for readable custom self-play code, not as a library. |
| (b) Training | Sample Factory | 2.1.1, 2023-06-19 (PyPI) | MIT | PyPI release is old. Repo activity not verified. | One GPU, many CPU cores | Fast async PPO for high-throughput sim. Likely overkill for a turn-based game with a monthly tick. |
| (b) Training | TorchRL | 0.14.1, 2026-10-10 | License not verified on PyPI (believed BSD-style) | Active | CPU or one GPU | Modular PyTorch RL library. Good if you want custom loops and PyTorch-native components. |
| (b) Evolution | EvoTorch | 0.6.1, 2025-05-14 | Apache-2.0 | Slower (last release ~17 months before this date) | CPU or GPU | GPU-capable evolutionary search. Suited to tuning many AI weights in parallel. |
| (b) Evolution | pycma (CMA-ES) | 4.5.0, 2026-09-13 | License not shown on PyPI (believed BSD-3) | Active | CPU | Very practical for tuning a few hundred continuous AI parameters (weights, thresholds). |
| (b) Evolution | Nevergrad | 1.0.12, 2025-04-23 | MIT | Slower (last release ~18 months before this date) | CPU | Gradient-free optimizer collection (incl. CMA, DE). Easy to use for parameter sweeps. |
| (c) Game theory | OpenSpiel | 2.0.2, 2026-08-12 | Apache-2.0 (GitHub page) | Active | CPU | Best open option for PSRO, CFR, MCTS and replicator dynamics on game matrices. Implemented in C++ with Python bindings. Its game library is for extensive-form and simultaneous-move games, not a 4X engine. |
| (d) Inference | llama.cpp | Latest release b11435, 2026-10-06 (GitHub releases page) | MIT (not shown on fetched page; believed MIT) | Active | CPU, or GPU via CUDA/Vulkan/Metal | Runs GGUF models locally. GBNF grammars and JSON-schema constraints (see below). Best control for a gated planner. |
| (d) Inference | llama-cpp-python | 0.3.36, 2026-10-01 | MIT | Active | Same as llama.cpp | Python bindings. Easy to embed in the external process. |
| (d) Inference | Ollama | Reported v0.40.6, 2026-10-16 (GitHub releases page; version numbers as shown) | MIT | Active | CPU or GPU; Windows, macOS, Linux | Easiest local server; REST API with `format` JSON schema. Schema enforcement mechanism not documented on the page fetched. |
| (d) Inference | vLLM | 0.32.0, 2026-10-18 | Apache-2.0 | Active | NVIDIA/AMD GPU, most practical with a large VRAM budget | Server-grade throughput. Structured output via xgrammar or guidance. Overkill for one gaming PC. |
| (d) Inference | LM Studio | Not checked | Proprietary freeware; not open source | Active (commercial app) | CPU or GPU | Not usable as a bundled component. Its terms restrict use to personal or internal business purposes and forbid distribution. |
| (d) Constrained decoding | XGrammar | 0.2.8, 2026-09-24 | Apache-2.0 | Active | CPU or GPU | Fast grammar/JSON-schema engine, used by vLLM and others. |
| (d) Constrained decoding | Outlines | 1.3.3, 2026-08-06 | Apache-2.0 | Active | CPU or GPU | Python-first structured generation with Pydantic/JSON schema. Works with several backends. |
| (e) Agent orchestration | Claude Agent SDK | 0.2.168, 2026-10-15 | MIT | Active | Cloud API; no local GPU | Strong for a planner that calls tools, but it depends on a hosted Claude model. Not local. |
| (e) Agent orchestration | PydanticAI | 2.57.0, 2026-10-16 | Not verified on PyPI (believed MIT) | Active | None (library) | Validated typed outputs with Pydantic models. Good fit for a gate that rejects invalid intent JSON. |
| (e) Agent orchestration | LangGraph | 1.2.16, 2026-10-16 | Not verified on PyPI (believed MIT) | Active | None (library) | Explicit state-machine graphs with checkpointing. Good for pausable, resumable turn workflows. Heavier than needed for a single planner. |
| (e) Agent orchestration | smolagents | 1.26.0, 2026-05-29 | Not verified on PyPI (believed Apache-2.0) | Active | None (library) | Lightweight code-agent library. Less suited to strict JSON intents than PydanticAI. |
| Offline RL | d3rlpy | 2.8.1, 2025-03-02 | MIT | Slower (last release ~19 months before this date) | CPU or one GPU | Offline RL and imitation algorithms. Needs logged trajectories. |
| Datasets | Minari | 0.5.4, 2026-09-29 | MIT | Active | CPU | Standard format for offline datasets, Gymnasium-compatible. Only useful if you record game decisions as episodes. |

## Environment APIs (key question a)

### Takeaway
Gymnasium and PettingZoo are both actively released and MIT-licensed, so they are safe bases. The Stellaris bridge is the harder part, since no official external API was found. The turn-based shape fits PettingZoo's AEC model.

### Cited Findings
- Gymnasium 1.4.0 uploaded 2026-10-05, MIT — [PyPI](https://pypi.org/project/gymnasium/)
- PettingZoo 1.27.0 uploaded 2026-08-13, MIT; models environments as Agent Environment Cycle (AEC) games with a Parallel API as a secondary option; Linux and macOS officially supported, Windows not officially supported — [GitHub](https://github.com/Farama-Foundation/PettingZoo), [PyPI](https://pypi.org/project/pettingzoo/)
- PettingZoo environment IDs carry a version suffix (for example `_v0`) that increases when changes might affect learning results — [GitHub](https://github.com/Farama-Foundation/PettingZoo)
- Stellaris save tooling: `Stellaris-Save-API` (TypeScript/Node, MIT) reads and rewrites save files only, has no live-game access, has 9 commits and 6 stars, and lists a planned `play` command as a Todo — [GitHub](https://github.com/againpsychox/stellaris-save-api)
- A .NET save parser, `MageeSoft.StellarisSaveParser` 0.0.1, is also on NuGet — [NuGet](https://www.nuget.org/packages/mageesoft.stellarissaveparser)
- CivRealm (Freeciv-based, GPL-3.0) exposes a Gymnasium interface with turn and step counters and runs through Docker; it is a useful reference design for a 4X environment wrapper, but it is not Stellaris — [GitHub](https://github.com/bigai-ai/civrealm)

### Inferences
- A monthly-tick game maps best to a PettingZoo AEC environment: one "turn" per month, with the AI agents acting in a fixed order, and the game process paused between ticks.
- Gymnasium alone is enough for a single-agent AI controlling one empire. Multi-empire self-play needs PettingZoo.
- The hard part is the game bridge (reading state and issuing intents). Save-file parsing gives read-only state at tick boundaries, which suits a slow game. Live control needs a mod or other channel that was not found in this research.

### Gaps
- No official Stellaris external API or IPC channel was found. Whether a mod can export state or accept intents without a live hook was not verified.
- Sample Factory and CleanRL repository activity could not be checked (GitHub API blocked); only old PyPI uploads were seen.

## Training: RL, self-play, evolution (key question b)

### Takeaway
On one consumer PC, Stable-Baselines3 (single-agent PPO) and pycma or Nevergrad (parameter search over AI weights) are the most practical. RLlib works but adds operational overhead. EvoTorch is a good GPU option for evolutionary search, but its last release is over a year old.

### Cited Findings
- Stable-Baselines3 2.9.0 uploaded 2026-06-15, MIT, Python >=3.10 — [PyPI](https://pypi.org/project/stable-baselines3/)
- Ray 2.59.0 uploaded 2026-10-02, Apache-2.0, Python >=3.10 — [PyPI](https://pypi.org/project/ray/)
- TorchRL 0.14.1 uploaded 2026-10-10 — [PyPI](https://pypi.org/project/torchrl/)
- CleanRL 1.2.0 uploaded 2023-05-22, MIT — [PyPI](https://pypi.org/project/cleanrl/)
- Sample Factory 2.1.1 uploaded 2023-06-19, MIT — [PyPI](https://pypi.org/project/sample-factory/)
- EvoTorch 0.6.1 uploaded 2025-05-14, Apache-2.0 — [PyPI](https://pypi.org/project/evotorch/)
- pycma 4.5.0 uploaded 2026-09-13 — [PyPI](https://pypi.org/project/cma/)
- Nevergrad 1.0.12 uploaded 2025-04-23, MIT — [PyPI](https://pypi.org/project/nevergrad/)
- d3rlpy 2.8.1 uploaded 2025-03-02, MIT — [PyPI](https://pypi.org/project/d3rlpy/)
- Minari 0.5.4 uploaded 2026-09-29, MIT — [PyPI](https://pypi.org/project/minari/)

### Inferences
- Start with PPO from SB3 on a reduced single-empire environment. Add population-based evaluation using CMA-ES or Nevergrad over the reward and utility weights of the AI, which is cheap on CPU.
- RLlib becomes worth it only if many empires train concurrently across several machines.
- Sample Factory's throughput advantage matters for millions of fast steps. A monthly-tick game produces far fewer steps, so it is unlikely to matter here.
- Offline RL (d3rlpy) only helps once there are logged game decisions. Record them in Minari format from the start.

### Gaps
- Only PyPI upload dates were checked for CleanRL, Sample Factory and EvoTorch; GitHub activity was not verified.
- No benchmark numbers for single-PC throughput on these libraries were found in this research.

## Game theory and search (key question c)

### Takeaway
OpenSpiel is the only well-maintained open library found that covers PSRO, CFR and MCTS in one place. It is most useful for population-based training against player archetypes, once the game is reduced to a tractable matrix or small extensive-form model.

### Cited Findings
- OpenSpiel 2.0.2 uploaded 2026-08-12 — [PyPI](https://pypi.org/project/open-spiel/)
- OpenSpiel is Apache-2.0, with core API and games in C++ exposed to Python; tutorials cover CFR, REINFORCE and replicator dynamics — [GitHub](https://github.com/google-deepmind/open_spiel)
- The OpenSpiel GitHub page fetched did not show PSRO or MCTS in its visible text, though an "Overview of Implemented Algorithms" page is linked — [GitHub](https://github.com/google-deepmind/open_spiel)

### Inferences
- PSRO against fixed player archetypes (for example, aggressive, diplomatic, and turtle) is feasible if the strategy space is discretized into a small set of AI policies. Full Stellaris is far too large for direct CFR.
- MCTS over a 4X state is not practical with a real game simulator. A tractable abstraction (fleet and border decisions only) is needed.

### Gaps
- PSRO and MCTS implementations were not confirmed from the fetched OpenSpiel page. Check `open_spiel/python/algorithms/` directly before relying on them.
- The PSRO README fetch failed (HTTP error), so PSRO specifics are unverified.

## Local LLM inference for an optional planner (key question d)

### Takeaway
llama.cpp with GBNF or JSON-schema grammars gives the strongest control for a gated planner on one consumer PC. Ollama is the easiest path. Gemma 4 (Apache-2.0) and Qwen3.5 small models are the candidates to test; VRAM figures for Qwen3.5 were not verified.

### Cited Findings
- llama.cpp converts a subset of JSON Schema into GBNF grammars. Completion endpoints take `json_schema`; `/chat/completions` takes `response_format`; the CLI takes `--json`. The schema constrains output but is not shown to the model, so the prompt must describe the structure. Known gaps include `prefixItems`, nested `$ref`, `patternProperties`, and `anyOf` mixed with `properties`. Grammar performance caveat: use `x{0,N}`, not repeated optional items — [llama.cpp grammars README](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)
- llama.cpp latest release tag b11435 published 2026-10-06 — [GitHub releases](https://github.com/ggml-org/llama.cpp/releases)
- Ollama is MIT-licensed and runs on macOS, Windows, Linux and Docker — [GitHub](https://github.com/ollama/ollama)
- Ollama `format` accepts a JSON schema (or the plain string `"json"`). The page recommends also putting the schema in the prompt and using temperature 0. The page does not say whether decoding is constrained — [Ollama structured outputs docs](https://docs.ollama.com/capabilities/structured-outputs.md)
- Ollama latest release reported as v0.40.6 (2026-10-16) with v0.40.7-rc0 pre-release (2026-10-18), per the GitHub releases page; year inferred — [GitHub releases](https://github.com/ollama/ollama/releases)
- vLLM 0.32.0 uploaded 2026-10-18 — [PyPI](https://pypi.org/project/vllm/); Apache-2.0 and structured output via xgrammar or guidance — [GitHub](https://github.com/vllm-project/vllm)
- XGrammar 0.2.8 (Apache-2.0) and Outlines 1.3.3 (Apache-2.0) are current — [XGrammar PyPI](https://pypi.org/project/xgrammar/), [Outlines PyPI](https://pypi.org/project/outlines/)
- LM Studio's terms allow only personal or internal business use, forbid sublicensing or distribution, and treat the app as proprietary; third-party models follow their own license terms — [LM Studio terms](https://lmstudio.ai/terms)
- Gemma 4 has four sizes: E2B (2.3B effective), E4B (4.5B effective), a 26B Mixture-of-Experts with 3.8B active parameters, and a 31B dense model. License is Apache 2.0. Release date 2026-04-02 — [DataNorth](https://datanorth.ai/news/google-releases-gemma-4-open-models) (secondary source; check the Google model card)
- Gemma 4 hardware guidance from the same secondary source: E4B about 12–16 GB, 26B MoE about 24 GB, 31B 40 GB or more in bf16 — [DataNorth](https://datanorth.ai/news/google-releases-gemma-4-open-models)
- Qwen3.5 small models come in 0.8B, 2B, 4B and 9B sizes; the article is dated 2026-03-03 and its full VRAM figures are paywalled — [Kaitchup](https://kaitchup.substack.com/p/qwen35-9b-4b-2b-and-08b-gpu-requirements)

### Inferences
- For a planner that emits validated JSON intents, a 4B-to-9B class model under Q4 quantization is the likely target. Gemma 4 E4B (Apache-2.0) is a reasonable first candidate; Qwen3.5 4B/9B is the second. Both need benchmarking on your hardware.
- Keep the planner optional and off the critical path: the engine should work with a rule-based fallback when the model is absent or slow.
- Grammar-constrained decoding plus a separate Pydantic validator that checks each intent against the current game state is the safest way to get valid intents. The grammar guarantees shape; the validator guarantees legality.

### Gaps
- Qwen3.5 license and VRAM at Q4/Q8 were not verified. The Kaitchup article was paywalled.
- Phi-4 mini, Mistral Small and Llama 3.2 3B were not verified in this research. Only a directory-style search was returned; do not treat any of them as checked.
- No measured tokens-per-second numbers for a game-PC setup were found.
- The llama.cpp license was not shown on the fetched page.

## Agent orchestration (key question e)

### Takeaway
PydanticAI fits best for a gated planner because it validates typed outputs against Pydantic models. LangGraph suits pausable, resumable turn workflows but is heavier. The Claude Agent SDK is strong but cloud-hosted, so it does not meet a fully local requirement.

### Cited Findings
- PydanticAI 2.57.0 uploaded 2026-10-16 — [PyPI](https://pypi.org/project/pydantic-ai/)
- LangGraph 1.2.16 uploaded 2026-10-16 — [PyPI](https://pypi.org/project/langgraph/)
- smolagents 1.26.0 uploaded 2026-05-29 — [PyPI](https://pypi.org/project/smolagents/)
- Claude Agent SDK 0.2.168 uploaded 2026-10-15, MIT — [PyPI](https://pypi.org/project/claude-agent-sdk/)
- Ollama's structured outputs use a Pydantic- or Zod-generated JSON schema, so a Pydantic model can serve both the Ollama request and the validator — [Ollama structured outputs docs](https://docs.ollama.com/capabilities/structured-outputs.md)

### Inferences
- Use PydanticAI or plain Pydantic with the local model server. Put a hard validator between the model and the game (schema, range, and legality checks against the current state snapshot).
- LangGraph is worth it only if the planner must keep multi-turn memory across many ticks and pause/resume mid-plan.

### Gaps
- The license of PydanticAI, LangGraph and smolagents was not shown on PyPI and was not fetched from GitHub; treat as unverified (believed permissive).

## Existing LLM/RL projects for Paradox or 4X games (cross-cutting)

### Takeaway
No open-source LLM or RL AI for Stellaris, EU4 or HOI4 was found. The closest reference projects are CivRealm (Freeciv, GPL-3.0) and the Stellaris save-file tools.

### Cited Findings
- CivRealm: a Gymnasium environment built on Freeciv-web, GPL-3.0, with a language-model agent baseline in a separate repository (civrealm-llm-baseline); last release not visible; the README says future maintenance will be by BIGAI — [GitHub](https://github.com/bigai-ai/civrealm)
- CivRealm was published at ICLR 2024 — [arXiv 2401.10568](https://arxiv.org/abs/2401.10568)
- Stellaris-Save-API (MIT, TypeScript): save-file read/write, with the save format changing between game versions — [GitHub](https://github.com/againpsychox/stellaris-save-api)
- A Digital Player paper evaluating LLM-based human-like agents in games was found — [arXiv 2502.20807](https://arxiv.org/html/2502.20807v1). Not reviewed in detail.

### Inferences
- The Stellaris engine likely needs its own environment wrapper. CivRealm's structure (turn counter, per-unit actions, Gymnasium API) is a useful template.
- Save-file parsing gives reproducible tick-boundary state for offline training, which may be enough before any live bridge exists.

### Gaps
- Searches returned no Stellaris AI mod, LLM agent or RL project. Steam Workshop changelog pages were returned but not read.
- No EU4, HOI4 or Unciv AI project was checked in this research.
- Save-file formats change between Stellaris versions, so any parser needs version pinning.

## Overall gaps and caveats

- GitHub API blocked for these repos; activity dates for most GitHub-only projects are missing.
- Licenses marked "believed" or "not verified" should be checked against each repo's LICENSE file before use.
- Qwen3.5 license, VRAM, and Phi-4/Mistral/Llama small-model status were not verified.
- No benchmarks were run.
