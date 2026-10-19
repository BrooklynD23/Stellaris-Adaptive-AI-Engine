# Plan: Development and Live-Monitoring Environment for the Adaptive Stellaris AI Engine

Date: 2026-10-18. Inputs: `dev_monitoring_environment.md` (primary), `stellaris_integration.md`, `adaptive_opponents.md`, `frameworks_and_models.md`, plus 5 web lookups this pass.

**Labels.** **[V]** means verified from a source, either in the research notes or fetched this pass. **[H]** means HYPOTHESIS: a design choice or background knowledge that still needs testing. Anything without a label is a design decision.

Facts this plan relies on:
- [V] The console command `run <file>` executes a list of commands from a file in the user's Stellaris folder (Documents/Paradox Interactive/Stellaris).
- [V] `observe` switches to observer mode, and `play <id>` returns control to an empire. Unpausing while in observer mode hands the player's empire to the AI.
- [V] Any console command disables achievements, and the console is unavailable in Ironman.
- [V] Mod folder paths: Windows `Documents\Paradox Interactive\Stellaris\mod`, Linux `~/.local/share/Paradox Interactive/Stellaris/mod`.
- [V] stellaris-dashboard watches autosaves while the game runs and parses them with a Rust parser (uv, maturin).
- [V] Rerun Python API (0.27.x reference): `rr.init`, `rr.spawn`, `rr.connect_grpc`, `rr.serve_grpc`, `rr.serve_web_viewer`, `rr.save`, `rr.set_time` (the `set_time_sequence` family is deprecated), `rr.send_columns`, `rr.Scalars`, `rr.SeriesLines`, `rr.TextLog`, `rr.BarChart`, `rr.blueprint.Blueprint`, `rr.send_blueprint`.
- [V] Phoenix is ELv2, runs as one container on SQLite and accepts OpenInference/OTel traces. Langfuse is MIT but needs ClickHouse, Postgres and Redis.
- [H] Not found in sources: the launch flags (`-debug_mode`, `-skiplauncher`), the game-speed and pause console commands, the exact `log` effect and the `game.log` path (assumed `Documents/Paradox Interactive/Stellaris/logs/game.log`), the autosave cadence and format (assumed zip around a Clausewitz text gamestate), and whether Stellaris is deterministic. Two searches this pass on launch options and console speed/pause found nothing usable.

---

## 1. Repo and devenv layout

```
stellaris-adaptive-ai/
├─ pyproject.toml            # uv-managed (matches stellaris-dashboard precedent [V]); py>=3.11
├─ uv.lock
├─ justfile                  # cross-platform task runner (works on Windows w/o make)
├─ docker-compose.yml        # OPTIONAL services only; engine runs natively
├─ config/
│  ├─ default.toml           # paths, ports, cadence, gates
│  ├─ local.toml             # gitignored; per-machine paths (Windows vs Proton)
│  └─ conditions/{A,B,C}.toml# benchmark conditions (see §5)
├─ mod/                      # Stellaris mod source (descriptor.mod, common/, events/)
│  └─ events/sai_telemetry.txt   # monthly on_action → log lines "SAI|v1|<json-ish>"
├─ src/sai/
│  ├─ io/        gamelog_tail.py, save_watch.py, save_parse.py, intent_writer.py, console_bridge.py
│  ├─ adapters/  live.py, replay.py          # same GameAdapter protocol
│  ├─ model/     opponent.py (BPR/Bayesian archetype belief), bandit.py (Thompson), planner_llm.py
│  ├─ core/      engine.py (tick loop), commitment.py, fow_filter.py, rng.py
│  ├─ telemetry/ bus.py, schema.py (pydantic), otel.py, tb.py, rerun_sink.py
│  ├─ console/   api.py (FastAPI+WS), static/ (frontend)
│  ├─ control/   commands.py, snapshot.py
│  └─ bench/     runner.py, scenarios/, report.py
├─ tests/ unit/  integration/  golden/   fixtures/sessions/<id>/{game.log, saves/, bus.jsonl}
└─ .github/workflows/ci.yml
```

**GameAdapter protocol.** Every adapter implements the same interface:

```python
class GameAdapter(Protocol):
    async def observations(self) -> AsyncIterator[Observation]   # monthly ticks + autosave snapshots
    async def emit(self, intents: list[Intent]) -> EmitReceipt
    async def acks(self) -> AsyncIterator[IntentAck]              # parsed from mod log lines
    def clock(self) -> GameClock
```

