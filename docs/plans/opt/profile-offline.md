# Profile and offline tools review (opt/profile-offline)

Reviewer: profile-offline specialist, 2026-11-14. Read-only: no code, contract or other doc changed, no git command
run. Builds on `docs/plans/ARCH_OPTIMIZATION_REVIEW.md` ("baseline"). Needs a `sae-adversary` review before any wave.

Measured on WSL2 (6 cores, ext4 scratch, Python 3.12.3) on a machine shared with other agents (load 5 to 6.5), so
times are user-CPU seconds, best of 3 or more. Fixture: 20 campaigns of 200 game years cloned from 4 `sae synth` runs
(11 MB each, 218 MB in all). Bus content, Windows costs and save-folder facts (A-30, A-46) are UNVERIFIED. The
"running game" was another specialist's dummy process named `stellaris` (A-43 UNVERIFIED). Prototypes ran on a
scratch copy of `src/sae`.

## 1. Verdict

1. **No speed problem.** Every offline tool is inside its target: `sae profile update` 9.3 s CPU and 88 MB (target
   30 s, 200 MiB), `sae tune detector` 54 s (target 30 min), `sae gate stage1` 11 s. No parallelism is needed.
2. **Two etiquette gaps, both cheap:** `sae play` can stall the daemon start for up to 600 s, and `sae gate` is the
   only offline tool that runs while the game runs.
3. **Adaptiveness is limited by data, not compute:** nothing is learned until campaigns are closed by hand and 5 are
   labelled. A cache and a memo remove the waits that remain (9.3 s to 0.25 s; 54 s to 12 s).

## 2. Ranked changes

Now:

| # | Change | Path | Gain (method) | Effort (h) | Risk | Contract | Team |
|---|---|---|---|---|---|---|---|
| 1 | Pass `stdin=subprocess.DEVNULL` to the `sae play` update subprocess | `src/sae/profile/launcher.py:44` | Removes a stall of up to 600 s before the daemon starts. Measured: on a terminal the update waits at the split prompt (killed by an 8 s watchdog); on a pipe it ends in 0.5 s, candidate left pending | 0.5 | Low: split questions are then asked only by a manual `sae profile update` | None | T9 |
| 2 | `sae gate`: refuse while the game is detected (`--force` to override) and drop to idle priority, as `sae tune` does | `src/sae/bench/gate/cli.py:50-64` | Measured with the dummy game alive: update exits 1, tune exits 2, gate runs 11.5 s CPU at 222 MB, normal priority. `--repo` adds the whole test suite | 1 | Low | Proposal: C8 §5 gate row (additive) | T7 |
| 3 | Cache for `sae profile update`: per-run segments and per-campaign summaries, keyed by each bus's size and mtime, the folded campaign facts, `BUILDER_VERSION`, `sae.__version__` and the summary file's sha256 | `src/sae/profile/cli.py:187-228`, `index.py:168-185` | Measured (prototype): unchanged history 9.3 s to 0.25 s CPU, 88 to 41 MB RSS; one new run 0.47 s. `profile.json` and all summaries byte-identical | 6 | Medium: a stale entry gives a wrong profile; needs invalidation tests | Proposal: C10 §0 file list (additive) | T9 + architect |
| 4 | Memoise the tuner grid: log-likelihood rows per (fold, kappa), fold score per (fold, kappa, rho, clip) | `src/sae/tune/fit.py:212-233` | Measured (prototype): 54.0 s to 12.4 s CPU (-77 %), same policy sha256; RSS 219 to 235 MB | 1.5 | Low | None | T9 |
| 5 | `sae profile update` lists active campaigns with the exact `sae profile close <id>` command | `src/sae/profile/cli.py:224-227` | Measured: 20 indexed campaigns, none closed, gives "0 summary(ies)". Only a `closed` row lets a campaign into the profile or the tuner | 1 | Low | None (C10 §1 rule 4 forbids auto-close, not a hint) | T9 |

Later:

| # | Change | Trigger | Measured or expected | Effort (h) | Contract | Team |
|---|---|---|---|---|---|---|
| 6 | `functools.lru_cache` on `normalize_game_date` (`src/sae/contracts/common.py:22`) | Any wave touching contracts code | Measured: bus validation 14.8 to 10.1-11.9 µs per event; cold update 9.13 to 7.28 s (-20 %). Errors are not cached (checked) | 0.5 | None | architect |
| 7 | Reader skips lines that cannot be a wanted event type | Cold update above 30 s | Measured with row 6: cold update 9.13 to 6.18 s (-32 %) | 1.5 | C5 §5 wording ("validate each line") | T6 + T9 |
| 8 | Load tune and gate campaigns one at a time | More than 10 labelled campaigns | 36 MB per 200-year campaign held as pydantic objects; 219 MB at 5 | 5 | None (T9/T7 loader seam) | T9 + T7 |
| 9 | Score `belief_threshold` in the tuner | V-1 data shows posteriors near the threshold | Today the value never moves. On synth, 0.40, 0.45 and 0.55 give identical decisions (10,526 of 11,990 months right) | 6 | None | T9 |

## 3. Evidence

### 3.1 Measurements

