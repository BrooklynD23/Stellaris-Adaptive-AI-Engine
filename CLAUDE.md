# SAE — Stellaris Adaptive Engine

Open-source adaptive AI companion for modded Stellaris: a Python daemon plus a Rust save parser plus a Clausewitz
mod (`sae_bridge`). The engine reads `SAE|v1|` log lines and autosaves, models the player's strategy with fog-of-war-
legal data, and steers the AI empire through bounded Intents applied via a console `run` file.

## Where things are
- `context/` — design report, research notes, resource architecture and the expansion proposal. Read-only inputs.
- `contracts/` — frozen interface contracts C1–C9 and the change protocol. `src/sae/contracts/` holds the typed
  models that implement them.
- `.claude/agents/` — subagent definitions: sae-architect, sae-team-lead (opus), sae-implementer (sonnet),
  sae-scout (haiku), sae-reviewer (opus), sae-adversary (fable).
- `docs/TEAM_PLAN.md` — teams T1–T8, owned paths, deliverables. `docs/status/` and `docs/plans/` per team.

## Settled decisions (do not reopen without the maintainer)
No LLM in the runtime loop. No GPU use on the player's machine through M4. Three processes (daemon, per-autosave
parser that exits, optional telemetry). Never touch the game's priority or affinity. Ironman has no live loop.
v0/v1 strategy selection is a belief → counter-strategy table with 24-month commitments and a safety floor.

## Rules for every agent
- Before recommending or running any git command: identify out-of-date docs (README.md, docs/, API specs), update
  them, record the changes in CHANGELOG.md in its existing format, and present the diffs to the maintainer for
  approval. Run no git command until the maintainer approves.
- Never edit another team's owned paths; use contracts/proposals/.
- Never launch Stellaris, modify its install folder or write to the user's mod folder without explicit permission.
- Stellaris script, console and save assumptions are UNVERIFIED until M0 proves them; label them so in code and docs.
- Fable review gates (maintainer rule, 2026-10-19): every plan gets an adversarial review by `sae-adversary`
  (Fable 5.1) before implementation starts. After each implementation or wave, `sae-adversary` reviews the result
  against the overall architecture and reports ON TARGET / AT RISK / OFF TARGET for: (1) performance and memory
  management, (2) telemetry, (3) a properly adaptive AI engine, (4) readiness to learn the player's profile and
  strategies from their saves (the bounded RSI expansion). A wave is not done until that review is addressed.
  Open validation tests live in `docs/TEAM_PLAN.md` "Validation backlog".
- Tests: `uv run pytest -x -q`, `uv run mypy --strict`, `uv run ruff check`, and in `parser/`:
  `cargo clippy -j 1 -- -D warnings`, `cargo test -j 1`.
