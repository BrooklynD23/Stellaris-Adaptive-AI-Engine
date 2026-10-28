# X3 — Game-deferred test harness and game-PC agent runbook (T7 lead)

Status: REVISED 2026-10-20 after `sae-adversary` plan review (APPROVE_WITH_CHANGES, `docs/status/X3_plan_review.md`);
all 5 blocking items closed below (tagged [R1]..[R5]).
Branch `feat/x3-game-test-harness`. Rule (maintainer / tech lead, 2026-10-20): everything that needs the real game,
real saves, real logs or labelled campaigns is BUILT now and DEFERRED: skipped with a reason naming the missing
input, documented in `docs/handoff/deferred/`, run tomorrow by one agent on the Stellaris PC in one session.

Goal: tomorrow's agent opens `docs/handoff/GAME_PC_AGENT.md`, sets `SAE_GAME_*`, runs
`uv run python3 tools/game/run_deferred.py --out <dir>` and fills `GAME_PC_RESULTS_TEMPLATE.md`. No guesswork.

## 1. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | No contract change, no proposal 028. The `game` marker is registered in a new root `tests/conftest.py` (`pytest_configure` → `config.addinivalue_line`), not in `pyproject.toml` (architect-owned, C8 §7). | smallest change inside T7's scope; pyproject untouched |
| D2 | Marker form: `@pytest.mark.game("SAE_GAME_LOG", ..., id="X3-G6")`. Positional args = required env vars. Unset → skip, reason `game input missing: SAE_GAME_LOG (docs/handoff/deferred/X3.md#x3-g6)`. Set but path missing → **fail** at setup (a typo must be loud). `id=` is copied to `item.user_properties` (`deferred_id`) so it reaches JUnit XML. | `uv run pytest -m game` selects all; the default suite stays green and game-free |
| D3 | `SAE_PARSER_EXE` is not a marker input. Fixture `game_parser_exe` (tests/game/conftest.py): `SAE_PARSER_EXE`, else T2's `resolve_paths(PathsConfig()).parser_exe`, else skip with reason `SAE_PARSER_EXE`. | the field PC has the exe on `PATH` (X2 D5); a dev clone has `parser/target/release` |
| D4 | **Live `sae daemon` does not record** (`make_adapter` never passes `record_dir`; only `sae synth --record` does). So `tools/game/make_recording.py` builds a replay session dir offline from a real `game.log` + an autosave folder, reusing T2's `RecordGate` (PLYR redaction, size cap) and placing each save at the byte offset after the last C1 line whose date ≤ the save's `meta.date` (from `sae-parse parse --sections meta`). `SAE_GAME_RECORDING` may point at a ready dir; else the `game_recording` fixture builds one under pytest's basetemp, which the collector pins to `<out>/private/tmp` (D5) [R3]. Real logs span reloads (dates go backwards): use the last HELLO segment and print a warning when dates are non-monotonic. | no T2/T4 code change; also turns old campaigns into recordings for V-1/V-3. `# ponytail:` date interleave approximates the live poll order; upgrade = wire `record_dir` in `make_adapter` (backlog message to T2/T4) |
| D5 | Collector `tools/game/run_deferred.py` is stdlib-only, runs `sys.executable -m pytest -m game --junitxml <out>/private/junit.xml --basetemp <out>/private/tmp -rs -p no:cacheprovider` (`--basetemp` keeps every raw save/log copy made by `tmp_path`/`tmp_path_factory` inside `private/`, never in `%TEMP%` [R3]), then writes `<out>/summary.md` (per test: deferred id, node id, outcome, message ≤ 200 chars) and `<out>/inputs.json` (names of the `SAE_GAME_*` vars set, `sys.platform`, Python version, `sae-parse --version`; **never values or paths**). Exit code = pytest's. `--out` is required (no default inside the repo, so nothing is committed by accident). `--list` prints the deferred index only. | tomorrow's evidence in one folder; privacy by construction |
| D6 | Deferred index by glob: the collector and the runbook read `docs/handoff/deferred/*.md` and list every heading matching `^#{2,4} +([A-Z][A-Z0-9]*-[A-Za-z0-9.-]+)` (X2 and X4 write their files in parallel; their ids appear without edits here). | task: index by glob, never hard-code |
| D7 | Redaction in the collector: every occurrence of a `SAE_GAME_*` / `SAE_PARSER_EXE` value **that contains a path separator (`/` or `\\`) or is ≥ 8 chars** and of `Path.home()` in failure text is replaced by `<VAR>` / `<HOME>` before it reaches `summary.md` (short flags like `SAE_GAME_EXPECT_ACK=1`, `SAE_GAME_MAX_SAVES=24` are not redacted, else every `1`/`24` in counts and ids is destroyed [R2]); JUnit XML stays in `<out>/private/` (may hold raw text), `summary.md` is the shareable file. Suites never put raw log lines or save content in assert messages: counts, line numbers, field names only. | privacy rule: nothing raw leaves the PC unless the maintainer says so |
| D8 | Reuse, don't duplicate: suites import `e2e._bus` (`load`, `check_chain`, `digest`), `e2e._fog` (`run_replay`, `decision_stream`), `e2e._cli` (`run_sae`), `sae.contracts.c1_log.parse_log_line`, `sae.adapters.logtail.LogTailer`, `sae.contracts.c4_parse.PARSE_OUTPUT_ADAPTER`. `tests/` is already on `sys.path` (rootdir conftest, prepend mode). | one checker, one fog harness |
| D10 | Saves are parsed once per session: session fixture `parsed_saves -> list[tuple[Path, int, str]]` (path, exit code, stdout) in `tests/game/conftest.py` runs `sae-parse parse --save X` once per `game_saves` entry (timeout 60 s each); G1/G2/G3/G5 consume it and never spawn full parses themselves. Only G4 (error paths) and G6 (Ironman) call `sae-parse` directly. [R4] | keeps the ≤ 25 min budget honest: one full parse per save |
| D9 | Game-free proof: `tests/e2e/test_game_dryrun.py` records a synth campaign, rebuilds it with `make_recording.py` from its own `game.log` + `saves/`, points every `SAE_GAME_*` at those files and runs the collector in a subprocess. Every `game` test must pass except the Ironman-positive test (skipped, reason `SAE_GAME_IRONMAN_SAVE`). The collector subprocess runs with `TMPDIR`/`TEMP`/`TMP` pointed at an empty `sys_tmp` dir; the dry run asserts no `*.sav` exists under it afterwards (proves pytest's default basetemp is unused [R3]). | proves tomorrow's commands execute end to end; runs in CI |

## 2. Owned paths (this wave)

T7 paths: `tests/conftest.py` (new, root; X3 owns the global `game` marker), `tests/game/**`, `tests/unit/t7/**`,
`tests/e2e/test_game_dryrun.py`, `tools/game/**`. Docs assigned to X3 by the orchestrator (T8 / architect files,
X3 rows only): `docs/plans/X3_plan.md`, `docs/handoff/GAME_PC_AGENT.md`, `docs/handoff/GAME_PC_RESULTS_TEMPLATE.md`,
`docs/handoff/deferred/X3.md`, `docs/TEAM_PLAN.md` (validation backlog pointer + X3 row only), `docs/BUILD_REPORT.md`
(one X3 pointer section), `docs/status/T7.md`, `docs/components/T7.md`, `changelog.d/T7.md`.
Not touched: `.github/workflows/**`, `tools/m0/**`, `src/sae/m0/**` (X2), `pyproject.toml`, `src/**` of other teams.

## 3. Inputs (shared convention `SAE_GAME_*`)

| Var | What | How to produce on the game PC |
|---|---|---|
| `SAE_GAME_SAVES_DIR` | one campaign folder under `save games\` with ≥ 13 monthly autosaves (12+ months) | autosave monthly (A-24), mod enabled, play 12+ months (the M0-5 session) |
| `SAE_GAME_LOG` | `game.log` of that same session (`logs\game.log`, copied after quitting) | the M0-3/5/6 session |
| `SAE_GAME_RECORDING` | optional: a replay session dir; else built by the collector into `<out>/private/recording` | `make_recording.py --log --saves --out` |
| `SAE_GAME_IRONMAN_SAVE` | one Ironman save file (`ironman.sav`, A-34) | M0-14 session, 2 months |
| `SAE_PARSER_EXE` | `sae-parse.exe` | CI artifact `sae-m0-field-kit-windows` `bin\` or `cargo build --release -j 1` |
| `SAE_GAME_MAX_SAVES` | optional cap on saves parsed (default 24, newest first) | time bound |

`SAE_GAME_LABELS` (V-1) belongs to X4; indexed through `deferred/X4.md`, not consumed here.

## 4. Deferred entries (`docs/handoff/deferred/X3.md`, owner WO X3-5)

| ID | Proves | Test (tests/game/…) | Inputs | Pass rule | Maps to | Est. |
|---|---|---|---|---|---|---|
| X3-G0 | harness works on the PC | collector `--list` + `pytest -m game --collect-only` | none | ≥ 1 game test per X3-G1..G11 collected, index lists X2/X3/X4 ids | — | 2 min |
| X3-G1 | parser on real saves | `test_saves.py::test_parse_real_saves` | SAVES_DIR | every save: exit 0, `ParseResult` validates, `schema_version == 1`, `meta.date`/`game_version`/player country present, `meta.ironman is not True` (C4 `ironman: bool | None`; count of `None` printed as A-20 evidence) [R1] | M0-11, A-19, A-20 | 5 min |
| X3-G2 | cost budget | `test_saves.py::test_parse_cost_budget` | SAVES_DIR | `cost.wall_s` ≤ 3.0 and `rss_peak_bytes` ≤ `GovernorConfig().worker_mem_cap_bytes` for every save; max/median recorded | M0-11, R-5 proxy | incl. G1 |
| X3-G3 | fixtures represent real saves | `test_saves.py::test_roundtrip_vs_fixtures` | SAVES_DIR | key set of a real `ParseResult` (minus `cost`, `save`) ⊇ key set of `parser/fixtures/balanced/expected.json`; legal sections hold no key outside C4 §3 (F2 on real data) | C4 §3, F2 | 1 min |
| X3-G4 | clean failures on Windows | `test_saves.py::test_exit_codes` | SAVES_DIR | missing file → 3; a truncated **temp copy** of a real save → 5 or 6; stdout validates as `ParseError` | C4 §2 | 1 min |
| X3-G5 | autosave cadence and folder | `test_saves.py::test_autosave_cadence` | SAVES_DIR | autosaves (`autosave_*.sav`, A-34) parse to consecutive month dates (gap 1, none > 1); all in one folder (A-46); `sae bench spy` s/game-year recorded as evidence | A-24, A-34, A-46 | 2 min |
| X3-G6 | Ironman detection | `test_ironman.py::test_ironman_flagged` | IRONMAN_SAVE | exit 0 and `meta.ironman is True` (full parse and `--sections meta`) | M0-14, A-20 | 1 min |
| X3-G7 | real log grammar | `test_log.py::test_log_grammar` | LOG | zero `LogLineError` over every `SAE|` line; ≥ 1 HELLO, ≥ 1 HB per game month spanned, ≥ 1 SNAP with `sid ≥ 1`; PLYR and ACK counts reported (ACK required only if `SAE_GAME_EXPECT_ACK=1`, the M0-6 session) | M0-3, M0-5, M0-6, A-04 | 1 min |
| X3-G8 | log adapter on real bytes | `test_log.py::test_tailer_matches_parser` | LOG | `LogTailer` over a temp copy (CRLF, encoding as-is) yields the same records as line-by-line `parse_log_line` on lines decoded with `errors="replace"` (as LogTailer does); no `parse_failed` health; cp1252 fallback count reported as X3-U2 evidence | C1, C6 | 1 min |
| X3-G9 | decision chain on real play | `test_recording.py::test_replay_chain` | RECORDING (or LOG + SAVES_DIR) | `sae replay --speed max --ack-model from_recording` exits 0; `check_chain(min_decisions=3, require_applied=<ACK lines present>)` empty | V-3 (signal), CI chain | 5 min |
| X3-G10 | determinism on real data | `test_recording.py::test_replay_deterministic` | as G9 | two replays → equal `digest` | — | 3 min |
| X3-G11 | fog on real recordings | `test_recording.py::test_fog_v5` | as G9 | `decision_stream` of a `PerturbingParser` replay == unperturbed, 2 seeds (F1 / V-5); non-vacuity: god bus non-empty | V-5, F1 | 5 min |

F3 (profile) and F4 (code boundary) need no game: F4 is green in CI; F3 on real data is V-1/X4 work. The index
says so; nothing is claimed for them here.

## 5. Work orders

Batch 1 (parallel, disjoint):

- **X3-1 marker + game fixtures** — `tests/conftest.py`, `tests/game/__init__.py`, `tests/game/conftest.py`,
  `tests/unit/t7/test_game_marker.py`. D2 + D3 exactly; fixtures `game_path(var) -> Path`, `game_parser_exe`,
  `game_saves(saves_dir, max)` (sorted by mtime, newest N), `parsed_saves` (session, D10) [R4], `game_recording`
  (SAE_GAME_RECORDING, else build via `tools/game/make_recording.py` into `tmp_path_factory`, i.e. under the pinned
  basetemp; skip reason names the missing var). Unit test with `pytester`, which must load the real root conftest via
  `pytester.makeconftest((REPO / "tests/conftest.py").read_text())` and run the inner pytest with `--strict-markers`
  [R5]: unset → skipped with exact reason; set → runs; set-but-missing → error; `-m game` selects; `deferred_id`
  in junit. Tests: `uv run pytest -q tests/unit/t7/test_game_marker.py`, default suite collection unchanged.
- **X3-2 tools** — scripts loaded by path, no package: `tools/game/run_deferred.py`,
  `tools/game/make_recording.py`, `tools/game/README.md`, `tests/unit/t7/test_run_deferred.py`,
  `tests/unit/t7/test_make_recording.py`. D4–D7. CLI: `run_deferred.py --out DIR [--list] [-- <pytest args>]`;
  `make_recording.py --log FILE --saves DIR --out DIR [--parser-exe EXE]` (out empty or new). Game-free tests:
  junit parse → summary (pass/fail/skip, id grouping), redaction of path-like/long env values and home, with
  `SAE_GAME_EXPECT_ACK=1` set "X3-G1" and the counts survive [R2], `--basetemp <out>/private/tmp` in the argv [R3], `--list` glob index with
  fake `deferred/*.md` (ids from X2/X4-style headings), exit-code passthrough (pytest stubbed via a runner arg);
  make_recording on a synth recording (`e2e._synth.record`) reproduces its save order and PLYR redaction.
  Load modules via `importlib` as `tests/integration/test_parser_fixtures.py` does. ruff + `mypy --strict tools/game`.

Batch 2 (needs X3-1 fixtures and X3-2 make_recording; parallel, disjoint):

- **X3-3 save suites** — `tests/game/test_saves.py`, `tests/game/test_ironman.py`. Entries X3-G1..G6 (§4).
  G1/G2/G3/G5 consume `parsed_saves` (D10), no `subprocess` full parse of their own [R4].
  Assert messages: counts, basenames, field names only (D7). Truncated copy goes to `tmp_path`, original untouched.
  Smoke on Linux: `SAE_GAME_SAVES_DIR=<synth recording>/saves`, `SAE_GAME_IRONMAN_SAVE` unset.
- **X3-4 log + recording suites + dry run** — `tests/game/test_log.py`, `tests/game/test_recording.py`,
  `tests/e2e/test_game_dryrun.py`. Entries X3-G7..G11; D9. Reuse `e2e._bus`, `e2e._fog`, `e2e._cli` (D8).
  Dry run passes once X3-3 lands (lead runs it in the gates); it asserts no `*.sav` under the redirected system temp [R3].

Batch 3 (docs; after batch 2 so commands and ids are real):

- **X3-5 runbook** — `docs/handoff/GAME_PC_AGENT.md`, `docs/handoff/GAME_PC_RESULTS_TEMPLATE.md`,
  `docs/handoff/deferred/X3.md` (sole owner; entries X3-G0..G11 with id, proves, PowerShell commands, inputs, evidence,
  pass/fail, record-where, time). Runbook: prerequisites (Windows, uv, git clone **required for pytest** — the X2 kit has
  no tests; `sae-parse.exe` from artifact `sae-m0-field-kit-windows` or cargo), PowerShell env setup, ordered session
  with time budget (critical first: M0-2, M0-3, M0-5, M0-6, M0-11, then X3-G*, then M0-14 / stretch / R-gates), how to
  produce each input (§3), the index (deferred/*.md by glob + `run_deferred.py --list`; `docs/m0_checklist.md`
  M0-1..M0-14 and A-01..A-46 by reference to its tables; R-1..R-6 from `bench/README.md`; V-1..V-5 from
  `docs/TEAM_PLAN.md`), quit Stellaris before G1 (sae-parse exits 6 on a sharing violation), privacy rules (no raw `game.log`, saves,
  recordings or `private/` — incl. `private/tmp`, pytest's basetemp with save/log copies — leave the PC unless the
  maintainer says so; share `summary.md`, `inputs.json`, the filled template), every game assumption UNVERIFIED.
- **X3-6 backlog + hand-back docs** — `docs/TEAM_PLAN.md` (validation backlog: pointer to the runbook, V-3/V-5 rows
  gain "X3-G9/G11"; X3 row), `docs/BUILD_REPORT.md` (one "7c X3" section), `docs/status/T7.md`,
  `docs/components/T7.md`, `changelog.d/T7.md`. Results numbers come from the lead's gate run.

## 6. Gates (lead, after batch 3)

`uv run pytest -x -q` (full, once), `uv run pytest -m game -q` (all skip, reasons name inputs), dry run green,
`uv run mypy --strict src/sae tools/game`, `uv run ruff check`, coverage ≥ 90 % per package (no `src/` change, so
unchanged). Reviewer on each batch; adversary hardening review (4 Fable criteria) before hand-back.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Field kit has no repo/tests; PC needs a clone + `uv sync` (network, ~10 min) | runbook step 1; fallback: copy the clone from a USB stick |
| make_recording order differs from live poll order (save written before/after the month's SNAP, UNVERIFIED X3-U1) | G9 checks chain integrity, not timing; backlog: daemon `--record` (T2/T4) |
| Real saves need a token table / binary (exit 4) | G1 fails with A-19 note; M0-11 fallback path in checklist |
| Raw player text in failure output | D7 redaction + `private/` split; reviewer checks every assert message |
| X2/X4 deferred file format drift | tolerant heading regex (D6); orchestrator relays the convention: one `### <ID> — title` heading per entry |
| Root `tests/conftest.py` duplicated by X2/X4 locally | X3 owns the global one; merge keeps D2 semantics |
| Session runs out of time on the PC | critical-first order; each entry carries a time estimate; one shared parse per save (D10) keeps the X3 run ≤ 25 min for 24 saves |

## 8. UNVERIFIED items (to T8 for the M0 checklist)

A-04, A-19, A-20, A-24, A-34, A-46 (already listed). New: **X3-U1** autosave file appears after that month's SNAP
lines in `game.log` (make_recording ordering); **X3-U2** `game.log` is UTF-8 (or cp1252) with CRLF line ends.

## 9. Backlog

`record_dir` wiring for `sae daemon` (T2 `make_adapter` + T4; C8 flag → proposal when wanted); F3 on real labelled
campaigns (X4/V-1); offline-kit test bundle so the PC needs no clone.
