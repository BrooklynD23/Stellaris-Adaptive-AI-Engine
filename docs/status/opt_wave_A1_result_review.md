# Opt wave A1 — post-implementation review (sae-adversary)

Date: 2026-11-14. Scope: uncommitted diff on `feat/x2-m0-field-kit` (14 modified files, 2 new). Read-only review.

| # | Change | Verdict |
|---|---|---|
| 1 | Windows `rss_peak_bytes` → `PeakPagefileUsage` + proposal 030 | ACCEPT WITH CHANGES (blocking 1, 2) |
| 2 | fsync gated by adapter mode | ACCEPT |
| 3 | `stdin=DEVNULL` in `sae play` | ACCEPT WITH CHANGES (blocking 3) |

Evidence run here: 71 tests in `tests/unit/t6/test_fsync_synth.py`, `t6/test_bus_writer.py`, `t4/test_statefile.py`,
`t4/test_loop.py`, `t9/test_launcher.py` pass (WSL). Synth 1-year run: 3 fsyncs gated vs 40 durable.

## Blocking

1. **Proposal 030 must be a C4 v1.1, not a "doc-only v1 clarification".** `contracts/README.md` §3 rule 7: doc-only
   wording that changes *no rule* stays v1. This changes what the field measures on Windows. Precedent 026 (cap now
   measures private memory) bumped C8 to v1.5. Fix: in 030 replace "doc-only v1 clarification" with "C4 → v1.1
   (rule change; no model, schema or wire change; `schema_version` stays 1)", add a `Migration:` line ("Windows
   values before parser X.Y.Z are working set; do not compare across the boundary"), and list `tools/m0` (T8) and
   `src/sae/bench` (T7) as consumers.
2. **Code ships ahead of acceptance.** Rule 4: teams build against FROZEN until the architect marks ACCEPTED.
   `cost.rs` now contradicts `contracts/C4_sae_parse.md:60`. Fix: before commit, the architect marks 030 ACCEPTED,
   edits C4 §2 line 60 and the version line, and adds a `contracts/CHANGES.md` entry, in the same commit as `cost.rs`
   (`uv run pytest tests/contract` green). Otherwise hold `cost.rs`/`output.rs` out of the commit.
3. **Windows NUL path untested; T9 fragment claims otherwise.** `changelog.d/T9.md` says the Windows `NUL` case is
   "covered only by the CI windows job", but the pty test is skipped on win32 and the Spy test only checks the kwarg.
   Wave A B3 required a Windows run. Fix (smallest): add a `@pytest.mark.skipif(sys.platform != "win32")` test that
   runs `_PLAY_UNDER_PTY` with `stdin=subprocess.DEVNULL, stdout=PIPE, stderr=STDOUT`, same fixture, and asserts exit
   0, `DAEMON STARTED`, `split candidate left pending`, index rows `["seen"]` (do not assert the prompt text is absent:
   on Windows `isatty(NUL)` may be True, prompt prints, `input()` hits EOF). Or, if not adding it, change the fragment
   to "Windows `NUL` path UNVERIFIED (no test)".

## Non-blocking

1. Mode sets: `AdapterMode` has exactly 4 members; `{LIVE, RELEASE}` (`writer.py:56`) and `LOCKSTEP_MODES = {REPLAY,
   SYNTH}` (`loop.py:51`) are exact complements today. Two definitions can drift when a 5th mode lands. Add one
   assert: `writer._DURABLE_MODES.isdisjoint(loop.LOCKSTEP_MODES) and writer._DURABLE_MODES | loop.LOCKSTEP_MODES ==
   set(AdapterMode)`.
2. `adapter is None` → durable in `loop.py:164`: safe default. Writer reads `config.adapter`, loop reads
   `adapter.mode`; both come from the same config in every wiring path seen.
3. Third fsync is `src/sae/daemon/runs.py:67` (`CURRENT.tmp` run lock, once per run). Correct and needed; note it in
   the test's printed line so nobody chases it again.
4. Normalised bus test strips `seq`, `t_*`, `run_id`, health lines and `save_folder_key`. It proves only that the
   gate does not change event content/order (fsync cannot change bytes). The fsync-per-inode counts are the real
   proof. Acceptable per B2(c); 1 year instead of 30 is fine for counts.
5. `cost.rs` Windows test: wrap the buffer in `std::hint::black_box(&buf)` so a release-mode build cannot elide the
   allocation. Bump the parser crate patch version so Windows bus history can be split at the meaning change.

Python consumers of `rss_peak_bytes` / `parse_rss_peak_bytes`: `daemon/loop.py:279` (health metric), `telemetry/
mapping.py:54`, `tools/console/views.sql:103`, `bench/cli.py:68`, `m0/evidence.py:271`. None applies a cap or health
threshold; no governor rule reads it. A ~3x Windows value trips nothing. `tools/m0/m0_results_template.md:24`
`rss_peak_mb` now means commit on Windows: relabel at M0-11 (T8).

Launcher test timeout 30 s: correct. It is the stall-detection bound, not a speed gate; a regression shows as
TimeoutExpired → "profile update failed" → assertion fails. Pass costs ~7 s.

LIVE/RELEASE: byte-for-byte unchanged (defaults `durable=True`, `fsync=True`). Close fsyncs in every mode (test
asserts `during + 2`). C5 §1 "at most once per game month and on exit" holds. Owned paths: all edits in T3/T4/T6/T9
paths plus a proposal file. No settled decision touched.

## Architecture ratings

| Area | Rating | Why |
|---|---|---|
| 1. Performance and memory | ON TARGET | Windows parse metric now matches the Job commit cap; synth fsyncs 40 → 3. |
| 2. Telemetry | AT RISK | Windows metric meaning changes with no version marker until 030 lands as v1.1; 5 % check vs Process Explorer is M0-11, UNVERIFIED. |
| 3. Adaptive AI engine | ON TARGET | No decision path changed; `state.json` bytes identical gated vs durable. |
| 4. Learning profile from saves | ON TARGET | `sae play` can no longer stall 600 s on a split prompt; candidate stays pending. |

Next action: architect edits proposal 030 to C4 v1.1 and lands C4 §2 + CHANGES.md with the `cost.rs` commit.
