# X4 alignment review (Fable gate, CLAUDE.md rule)

Date: 2026-10-20. Reviewer: sae-adversary (Fable 5.1). Scope: `git diff` + untracked files on
`feat/x4-tune-gate-scaffold` (worktree `scratchpad/x4`). No code edited; this file is the only write.

## Verdict: ACCEPT WITH CHANGES (3 doc-only fixes before commit)

| Axis | Rating | Evidence |
|------|--------|----------|
| 1. Performance and memory | ON TARGET | tune/gate are offline CLIs, never in the daemon loop; stdlib-only `metrics.py`; `fast_lls` precomputed path for the grid. Runtime <= 30 min is a measured target in X4-G1, not a claim. |
| 2. Telemetry | ON TARGET | No new runtime telemetry (none needed for offline CLIs). Policy sha surfaces in `run_started.policy.sha256`; X4-G5 checks it. Evidence files + append-only registry give an audit trail. |
| 3. Properly adaptive engine | ON TARGET | Policy swap is out-of-run only ("a policy never changes during a run"); C3 belief -> counter table untouched; 24-month commitments honoured via `strategy_trace` using the real `engine.on_observation`. Settled decisions not reopened. |
| 4. Readiness to learn from saves (bounded RSI) | ON TARGET | LOCO shrinkage fit over fog-legal `Observation`s only (`ledger.labelled_campaigns` -> `summary.kept_observations`); labels human, used for scoring only; one sealed query per candidate enforced twice (`stage2.py:131,141`); `--include-synth` never touches live sealed data (`stage2.py:144-145`) and writes no registry row. Human `approve` sits between `stage2_pass` and `promote`. |

## Gates re-run (this worktree, SAE_GAME_* unset)

| Gate | Result |
|------|--------|
| `uv run pytest tests/unit/t7 tests/unit/t9 tests/test_cli.py` | 248 passed, 3 skipped (game), exit 0 |
| Coverage of X4 packages (`sae.bench.gate`, `sae.tune`, `sae.profile.ledger`, `sae.profile.policy_cli`) | 96.10 %; lowest file `tune/cli.py` 93 % |
| `uv run mypy --strict src` (the CI invocation) | clean, 105 files |
| `uv run ruff check` | clean (the E501 at `test_gate_stage1.py:33` is fixed) |
| `uv run pytest -m game tests/unit/t7/test_gate_game.py tests/unit/t9/test_tune_game.py -rs` | `sss`, 3 skipped, exit 0 |
| Full suite | run by the Gates phase (exit 0, no F/E reported); not re-run to completion here, 3 waves share 6 cores |

`mypy --strict` over `tests/` shows 15 pre-existing errors in `t9/test_cli.py`, `test_model.py`, `test_summary.py`
(none in X4 files; CI only checks `src`). Not an X4 blocker.

## Deferred items (docs/handoff/deferred/X4.md)

Every game-dependent item (G0-G5, Fallback) has a runnable command, an input list and a pass rule. Nothing is
claimed verified: V-1/V-3 are "NOT VERIFIED" in `docs/status/T9.md:14-15`, `docs/components/T9.md:27-30`,
`changelog.d/X4.md:4`; the Fallback block says synth output "must never be quoted as V-1 or V-3 met". Stage 2 is
labelled "tripwire, not a significance test" in code and doc. Good.

## Must fix before commit (doc only, all X4-F/T8)

1. `docs/handoff/deferred/X4.md:4`, `:61`, `:70`: still say `test_gate_game.py` is "pending X4-E" / "not in the tree
   yet". The file exists and skips cleanly. Fix: delete the pending sentences and the `# file pending X4-E` comment.
2. `docs/handoff/deferred/X4.md:72`: add `SAE_GAME_RUNS_DIR` (optional, defaults to `<sae_dir>/runs`) to the G3
   inputs; `test_gate_game.py:33` reads it.
3. `docs/handoff/deferred/X4.md:9`: the quoted skip reason `"set SAE_GAME_SAE_DIR (X4-G1)"` matches neither test
   (`test_tune_game.py:40` says `(X4-G1; needs 6 ...)`, `test_gate_game.py:25` says `(X4-G3; ...)`). Fix: say the
   reason starts with `set SAE_GAME_SAE_DIR (X4-G`.

## Later (not blocking)

- `pyproject.toml` `[tool.pytest.ini_options]`: register `markers = ["game: ..."]` and add `--strict-markers`.
  Today `-m game` works but a typo on the game PC selects 0 tests silently.
- `tests/unit/t7/test_gate_game.py:48-49`: Stage 2 refusal (exit 2) goes to stderr; the assertion message shows
  only stdout. Use `cap2 = capsys.readouterr(); assert s2 in (0, 1), cap2.out + cap2.err`.
- `src/sae/profile/ledger.py:111-123` `_is_live` returns True for a campaign whose bus files are all missing. Such a
  campaign has no observations anyway, so it cannot leak data, but it counts as "live" in the split. Return False
  when no `run_started` was read.
- `tests/unit/t7/test_gate_stage2.py:148` comment claims a case ("synth may follow a real query") that no test
  covers; reword or add the case.

## Bottom line

X4 scaffolds the tune/gate/policy loop without touching the runtime, keeps the sealed slot single-use and keeps
every real-data number labelled unverified, so the architecture holds on all four axes. Three stale "pending" notes
in the deferred doc are the only things standing between this wave and a clean commit.

## Follow-up (2026-10-21)

Re-checked on main as merged into feat/x3-game-test-harness (6f30a96). Must-fix 1 DONE (`deferred/X4.md:4,61`; residual "once they land" wording on line 5 is cosmetic), 2 DONE (`X4.md:72` lists `SAE_GAME_RUNS_DIR`, matches `test_gate_game.py:34`), 3 DONE (`X4.md:9` prefix matches both tests). "Later" items not re-audited. No change to the ACCEPT verdict.
