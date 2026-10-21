# X3 plan review (Fable gate, sae-adversary, 2026-10-20)

Verdict: **ACCEPT WITH CHANGES**. Five blocking changes, all small; none touch contracts or settled decisions.

Checked against the worktree: `check_chain(min_decisions, require_applied)`, `GovernorConfig().worker_mem_cap_bytes`,
`cost.rss_peak_bytes/wall_s`, `sae-parse --version/--sections`, exit 6 for a half-truncated zip
(`parser/tests/cli.rs::truncated_zip_is_unstable`), `bench spy`, `RecordGate.lines`, replay dir layout — all exist as
the plan names them. No LLM/GPU/priority/folder/Ironman-loop violation. Fog: recordings use `RecordConfig()`
(redact_plyr on, no `GOD_VIEW_INSIDE`), saves go through the same C4 legal-section parser as live; no new leak path.

## Blocking

1. **G1 pass rule contradicts C4 (A-20).** `SaveMeta.ironman: bool | None` — None means "parser cannot tell".
   `meta.ironman is False` turns an unverified assumption into a failing test on real saves.
   Fix: G1 asserts `meta.ironman is not True` and prints the count of `None` as A-20 evidence. G6 keeps `is True`
   (that is the proof). Update plan §4 and `deferred/X3.md`.
2. **Redaction destroys the summary.** D7 replaces *every* value of a `SAE_GAME_*` var. `SAE_GAME_EXPECT_ACK=1` and
   `SAE_GAME_MAX_SAVES=24` turn every "1"/"24" in `summary.md` (counts, line numbers, ids like X3-G1) into `<VAR>`.
   Fix: redact only values that contain a path separator or are ≥ 8 chars; X3-2 test sets `SAE_GAME_EXPECT_ACK=1` and
   asserts "X3-G1" and the pass count survive.
3. **Raw saves/log leave `<out>/private/`.** D4 says the built recording lives in `<out>/private/recording`; WO X3-1
   builds it in `tmp_path_factory`; G4 (truncated save) and G8 (log copy) also use `tmp_path`. On the PC that is
   `%TEMP%`, outside the privacy split and never cleaned by the runbook.
   Fix: `run_deferred.py` appends `--basetemp <out>/private/tmp` to the pytest argv (one line); the dry run asserts
   no `*.sav` exists under the pytest default basetemp. Runbook privacy section names `private/tmp`.
4. **Time budget is fiction without a shared parse.** G1, G2, G3, G5 each spawn `sae-parse` on up to 24 saves with a
   60 s timeout; the "≤ 25 min" claim assumes one parse. `make_recording` adds a `--sections meta` pass.
   Fix: X3-1 adds a session fixture `parsed_saves -> list[tuple[Path, int, str]]` (path, exit, stdout) in
   `tests/game/conftest.py`; G1/G2/G3/G5 consume it. Add it to the X3-1 file list; X3-3 must not call `subprocess`
   for full parses itself.
5. **`test_game_marker.py` can pass vacuously.** `pytester` runs in its own rootdir; the root `tests/conftest.py`
   is not loaded there, so an unregistered marker is only a warning and "unset → skipped" would simply fail — or,
   if the implementer registers the marker in the inner test file, the test proves nothing about the real conftest.
   Fix: the WO states `pytester.makeconftest((REPO / "tests/conftest.py").read_text())` and runs the inner pytest
   with `--strict-markers`.

## Suggestions (not blocking)

- X3-U1: a real `game.log` spans reloads, so dates go backwards; "last line with date ≤ save date" is then ambiguous.
  Document that `make_recording` uses the last HELLO segment, or print a warning when dates are non-monotonic.
- G7 decodes utf-8 with cp1252 fallback; `LogTailer` uses `errors="replace"`. A real cp1252 byte makes G8 fail for a
  harness reason. Compare both sides with `errors="replace"`; report the fallback separately as X3-U2 evidence.
- Windows: `sae-parse` returns 6 on a sharing violation while the game writes. Runbook: quit Stellaris before G1.
- G5 `autosave_*.sav` glob is itself A-34 UNVERIFIED; the skip-not-fail rule in X3-3 is right, keep it.
- Disjoint file sets: batches are disjoint except item 4 above (fixture must land in X3-1, not X3-3).

Bottom line: the harness design is sound and genuinely deferred (every entry has a command, a pass rule and nothing is
claimed verified). Fix the five items above in the plan and WOs before batch 1; none needs the maintainer.

## Plan review response (T7 lead, 2026-10-20)

All 5 blocking items closed in `docs/plans/X3_plan.md` (tags [R1]..[R5]) and in the revised work orders.

| # | Fix | Where |
|---|---|---|
| 1 | G1 asserts `meta.ironman is not True`, prints the `None` count as A-20 evidence; G6 keeps `is True` | plan §4 X3-G1; WO X3-3; WO X3-5 (deferred/X3.md) |
| 2 | Redact only values with a path separator or ≥ 8 chars (+ home); test: `SAE_GAME_EXPECT_ACK=1` keeps "X3-G1" and counts | plan D7; WO X3-2 |
| 3 | Collector passes `--basetemp <out>/private/tmp`; dry run points TMPDIR/TEMP/TMP at an empty dir and asserts no `*.sav` there; runbook names `private/tmp` | plan D4, D5, D9; WO X3-2, X3-4, X3-5 |
| 4 | Session fixture `parsed_saves -> list[tuple[Path, int, str]]` in `tests/game/conftest.py` (X3-1); G1/G2/G3/G5 consume it | plan D10, §5; WO X3-1, X3-3 |
| 5 | `pytester.makeconftest((REPO / "tests/conftest.py").read_text())` + `--strict-markers` on the inner run | plan §5; WO X3-1 |

Suggestions taken: last-HELLO-segment + non-monotonic warning in make_recording (X3-U1); G8 compares with
`errors="replace"`, fallback count reported as X3-U2 evidence; runbook says quit Stellaris before G1; G5 skip-not-fail kept.