- **LiveAdapter.** Tails `game.log` and watches the save directory. It writes the intent file, triggers the console `run` command, and reads acks back from the log.
- **ReplayAdapter (the "fake game").** Reads a recorded session folder: `game.log` with a wall-clock sidecar (`game.log.ts`, a byte offset to monotonic ns map captured during recording), the saves, and optionally `bus.jsonl`. Its modes:
  - `--speed realtime|Nx|max`
  - `--from <date>`
  - `--stop-at <date>`
  - `--ack-model {echo|from-recording|drop:p|delay:ms}`. This simulates the mod applying or rejecting intents, so the intent pipeline can be developed offline.
  - The engine cannot tell replay from live. A replayed observation stream is fixed: counterfactual engine decisions do not change the game. That is fine for developing inference, telemetry and the UI, but not for evaluating outcomes. Use §5 for that.
- **SynthAdapter.** A small hand-written state machine that generates archetype-flavoured observation streams (turtle, tech rush) with known ground-truth labels. It drives opponent-model unit tests and calibration checks without needing any recordings.

**Tasks (justfile).**

| Task | What it does |
|---|---|
| `just setup` | `uv sync`, then build the save parser |
| `just dev` | Engine with ReplayAdapter on `fixtures/sessions/demo`, plus console, plus Rerun |
| `just live` | Engine with LiveAdapter |
| `just console` | Console only, reading an existing bus |
| `just record <name>` | Record a live session to a fixture |
| `just replay <id> --speed 20x` | Replay a recorded session |
| `just services` | `docker compose up phoenix` |
| `just tb` | TensorBoard |
| `just test` / `just test-int` / `just lint` | Unit tests / integration tests / lint |
| `just bench <suite>` | Run a benchmark suite |
| `just report <suite>` | Build the benchmark comparison report |
| `just mod-install` | Symlink `mod/` into the user mod folder |

**Config.** Config is a TOML stack: `default.toml`, then `local.toml`, then environment variables `SAI_*`, then CLI flags. Each run writes its fully resolved config into the run folder (`runs/<run_id>/config.resolved.toml`), so every run records exactly what it used. Key settings:
- `paths.user_dir` (auto-detected for Windows and for the Proton prefix `steamapps/compatdata/281990/pfx/drive_c/users/steamuser/Documents/...` [H])
- `ports`
- `llm.endpoint`, `llm.enabled`, `llm.gate`
- `fow.mode = legal`

**docker-compose (all optional).**
- Phoenix (UI plus OTLP). Ports: the UI on 6006 is common knowledge [H]. TensorBoard is moved to 6007 to avoid a clash.
- Ollama, for Linux. On Windows, run llama.cpp or Ollama natively for GPU access.
- The engine itself stays outside Docker. It needs filesystem access to Documents, low latency and Windows input injection.

**Tests.**
- **Unit tests.** Log-line parser (fuzzed with hypothesis), save-parse field extractors, Bayesian update math (posterior sums to 1, monotone in evidence), Thompson posterior updates, commitment-timer logic, fog-of-war filter (property: no field tagged `god` ever reaches `Observation`), and intent serializer (golden files of the generated `.txt` command files).
- **Integration tests.** ReplayAdapter on 2–3 short fixtures (about 10 game-years each). These assert the event-type sequence, the bus schema version, determinism of the engine given a seed (same bus hash on two runs), and the LLM path through a stub server that returns canned JSON plus one invalid output (to exercise validation rejection).
- **Golden tests.** A frozen `bus.jsonl` per fixture. Any diff fails the test unless it is regenerated with `just golden-update`.
- **CI (GitHub Actions).** Matrix of ubuntu-latest and windows-latest × py3.11/3.12. Steps: `uv sync`, ruff, mypy, pytest unit plus integration (replay only; no game, no GPU), a bus-schema compatibility check, and the frontend build. Fixtures over 50 MB go in Git LFS or a release asset. Optional job: lint the mod with a Clausewitz syntax linter if one is available [H].

---

## 2. Telemetry design

**One event bus, append-only.** Each run writes to `runs/<run_id>/bus.jsonl`. The file is flushed per event and rotated per game-year. A background compactor converts closed segments to Parquet (`bus/year=2231.parquet`). Every other consumer reads from the bus: the console, Rerun, TensorBoard and DuckDB. The engine never writes to the UIs directly, except through sinks that subscribe to the bus. The bus is therefore also the recording format for replay and audit.

**Envelope (pydantic, `schema_version`).**