| Test | Result |
|---|---|
| `sae profile update`, 20 closed campaigns | 9.1 to 9.9 s CPU, 8.8 to 11.7 s wall, RSS 88 MB (0.47 s per campaign) |
| Same, none closed (index only) | 3.9 s CPU, RSS 77 MB |
| cProfile of the update | validation 9.9 of 15.4 s (64 %); `normalize_game_date` 4.4 s over 1.27 M calls; feature extraction 2.6 s |
| Cache prototype, unchanged history | 0.25 s CPU, 0.8 s wall, RSS 41 MB |
| `sae tune detector`, 5 campaigns | 54.0 and 57.6 s CPU, RSS 219 MB. Grid time: `fast_lls` 48 %, `fold_score` 27 %, `fast_trace` 23 % |
| Tune with memo | 12.4 and 12.9 s CPU, policy `12e18764…` unchanged |
| `sae gate stage1 --include-synth` | 11.3 s CPU, RSS 222 MB |
| Telemetry following a run | RSS 118 MiB (63 private); 35 ms CPU per 30 s idle |
| Telemetry `.rrd` export after the run | 2.1 s CPU, RSS 122 MB |
| Existing tests, all prototypes on | `tests/unit/t9`, `tests/e2e/test_fog_f3.py`, `tests/unit/t6/test_bus_reader.py`: 171 pass, 5 skip |

### 3.2 Code

1. Double read: `scan_runs` validates every line of every bus (`index.py:175`); `_rebuild` then reads each closed
   campaign's buses again (`cli.py:190-192`, `summary.py:50-58`).
2. Blocking prompt: `_ask_split` calls `input()` when stdin is a TTY (`cli.py:137-145`). The launcher inherits stdin
   and waits up to `UPDATE_TIMEOUT_S = 600` (`launcher.py:14,44`).
3. Guard: only `profile/cli.py:205-208` and `tune/cli.py:210-212` check for the game and lower priority.
4. Grid waste: `_score` recomputes `fast_lls` for all 900 (point, fold) pairs (`fit.py:195-197,224-230`); 25 differ.
   The threshold is not scored (`fit.py:66-72`) yet gates the counter (`core/strategy.py:99`).
5. Closing: only a `closed` row closes a campaign (`index.py:99-101`); profile and tuner take closed campaigns only
   (`cli.py:190`, `ledger.py:153`). Summaries span runs (`summary.py:61-110`).

### 3.3 Where this differs from the baseline

1. Rec 5 ("cache per-run summaries", no contract impact): summaries span runs (point 5 above), and C10 §0 lists
   every profile file, so the cache file needs an additive proposal.
2. "Pydantic is not a cost" holds for the daemon only. Offline, validation is 64 % of the update.
3. Offline parallelism: agreed, and stronger. Tune is 33 times under its target before row 4.
4. Rec 3 needs no re-parse of saves: `economy_cat` is in every recorded observation (`summary.py:103`). The tuner has
   no economy table yet (`tune/fit.py:130-147`).

### 3.4 Sources

| Claim | Source |
|---|---|
| "Don't do validation when you don't have to" (row 7) | https://pydantic.dev/docs/validation/latest/concepts/performance/ |
| An NTFS directory entry can show a stale size until the last handle closes. `os.stat` freshness while the daemon holds the bus open is UNVERIFIED | https://devblogs.microsoft.com/oldnewthing/20111226-00/?p=8813 |

Scratch scripts and fixtures (not in the repo):
`/tmp/claude-1000/-mnt-c-Users-DangT-Documents-GitHub-Stellaris-Adaptive-AI-Engine/d4efc1c5-69e5-4ddc-879f-1e378d064932/scratchpad/opt/profile-offline/`

## 4. Do not do

| Idea | Why it loses here |
|---|---|
| Process pool or threads for update, tune or gate | Cold update 9.3 s once, tune 12 s after row 4, gate 11 s |
| Parallel re-parse of recorded saves for the learning loop | Tune and gate read bus observations, never saves |
| Compare file times instead of keeping a cache file | Misses a deleted bus and a pending split candidate |
| numpy, msgspec or a slim model in the offline readers | A new dependency or weaker validation for work row 3 removes |
| Live telemetry next to the game for players | 118 MiB RSS; the export after the session costs 2.1 s CPU. Keep `telemetry.enabled = false` (`c8_config.py:80`) |

## 5. Needs maintainer decision

| Topic | Settled decision it touches | Benefit | Cost | Recommendation |
|---|---|---|---|---|
| Run the `sae play` profile update after the daemon exits, not before | D5; C8 §5 "then starts `sae daemon`" | No start delay; the session just played is indexed at once; no refusal when the game was started first (`cli.py:205`) | 1 h plus C8 wording | Decide. If yes, row 3 moves to Later |
| Tune on fewer than 5 labelled campaigns | V-1, D3 (`tune/cli.py:40`) | First personal policy after 3 campaigns | Leave-one-out on 3 folds is weak evidence | Keep 5 until V-1 has run |
| Build the profile from saves the player already has | C10 §2 strict inputs; C4 reports only countries with `sae_sid` (`contracts/C4_sae_parse.md:65`, `parser/src/extract.rs:323`) | A profile on day 1 | 30+ h, C4 and C10 changes, fog review; saves made without the mod hold no `sae_sid` (UNVERIFIED, A-30) | Not before M0-11 |

Next action: send rows 1 to 5 to `sae-adversary` for the plan review, starting with row 1.
