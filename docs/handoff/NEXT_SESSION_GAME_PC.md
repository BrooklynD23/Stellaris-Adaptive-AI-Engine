# Hand-over: next session on the Stellaris PC (2026-10-20)

Development continues on the PC that has Stellaris installed. `main` holds the merged MVP (PR #1, `299e13f`).
Three wave branches are pushed and NOT merged yet. Each has an open draft PR.

## State of the three branches

| Branch | Wave | Done | Still to do before merge |
|---|---|---|---|
| `feat/x2-m0-field-kit` | X2 field kit: CI artifact `sae-m0-field-kit-windows`, `tools/m0/field_kit/setup.ps1`, `sae m0 doctor`, `sae m0 pack` | plan + Fable plan review, 4+ change sets implemented and reviewed | full gates; Fable alignment review (`docs/status/ARCHITECTURE_alignment_review_4.md`); CI green |
| `feat/x3-game-test-harness` | X3 `game` marker, `tests/game/**`, `tools/game/run_deferred.py`, this folder's runbook | plan + Fable review, 6 change sets approved, Fable alignment actions fixed | full pytest + coverage (the last run stalled at 45% on `tests/unit/t4/test_boundary.py::test_rss_growth_120_year_run_under_150_mib` while three waves shared 6 cores; rerun alone); Fable follow-up on `docs/status/X3_alignment_review.md`; CI green |
| `feat/x4-tune-gate-scaffold` | X4 `sae tune detector`, `sae gate stage1/stage2`, `sae policy …` (proposal 029) | everything incl. final gates green (coverage 96.61%) and alignment actions fixed | Fable follow-up on `docs/status/X4_alignment_review.md`; CI green |

Merge order: X4, X3, X2 (X2 touches `.github/workflows/ci.yml`; X3 and X4 do not). Each merge needs the four
required checks (`python-linux`, `mod`, `rust-linux`, `windows`). Expect conflicts only in `CHANGELOG.md`,
`docs/TEAM_PLAN.md`, `docs/BUILD_REPORT.md` and possibly `tests/conftest.py` / the `game` marker (X3 owns the global
marker; X4 may have a local one; keep X3's).

## Then, on the game

1. Read `docs/handoff/GAME_PC_AGENT.md`; it orders every deferred item (critical M0 items first) and indexes
   `docs/handoff/deferred/*.md` (X2, X3, X4).
2. Set the `SAE_GAME_*` inputs and run `tools/game/run_deferred.py` (wraps `pytest -m game`).
3. Record results in `docs/handoff/GAME_PC_RESULTS_TEMPLATE.md` and `m0_results/`. No raw game log or save leaves
   the PC unless the maintainer says so.

## Follow-ups that are not game-bound

- Add a Rust coverage gate (>= 90 %) to the `rust-linux` CI job after X2 merges. Measured 2026-10-20 with
  `cargo llvm-cov`: 96.97 % lines; lowest `output.rs` 80.43 %.
- Fable review gates stay mandatory (CLAUDE.md): every plan before code, every wave after.