```json
{"v":1,"run_id":"…","seq":18231,"t_wall":"2026-10-18T21:03:11.402Z","t_mono_ns":…,
 "game_date":"2231.04.01","tick":2655,"type":"belief_update","decision_id":"d-2231.04-07",
 "trace_id":"…","span_id":"…","fow":"legal","payload":{…}}
```

**Event types and payloads.**

| type | payload essentials |
|---|---|
| `observation` | source (`log` or `save`), `lag_ms`, a features dict (only fog-of-war-legal fields), `feature_hash`, `stale_fields` |
| `observation_god` | the same fields from the full save; written to a **separate file** `bus.god.jsonl`. It is never an input to the engine. |
| `belief_update` | `prior[]`, `likelihood[]`, `posterior[]` over archetypes, entropy, `top_evidence[]` (feature, log-LR) |
| `strategy_selected` | arm samples, posterior params per arm (α/β or μ/σ), chosen arm, reason (`sample`, `forced`, `commitment_hold`), `commit_until` |
| `llm_call` | model, quantization, prompt hash and text (opt-in), schema id, raw output, `parsed_ok`, validation errors, tokens in/out, `latency_ms`, gate reason, `accepted` |
| `intent_emitted` | `intent_id`, knob, value, file path, `console_cmd`, `emit_ms` |
| `intent_ack` | `intent_id`, status (`applied`, `rejected`, `timeout`), `mod_reason`, ack lag in game days |
| `outcome` | horizon (for example 5 years), deltas versus baseline (military, economy, territory, war score), reward fed back to the bandit |
| `control` | operator commands (§4), so manual overrides are auditable |
| `health` | tail lag, parse errors, save age, process CPU and RAM |

**Decision audit record.** This is not a separate store. It is a DuckDB view joining every row that shares a `decision_id`: observation, then belief_update, then strategy_selected, then llm_call, then intent_emitted, then intent_ack, then outcome (which arrives later). The view is `v_decisions` in `sql/views.sql`.

**DuckDB.** Queries run directly over `read_json_auto('runs/*/bus*.jsonl')` and Parquet files. Typical uses: archetype calibration (Brier score per fixture), the intent rejection rate by knob, and LLM acceptance rate against latency. DuckDB was not verified in the research; it is a low-risk choice [H].

**LLM spans.** OpenInference/OTel through the OTLP exporter to Phoenix. Span tree per planning cycle: `plan_cycle` → `gate_check` → `llm.generate` (attributes for model, tokens, latency) → `schema_validate` → `intent_merge`. The span and trace IDs are copied into the bus row, so the console can deep-link to Phoenix. The exact GenAI attribute names (`gen_ai.request.model`, `gen_ai.usage.input_tokens`) are [H]: check `open-telemetry/semantic-conventions-genai` before freezing them. Langfuse is the swap-in if MIT licensing matters, at the cost of a heavier stack [V].

**TensorBoard.** Scalars written per game-month (global_step = tick):
- `bandit/arm_k/mean` and `bandit/arm_k/n`, plus `bandit/regret_proxy`
- `opp/entropy`, `opp/brier` (replay with labels only)
- `llm/accept_rate`, `llm/p95_latency`
- `intent/ack_rate`

Logs go to `runs/<run_id>/tb/`, following the same pattern as ML-Agents [V].

---

## 3. Live "Engine Console"

### Stack options

| Option | Pros | Cons | Use for |
|---|---|---|---|
| **Rerun** [V API] | Zero frontend code. Named timelines (`game_date`, `tick`) with scrubbing. Time series, text logs and bar charts. Saves to `.rrd` for replay. Blueprints fix the layout. | Not a control plane. Weak at nested JSON inspection (prompts, schemas). The layout is opinionated. | Phase 0 timelines: belief and posterior scrubbing, post-hoc replay |
| **Streamlit** | Fastest to build. Python only. | The rerun-the-script model fights high-rate live streams and keeps state poorly. Buttons are fine. | Throwaway Phase 0 control panel only |
| **FastAPI + WebSocket + light SPA** (Preact or Svelte, uPlot for charts) | True push and low latency. Full control of the audit feed, LLM inspector, controls and the god-view toggle. One process. | Frontend work. | **Phase 1+ primary console** |
| Grafana + Prometheus | Good at health metrics. | Poor fit for decision audits. Extra services. | Not recommended |

