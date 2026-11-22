# SAE — Stellaris Adaptive Engine

Open-source adaptive AI companion for modded Stellaris: a Python daemon plus a Rust save parser plus a Clausewitz
mod (`sae_bridge`). The engine reads `SAE|v1|` log lines and autosaves, models the player's strategy with fog-of-war-
legal data, and steers the AI empire through bounded Intents applied via a console `run` file.

## Where things are
- `context/` — design report, research notes, resource architecture and the expansion proposal. Read-only inputs.
- `contracts/` — frozen interface contracts C1–C10 and the change protocol. `src/sae/contracts/` holds the typed
  models that implement them.
- `.claude/agents/` — subagent definitions: sae-architect, sae-team-lead (opus), sae-implementer (sonnet),
  sae-scout (haiku), sae-reviewer (opus), sae-adversary (fable 5.2 medium). They carry the standing rules and
  hand-back shapes, so work orders stay short.
- `docs/TEAM_PLAN.md` — teams T1–T9, owned paths, deliverables. `docs/status/` and `docs/plans/` per team.
- `docs/SPRINTBOARD.md` — master progress board (waves, components, tests, open follow-ups). When asked for progress,
  read it, verify it against git and the status files, report drift, and update it in the same change set as the work.
- `docs/process/` — `DISPATCH_TEMPLATE.md` (work orders, test-area map), `RESERVATIONS.md` (shared ids),
  `WORKFLOW_OPTIMIZATION.md` (why the process looks like this).

## Settled decisions (do not reopen without the maintainer)
No LLM in the runtime loop. No GPU use on the player's machine through M4. Three processes (daemon, per-autosave
parser that exits, optional telemetry). Never touch the game's priority or affinity. Ironman has no live loop.
v0/v1 strategy selection is a belief → counter-strategy table with 24-month commitments and a safety floor.

## Rules for every agent
- Git: before recommending or running any git command, identify out-of-date docs (README.md, docs/, API specs),
  update them, record the changes in CHANGELOG.md in its existing format, and present the diffs to the maintainer
  for approval. Run no git write command (commit, push, stash, checkout, reset, branch, worktree, merge,
  rebase) until the maintainer approves. Read-only git (`git diff`, `git status`, `git log`, `git show`) is allowed
  for every agent, so reviewers can read the change set (maintainer P5, 2026-11-17).
- Never edit another team's owned paths; use contracts/proposals/. Only the coordinator or the team lead edits
  `changelog.d/`; implementers return the fragment line.
- Never launch Stellaris, modify its install folder or write to the user's mod folder without explicit permission.
- Stellaris script, console and save assumptions are UNVERIFIED until M0 proves them; label them so in code and docs.
- Build the spec. Anything beyond it (extra clamp, field, default) is reported as BEYOND-SPEC, never silent.
- Context economy: read file ranges, not whole files; pipe long output through `tail`; plans stay under 2,500
  words and work orders cite their sections.

## Review gates (maintainer rule 2026-10-19; models 2026-11-14/15; tiering 2026-11-16)
1. Plan gate: every plan gets one adversarial review by `sae-adversary` before implementation starts. Planning,
   architecture design, contract drafting and adversarial reviews run on Fable 5.2 at medium effort (Agent
   `model: "fable"`, `effort: "medium"`). One review per wave plan, not per work order.
2. Code gate: `sae-reviewer` (Opus 5.5) reviews each wave's change set line by line.
3. Architecture gate: after each implementation or wave, `sae-adversary` reviews the result against the overall
   architecture and reports ON TARGET / AT RISK / OFF TARGET for: (1) performance and memory management,
   (2) telemetry, (3) a properly adaptive AI engine, (4) readiness to learn the player's profile and strategies
   from their saves (the bounded RSI expansion). A wave is not done until that review is addressed.
4. Closure: reviewers tag each blocking item SEMANTIC (changes code behaviour, contract meaning, a threshold, or a
   safety, fog or data-loss property) or TEXT (wording, numbering, cross-references). A closure check by the
   reviewer runs only when a SEMANTIC item was fixed, and reads only the changed sections or files. The coordinator
   closes TEXT items itself and records "closed by coordinator" in the review file. Fix loops inside a wave need no
   new plan review. When in doubt an item is SEMANTIC.
Open validation tests live in `docs/TEAM_PLAN.md` "Validation backlog".

## Testing: test what matters
A test earns its place when it would fail if behaviour a player, another team's contract or a safety rule depends
on broke. Write down that behaviour in the test name or docstring.
- Test through the public entry point (function, CLI, contract model, bus event), on realistic input: the C4
  fixtures, synth campaigns, recorded logs.
- Priorities: fog-of-war legality, validator and safety floor, persisted state and atomic writes, record/replay
  determinism, contract round-trips, governor limits. Use property tests there, and a mutation check for every
  fix: revert the fix, a test must go red. Report the check.
- Do not write: tests that read source text (`inspect.getsource`, grepping `src/`; the fog and import boundary
  lints in `test_boundary.py` and `test_import_guard.py` are the exception); tests of private helpers
  already covered through the public path; tests that mock the unit under test; one test per trivial getter or
  per impossible input; a second test for a behaviour that already has one.
- Never change an expected value, relax an assertion (`== 1` to `>= 1`), drop a case or add skip/xfail to get
  green. If the behaviour change is intended, update the test and report it as RE-PINNED (test, old -> new, the
  behaviour change); the reviewer checks each one.
- Stop rules are fixed before the run (for example CI-1 pooled rate >= 0.90). Never edit a threshold to pass; a
  miss is reported as "not demonstrated" with the number.
- Measured claims (speed, memory) need a before and an after from the same machine state, or say "not demonstrated".

Tiers (commands and the src -> tests map: `docs/process/DISPATCH_TEMPLATE.md`):
1. Targeted, while iterating: `uv run pytest -x -q <test files you touched>`.
2. Affected area, once before every hand-back: the work order's AREA command
   (`uv run pytest -x -q -m "not slow" <area paths>`), plus `uv run ruff check` and `uv run mypy --strict` on the
   touched packages; in `parser/`: `cargo clippy -j 1 -- -D warnings`, `cargo test -j 1`.
3. Full, once per wave, coordinator only, after all hand-backs: `uv run pytest -q` (includes `slow`),
   `uv run mypy --strict src`, `uv run ruff check`. Never two pytest processes at once on this machine; subagents
   never run tier 3.

## Shared ids
Contract versions, assumption ids (A-xx) and proposal numbers are reserved in `docs/process/RESERVATIONS.md`
before any plan, proposal or work order uses them. Take the next free value, add your row, then write. A value
not in the ledger is not yours.
