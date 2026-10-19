# Dev and Monitoring Environment for a Live Stellaris AI Engine

## Precedents: Stellaris and Paradox companion tools

### Takeaway
stellaris-dashboard is the closest precedent: a Python app with a Rust save parser that tracks new saves while Stellaris runs and serves a local web dashboard. It is MIT-licensed and has an active-looking repo, but the release date and recent compatibility are not visible in the fetched page. For game-AI and RL debugging UIs, the sourced precedents are ML-Agents' TensorBoard workflow and python-sc2 debug drawing (the latter could not be verified in this session).

### Cited Findings
- stellaris-dashboard reads saves during play and shows graphs, statistics, a historical galaxy map and a text event ledger; it works with Ironman. — [GitHub: eliasdoehne/stellaris-dashboard](https://github.com/eliasdoehne/stellaris-dashboard)
- Live watching: the packaged app "tracks new saves as they appear while it runs"; autosaves trigger new data, so it must run during play. Bulk import of existing saves needs a source checkout and `uv run stellarisdashboardcli parse-saves`. — [GitHub: eliasdoehne/stellaris-dashboard](https://github.com/eliasdoehne/stellaris-dashboard)
- Load controls exposed: autosave interval, "Skip saves", initial graph resolution, CPU thread count. — [GitHub: eliasdoehne/stellaris-dashboard](https://github.com/eliasdoehne/stellaris-dashboard)
- The fetched page does not say how file changes are detected (filesystem events vs. polling). — [GitHub: eliasdoehne/stellaris-dashboard](https://github.com/eliasdoehne/stellaris-dashboard)
- Tech stack: Python dashboard code; a Rust parser (`rust_parser`) built with maturin; uv for dependencies; pytest; PyInstaller packaging; data stored in a database file. The page does not name a Python version or charting libraries. — [GitHub: eliasdoehne/stellaris-dashboard](https://github.com/eliasdoehne/stellaris-dashboard)
- Served at localhost:28053 in any browser, or from the in-game browser via its mod. — [GitHub: eliasdoehne/stellaris-dashboard](https://github.com/eliasdoehne/stellaris-dashboard)
- License: MIT (Python code), copyright 2018 Elias Doehne. Stellaris is copyright Paradox Interactive AB. — [GitHub: eliasdoehne/stellaris-dashboard](https://github.com/eliasdoehne/stellaris-dashboard)
- Maintenance signals: 593 commits on master, 5 open issues, 3 open PRs. Releases section empty and commit dates not shown in the captured page. Updates also ship via the Steam Workshop, which shows update notices. Developed for vanilla Stellaris; mods may break; multiplayer marked experimental. Compatibility with recent Stellaris versions is not stated. — [GitHub: eliasdoehne/stellaris-dashboard](https://github.com/eliasdoehne/stellaris-dashboard)
- Save parsing for Paradox games has a shared Rust foundation: the `jomini` crate (Paradox save parser) appears in search results with versions up to 0.35.0. I did not open the pdx.tools or rakaly repositories; the fetch failed. — [docs.rs: jomini 0.16.3](https://docs.rs/crate/jomini/0.16.3) (search hit, not read in full)
- python-sc2 debug drawing and Unity ML-Agents TensorBoard: ML-Agents documents TensorBoard use against the `results` directory. — [Unity ML-Agents: Using TensorBoard](https://docs.unity3d.com/Packages/com.unity.ml-agents@4.0/manual/Using-Tensorboard.html)

### Inferences
- stellaris-dashboard's architecture (watch save directory, parse incrementally with a native parser, store to a local DB, serve a local web UI) is a reasonable template for the engine's save-side observability. Its strengths and limits for in-tick decision data should be checked: saves only exist per autosave, so per-decision telemetry needs to come from the engine itself, not from saves.
- Because the dashboard depends on save files, it cannot show intra-month decisions. Those need the engine's own event log.

### Gaps
- Latest release date and last commit date of stellaris-dashboard: not visible in the fetched page. Check the Releases tab and recent commits before relying on it.
- pdx.tools / rakaly: the repo fetch failed; no verified facts on its stack, license or activity. HOI4 and EU4 companion tools were not researched.
- python-sc2 debug API (`debug_text_screen`, `debug_box2_out`, etc.) and OpenAI Five dashboards: the python-sc2 docs fetch failed and no other reliable source was retrieved. Not verified.

---

## Observability for agents and RL metrics

### Takeaway
For a single-PC project, the lowest-cost reliable path is structured JSONL event logs, with OpenTelemetry-style fields so that an LLM planner can later be traced in a tool like Phoenix. Langfuse is MIT but its self-hosted stack is heavy for one machine; Phoenix is a single container but uses a non-OSI license. OpenTelemetry GenAI conventions are the standard target, but I could not read their current attribute list in this session.

### Cited Findings
- Langfuse is MIT-licensed, self-hostable on every tier including free, runs on Docker Compose on one VM, but the production stack is ClickHouse, Postgres, Redis and blob storage. — [Langfuse: best Phoenix/Arize alternatives](https://langfuse.com/resources/engineering/best-phoenix-arize-alternatives.md) (vendor-authored; claims not independently verified)
- Phoenix is under Elastic License 2.0, described by the vendor page as "source-available" and not OSI-approved; it starts as one container on SQLite, with Postgres recommended for production. — [Langfuse: best Phoenix/Arize alternatives](https://langfuse.com/resources/engineering/best-phoenix-arize-alternatives.md) (vendor-authored); Phoenix license also stated in the same page's FAQ
- Phoenix uses OpenInference SDKs, which the page describes as OpenTelemetry-compliant; Langfuse offers an OTLP endpoint and maps OpenInference attributes. — [Langfuse: best Phoenix/Arize alternatives](https://langfuse.com/resources/engineering/best-phoenix-arize-alternatives.md)
- The OpenTelemetry GenAI conventions moved to a separate repository, `open-telemetry/semantic-conventions-genai` (Apache-2.0). The old spans page is no longer maintained there. — [OpenTelemetry GenAI spans (moved notice)](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans), [GitHub: open-telemetry/semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai)
- ML-Agents TensorBoard: run `tensorboard --logdir results --port 6006`; TensorBoard can be started during or after training; logged stats include Environment/Cumulative Reward, Policy/Entropy, Losses, Self-Play/ELO; custom metrics can be sent from C# with `StatsRecorder`. — [Unity ML-Agents: Using TensorBoard](https://docs.unity3d.com/Packages/com.unity.ml-agents@4.0/manual/Using-Tensorboard.html)
- Rerun (rerun-io/rerun): Apache-2.0 and MIT; "data layer" with viewer plus SDKs; Python SDK bundles the viewer (`pip install rerun-sdk`); supports time series; time-aware buffer for scrubbing back in time; logs to named timelines such as "frame"; data can be streamed to a viewer, saved to disk, or sent over gRPC; about 11.4k stars at capture. — [GitHub: rerun-io/rerun](https://github.com/rerun-io/rerun), [Rerun: What is Rerun?](https://rerun.io/docs/getting-started/what-is-rerun)

### Inferences
- A decision audit record (observation → inference → confidence → candidates → chosen → outcome) maps well onto OTel span events or a JSONL row keyed by (game_date, tick, decision_id). Storing it as JSONL and loading it with DuckDB is an inference from general practice; no source was fetched for DuckDB in this session.
- For the LLM planner, emitting one span per LLM call plus parent spans per planning cycle to a local Phoenix container would give trace views without a heavy stack. Langfuse becomes attractive only if the project later needs multi-user hosting.
- Rerun is a good fit for the belief-state timeline and replay/scrub, since its time-aware buffer and named timelines match a game-tick axis.
- TensorBoard is a good fit for the bandit and any neural opponent-model metrics. It is less suited to per-decision audit records.

### Gaps
- OpenTelemetry GenAI attribute names (`gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.operation.name` values, content capture opt-in): not verified. The current spec page did not load. Check `open-telemetry/semantic-conventions-genai` before implementing.
- W&B Weave, MLflow and Prometheus+Grafana: not researched in this session.
- DuckDB, Streamlit, Gradio, Panel, Dash, and FastAPI+WebSocket: not researched in this session. No comparison is claimed here.
- Langfuse and Phoenix versions and current release cadence: not found. The comparison page is vendor-authored and dated October 2026, but its claims about Arize were not verified against Arize sources.

---

## Game-run automation and Stellaris console behaviour

### Takeaway
The console is usable for observer-mode runs: the `observe` command switches to observer mode and `play <empire id>` returns control. But using any console command disables achievements, and the console is unavailable in Ironman. Launch flags, autosave frequency settings, game speed control and file locations could not be verified in this session.

### Cited Findings
- The console is opened with the backtick key on QWERTY; other layouts use Shift+2, ALT+2+1, Shift+3, §, ~, ^, °, or ². Available on non-Ironman games. — [Stellaris Wiki: Console](https://stellaris.paradoxwikis.com/Console)
- `observe` switches to observer mode; `play` followed by an empire ID returns control to that empire. If the game is unpaused while in observer mode, the AI takes control of the player's empire. — [Stellaris Wiki: Console](https://stellaris.paradoxwikis.com/Console)
- "Using any console command will disable achievements." — [Stellaris Wiki: Console](https://stellaris.paradoxwikis.com/Console)
- `instant_build` is the one AI-relevant debug command listed; it "also applies to AI." — [Stellarisacot Wiki: Useful Debug Console Commands](https://stellarisacot.wiki.gg/wiki/Useful_Debug_Console_Commands)
- The stellaris-dashboard's autosave interval setting and its "Skip saves" option relate to saves being produced during play. — [GitHub: eliasdoehne/stellaris-dashboard](https://github.com/eliasdoehne/stellaris-dashboard)

### Inferences
- An observer-mode benchmark run needs console access, so achievements are lost for those sessions. Keep benchmark runs on a separate profile or accept that achievements are off.
- Pausing and unpausing in observer mode hands the player's empire to the AI, so a harness must avoid unpausing unless that is intended.

### Gaps
- Launch options (`-debug_mode`, `-skiplauncher`, other flags): not verified. Searches returned only forum threads and guide pages. Do not rely on any flag name without checking the game install or the Paradox forums.
- Game speed control and pause commands for automated runs: not found in the sources read. Stellarisacot's debug list does not cover them.
- Autosave frequency settings (in-game options and names): the Paradox wiki Save game page failed to load. Not verified.
- Save file locations by OS (Windows, Linux/Proton, macOS) and whether files are plain text or zipped: not verified.
- Automation tooling (AutoHotkey, pywinauto, xdotool) and watchdog file watching: not researched. No sources retrieved.

---

## Replay: deterministic replay alternatives

### Takeaway
No source in this session confirms whether Stellaris is deterministic. Save snapshots at each autosave plus a self-written event log are the practical replay substitute. Replay fidelity depends on whether the engine's own decisions can be reproduced from the logged inputs, which is an inference to test, not a sourced fact.

### Cited Findings
- stellaris-dashboard builds history from successive saves, showing a historical galaxy map and an event ledger. — [GitHub: eliasdoehne/stellaris-dashboard](https://github.com/eliasdoehne/stellaris-dashboard)
- Rerun supports scrubbing an episode and going back in time through a time-aware buffer, which suits replay of logged records. — [GitHub: rerun-io/rerun](https://github.com/rerun-io/rerun)

### Inferences
- Save snapshots give ground truth for game state at each autosave, but not the engine's inputs between saves. To replay decisions, log the observations and RNG seeds the engine uses at each tick.
- Determinism of Stellaris itself is unknown here. A test would be: load one save, run N ticks twice with identical inputs, and compare the resulting saves.

### Gaps
- Stellaris determinism and multiplayer-sync behaviour: no source read. Test empirically.
- Autosave timing (monthly vs. yearly, and whether a save captures the exact tick a decision was made): not verified.

---

## Summary of recommendations (single-PC, free/open-source)

1. Engine-side JSONL decision/event log, one record per decision with observation, inference, confidence, candidates, chosen action and outcome. Load with DuckDB (unverified tool; check before adopting).
2. Live timeline and belief-state view: Rerun (Apache-2.0/MIT, Python SDK bundles viewer, time-aware scrubbing).
3. LLM planner tracing: OpenTelemetry GenAI-style spans into a single-container Phoenix instance. Note Phoenix's ELv2 license; Langfuse (MIT) is the alternative but needs ClickHouse, Postgres and Redis for production.
4. Bandit and opponent-model metrics: TensorBoard, the same pattern ML-Agents uses (`results` directory, log during training).
5. Save-side view: follow stellaris-dashboard's pattern (watch the save directory, parse with a native parser) for historical state, but do not treat it as the decision record.
6. Observer runs: use the `observe` and `play <empire id>` console commands; plan for achievements being disabled and do not unpause in observer mode unless intended.
7. Before implementation, verify: Stellaris launch flags, autosave settings and save paths; OpenTelemetry GenAI attribute names; stellaris-dashboard's current maintenance status.