**Recommendation (phased).**
- **P0, week 1.** Bus plus a Rerun sink (`rr.init("sai")` and `rr.spawn()`; `rr.set_time("tick", sequence=…)` [H on kwarg form]; `rr.Scalars` for beliefs and arms; `rr.TextLog` for decisions). Add TensorBoard and a Phoenix container. A CLI control client (`sai ctl pause`) talks to a FastAPI control endpoint.
- **P1.** The FastAPI console below. It subscribes to the bus tail, backfills from DuckDB on load, and pushes deltas over WebSocket.
- **P2.** A replay scrubber in the console, served by the same API through `?run=<id>&at=<tick>`. Rerun `.rrd` export for deep dives. Benchmark report pages.

**Data flow.** Engine → `bus.jsonl` → a `BusTailer` that fans out to: the WebSocket hub (console), the Rerun sink, the TensorBoard writer and the Parquet compactor. The console never queries the engine for state. Everything it shows comes from the bus, so what you see live matches what you see in replay.

### Wireframe

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ SAI ENGINE CONSOLE  run=2026-10-18_2103_live  mode:LIVE ●   [FOG: LEGAL]  ( ) GOD VIEW  │
│ Game 2231.04.01 tick 2655 | log lag 140ms ● | last autosave 2231.01.01 (3mo, 41s ago) ● │
│ parse err 0 | intents in-flight 2 | engine: RUNNING  learning: ON  LLM: gated(idle)       │
│ [Pause] [Freeze learning] [Force strategy ▾] [Override intent] [Snapshot] [● Rec]        │
├───────────────────────────────────────────┬──────────────────────────────────────────────┤
│ BELIEF OVER ARCHETYPES (posterior ±90%CI)  │ BANDIT ARMS (Thompson posteriors)            │
│ 1.0┤        turtle ▓▓▓▓▓▓▓▓▓▓▓░░          │ arm            mean  95%CI      n  last θ    │
│    │      ▓▓▓                    ░ tech    │ deep_strike    .61 [.44,.77]  14  .66 ◄sel   │
│ 0.5┤ ▓▓▓▓       ░░░░░░░░░░░░░░             │ siege_econ     .55 [.35,.73]  11  .49        │
│    │░░░░░░░░░░░░   ···· expand             │ tech_parity    .48 [.30,.66]   9  .52        │
│ 0.0┼────┬────┬────┬────┬────► game date    │ raid_harass    .40 [.18,.65]   5  .58        │
│   2225 2227 2229 2231   entropy 0.71 ▼     │ [density sparklines per arm]                 │
├───────────────────────────────────────────┼──────────────────────────────────────────────┤
│ COMMITTED STRATEGY                         │ INTENT PIPELINE                              │
│ deep_strike  since 2230.10  ██████░░░ 7/12mo│ emitted 38 | applied 33 | rejected 2 | t/o 1 │
│ re-eval at 2231.10 | switch cost 0.12      │ i-0381 ai_budget.military=0.45  APPLIED +1d  │
│ guard: min-strength floor OK (0.93×base)   │ i-0382 war_target=E7          PENDING 0d     │
│                                            │ i-0379 econ_plan=alloys_hi    REJECTED       │
│                                            │       "flag sai_lock set"                    │
├───────────────────────────────────────────┴──────────────────────────────────────────────┤
│ DECISION AUDIT FEED  (filter: type ▾ archetype ▾ only-switches ☐)                         │
│ d-2231.04-07  OBS: chokepoint starbases +2, fleet power flat, 3 techs/yr↑                │
│   → INFER turtle .68 (+.09)  conf MED   → CANDIDATES deep_strike .66 | siege .49 | …      │
│   → CHOSEN deep_strike (commit hold)  → INTENTS i-0381,i-0382  → OUTCOME (pending 5y)    │
│ d-2230.10-02  … OUTCOME +0.21 war score, reward 0.62 ✓                     [expand ▸]    │
├───────────────────────────────────────────┬──────────────────────────────────────────────┤
│ LLM CALL INSPECTOR  (call l-0091)          │ ECONOMY / MILITARY vs PLAYER  [LEGAL]        │
│ gate: entropy>0.6 & 12mo since last        │ fleet power  ── self  ┄┄ player(est ±band)   │
│ model qwen-small Q4 | 412→96 tok | 1.8s    │ alloys/mo    ── self  ┄┄ player(est, stale 3mo)│
│ schema: plan_v3  VALID ✓  accepted ✓       │ systems      ── self  ┄┄ player(observed)    │
│ [prompt] [schema] [raw out] [diff vs bandit]│ ⚠ GOD VIEW overlay (red, hatched) when on   │
│ ↗ open trace in Phoenix                    │                                              │
└───────────────────────────────────────────┴──────────────────────────────────────────────┘
```

**Panel specs.**
- **Clock and health strip.** Shows the game date and tick, and the log tail lag (time from the log line's mtime to processing). The tail lag goes yellow above 2 s and red above 10 s, or when the expected monthly line is missing. Also shows save age in game months and in wall-clock time, parse errors, and the engine, learning and LLM states. The tailer detects truncation or rotation of `game.log` (the log is likely rewritten when the game launches [H]) and shows a RECONNECT banner.
- **Belief timeline.** Stacked or line posterior per archetype, with a band from a bootstrapped credible interval (Dirichlet or particle). Shows the entropy trend. Clicking a point opens the matching `belief_update` with its top evidence.
- **Bandit arms.** Posterior mean and CI, pull count, the last sampled θ, and the selected arm marked. Small density plots. A "forced" badge appears on operator override.
- **Commitment panel.** Shows the strategy, its start date, a progress bar to the re-evaluation point, the switch hysteresis and the safety floor status (the restricted-Nash-style guard from `adaptive_opponents.md`).
- **Audit feed.** One collapsible row per `decision_id` covering the full chain. Rows whose outcome is still pending update in place when the outcome arrives. Filters: switches only, LLM-involved, rejected intents.
- **LLM inspector.** Shows the gate reason, prompt (redacted by default), schema, raw output, validation errors, latency, tokens and the accept/reject result. Includes a diff of the plan against the bandit's choice and a Phoenix deep link.
- **Intent pipeline.** Counters plus the latest N intents, each with emitted, applied and rejected status and the ack latency in game days. If an intent has no ack within K months it is marked "timeout", which usually means the `run` command failed.
- **Economy and military versus player.** Only data from `observation` events, with estimates drawn as uncertainty bands and staleness labels. The **GOD VIEW** toggle overlays `observation_god` data in red hatching and changes the top bar to a red "GOD VIEW: DEBUG ONLY". The toggle is client-side only and is logged as a `control` event. The engine process cannot read the god file: it is opened only by the console's read-only route. A unit test enforces this boundary.

---

## 4. Control plane

The control plane is a FastAPI `POST /control/<cmd>` endpoint, also available from the CLI as `sai ctl`. Every command becomes a bus `control` event with an operator, a reason and a timestamp. The engine applies commands at the next tick boundary. None of them edit game state directly; all game effects still go through the intent pipeline.

| Command | Semantics |
|---|---|
| `pause` / `resume` | Stops decision-making. Observation and logging continue. No intents are emitted. |
| `force_strategy {arm, until}` | Bypasses Thompson sampling. Rewards for forced periods are tagged and **excluded** from posterior updates unless `learn=true`. |
| `override_intent {knob, value, ttl}` | Injects an operator intent, marked `source=operator`. |
| `freeze_learning` | Bandit and opponent-model updates stop. Inference continues. |
| `llm {off\|on\|force_once}` | Controls the planner gate. |
| `snapshot` | Serializes engine state (beliefs, bandit params, commitment, RNG states, config hash, bus `seq`) to `runs/<id>/snapshots/<tick>.msgpack`, paired with the latest save filename. |
| `restore {snapshot}` | Loads the engine state. With live play, the operator loads the matching save in-game. With replay, it seeks the adapter. |
| `record start/stop` | Copies `game.log` increments and the wall-time sidecar, new saves and the bus into `fixtures/sessions/<name>/`. Produces a replayable fixture (§1). |

Safety: if the intent file write or the `run` trigger fails 3 times in a row, the engine automatically pauses and raises a health alert.

---

## 5. Benchmark automation

**Harness.**
1. Launch Stellaris with the mod. Launch flags are [H]: probe for `-skiplauncher` and `-debug_mode` and record what works.
2. Load a scenario save.
3. Inject console commands with pywinauto or AutoHotkey on Windows, or xdotool on Linux and Proton. All three are [H]. The commands are `observe`, then `run sai_setup.txt`, then unpause.
4. Advance the game at maximum speed. The speed and pause commands are [H], so fall back to keystroke speed keys.
5. Stop at the horizon date, then `save`, then quit.
6. Record the bus, saves and log as a fixture.

A watchdog kills and restarts the game if the log stalls for more than N seconds. [V] Observer mode means unpausing hands the player's empire to the AI, which is exactly what AI-vs-AI runs want. It also means console use, so achievements are off: use a dedicated benchmark profile.

**Scenarios.**
- Start saves crafted per archetype. A "player" empire is driven by a **scripted opponent mod profile**: fixed `ai_budget`, `economic_plans` and weights files that make the vanilla AI behave like a chokepoint turtle (defense-heavy budget, starbase spam, low expansion), a tech rush (research plans), or an expander. This is [H]; how well vanilla AI levers can express an archetype is unverified, so validate each profile with the opponent model's own labels on SynthAdapter-free runs.
- Scenarios are YAML: start save, player empire id, profile, horizon (for example 2200 to 2300), engine condition, seeds and N repetitions.

**Conditions.**
- **A**: vanilla AI (engine attached and logging, all intents suppressed). This is the telemetry-equal control.
- **B**: engine with a fixed strategy library (no bandit, rule-based archetype response).
- **C**: full engine (Bayesian belief plus Thompson plus the gated LLM).
- **C'**: C without the LLM (ablation).

**Batch runner.**
- Runs sequentially on one PC, roughly 30–90 minutes per 100-year run at maximum speed [H].
- Schedules overnight queues, can resume after a crash, and randomizes condition order to counter drift from thermal throttling or background load.

**Results database.** `bench.duckdb` with tables `runs` (condition, scenario, seeds, game version and checksum, mod hash, engine git SHA, config hash), `metrics_yearly` (from saves), and `outcomes`. Primary metrics:
- win or survival and war-score integral
- military and economy ratio versus the player at +25, +50 and +100 years
- archetype classification accuracy and time-to-confident
- intent ack rate
- LLM accept rate and wall-clock cost

**Reports.** `just report` produces an HTML report comparing A, B and C per scenario:
- medians with bootstrap CIs
- Mann-Whitney U or permutation p-values
- effect sizes
- trajectory fan charts
- a "decisions that mattered" appendix pulled from `v_decisions`

A comparison is flagged "underpowered" if N is below the threshold given by a pilot variance estimate.

---

## 6. Reproducibility caveats and mitigations

- **Stellaris determinism is unknown [H].** Assume it is not deterministic: there may be no seed control, multithreaded simulation and timing effects, and the AI may be stochastic. Mitigations:
  - Run an empirical determinism probe early: load one save, observe 5 years twice, and diff the saves (`just determinism-probe`).
  - Treat outcomes as distributions: use N repetitions and paired designs (the same start save across conditions).
  - Report CIs, never single runs.
- **Engine determinism (controllable).** Seed all RNGs per run and per decision (`rng.py` derives seeds from `run_seed` and `decision_id`). Use LLM temperature 0 with a fixed seed and model hash. Record the model file SHA. The test is: same replay fixture plus same seed gives a byte-identical bus, excluding wall-clock fields.
- **Timing nondeterminism in the bridge.** The game tick at which `run` executes depends on keystroke latency. Log the emit tick and the ack tick, and have the mod stamp the applied date. Analysis uses the ack date, not the emit date.
- **Observation cadence.** Autosave intervals and log flush timing vary [H]. Store `lag_ms` and `stale_fields` on every observation. Replay uses the recorded wall-time sidecar, so the timing seen in live sessions is reproduced.
- **Version drift.** Pin the game version and checksum (the last verified release is 4.4.4, June 2026 [V]; anything newer is unverified), the mod hash, the engine SHA and the resolved config in every run row. Never compare across checksums without a flag.
- **Replay cannot test counterfactual outcomes.** Use replay only for inference, telemetry, UI and regression. Use live benchmark batches for policy quality.
- **Fairness audit.** A CI test plus a runtime assertion check that no `god`-tagged field reaches the engine. The console's god view is visually distinct and logged.

---

## Open items to verify before build
1. Stellaris launch flags, the speed and pause console commands, and the `log` effect syntax with its `game.log` path and flush behaviour.
2. Whether `run` can be triggered reliably by injected keystrokes at maximum speed, and how long it takes.
3. Autosave cadence options and save format, and whether jomini/rakaly or the stellaris-dashboard parser can be reused.
4. Phoenix ports and OTLP endpoint, and the OTel GenAI attribute names.
5. The `rr.set_time` keyword form in the pinned Rerun version.

Sources fetched this pass: [Rerun Python API 0.27.3](https://ref.rerun.io/docs/python/0.27.3/common/). Searches that returned nothing usable: [Stellarisacot debug commands](https://stellarisacot.wiki.gg/wiki/Useful_Debug_Console_Commands) (no observe, speed, pause or launch-flag info) and Phoenix deployment search results (third-party Railway templates only, not used). The other sources are cited in the input notes.
