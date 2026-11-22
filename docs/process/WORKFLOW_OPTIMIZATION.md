# Agent workflow optimization (2026-11-16)

Goal: fewer tokens and less test time per wave, with tests that prove production behaviour. Scope: the agent
process only. No source, contract or hook behaviour was changed; hook and CI changes are proposals (§5).

Evidence base: the latest multi-day session transcript (63 subagent runs, 52 coordinator dispatches), the review
artefacts in `docs/status/opt_*.md`, and timed test runs on this WSL2 host (6 cores, load average 11-17 from other
work during every measurement, so timings are upper bounds).

## 1. Where the tokens went

Subagent usage for the session, de-duplicated by message id:

| Measure | Value |
|---|---|
| Subagent runs / model turns | 63 / 1,264 (median 15 turns per run) |
| Cache-read input tokens | 138.2 M |
| Cache-write input tokens | 8.56 M |
| Output tokens | 1.48 M |
| Context at turn 1 of every subagent | 45.3k-48.7k tokens (median 46.7k) before it reads one project file |

Weighted with the usual price ratios (cache read 0.1, cache write 1.25, output 5, relative to plain input), the
session costs about 31.9 M input-equivalent tokens: 43 % cache reads, 33 % cache writes, 23 % output.

Findings, largest first:

1. **The fixed 46.7k-token prefix is about 30 % of subagent spend.** It is re-read on every turn
   (46.7k x 1,264 turns = 59.0 M, 43 % of all cache reads) and written once per run (63 x 46.7k = 2.9 M, 34 % of
   cache writes). Most of it is not SAE: the harness tool descriptions, about 100 global skill descriptions (UI,
   animation, slides), MCP server notes and nine global rule files. The project `CLAUDE.md` is under 1.5k of it.
2. **Long runs cost quadratically.** 4 of 63 runs went past 60 turns and took 31 % of all cache reads. The five
   one-off "optimization specialist" runs took 56.0 M cache-read tokens (41 %) at 180k-250k tokens of context per
   turn. 73 tool results were over 20,000 characters (2.0 M characters in total); each one is re-read on every later
   turn of its run.
3. **One review covered two waves.** "Opus code review: Waves 1-2" ran 68 turns, 8.7 M cache-read tokens (6.3 % of
   the session), 27 git commands and the full suite at least twice. The Wave 1.5 review, one wave, took 26 turns and
   2.5 M.
4. **Hand-backs ran over their caps.** Median final report: implementer 365 words (cap 250), architect 546 (cap
   400), adversary 600 plus a 700-2,100-word review file with the same content, scout 863 (cap 200). Every word is
   re-read by the coordinator on each later turn.
5. **Plans are large and read whole.** `docs/plans/opt/W3_DESIGN.md` is 8,101 words, `IMPLEMENTATION_WAVES.md`
   3,645; the 23 plan and review files of this effort total 40,285 words.
6. **Dispatch boilerplate is real but small.** 52 prompts, 121,892 characters in total (median 2,231). "No git"
   appears in 45, "launch Stellaris" in 38, `changelog.d` in 32, the output-style line in 52. Every one of those
   rules is already in the subagent's context through `CLAUDE.md`. Removing them saves under 1 % of tokens; the
   gain is consistency, not cost.
7. **Closure rounds.** 2 of 12 adversary runs were closure checks. The W3 one found two items only partly closed
   (`docs/status/opt_w3_design_review.md:133-134`), so closure checks on semantic items earn their cost. Text-only
   closure does not need a Fable run.

## 2. Where the test time went

| Measurement | Result |
|---|---|
| Tests collected | 1,621 (unit 1,058 functions in 89 files, contract 83, e2e 41, integration 21) |
| Collection only, repo on `/mnt/c` (drvfs) | 84.9 s |
| Collection only, same tree copied to ext4 (`/tmp`), venv on ext4 | 6.3 s (13x faster) |
| `tests/unit` minus one test, ext4 copy | 115 s for 1,282 tests (1,265 passed, 3 failed, 14 skipped) |
| `tests/unit` on `/mnt/c` | 262 tests in 637 s, then interrupted inside the test below |
| `tests/unit/t4/test_boundary.py::test_rss_growth_120_year_run_under_150_mib` | not finished after about 330 s (ext4) and about 470 s (`/mnt/c`); one subprocess, 120 game years |
| Next slowest unit test (ext4) | `tests/unit/t6/test_bus_writer.py::test_crash_truncation_every_offset`, 27 s; all others under 3 s |
| Full suite on `/mnt/c`, from the transcript | 964 s to 1,648 s (16-27 min) |
| Same-size suites under contention, from the transcript | "303 passed in 160 s" against "354 passed in 1,812 s" |
| Full-suite runs in the session | 14 by the coordinator, at least 6 more by subagents (2 or more by one reviewer) |

Findings:

1. **The working tree is on the Windows drive.** Collection alone is 13x slower there. This is the largest single
   time cost and it is paid by every pytest call of every agent, targeted or not.
2. **One test hides the speed of the rest.** Without the 120-year RSS test the whole unit tree runs in under
   2 minutes on ext4. e2e and integration were not timed (run budget); they spawn `sae` and `sae-parse` processes.
3. **Concurrent runs multiply each other.** Several agents each ran large suites; timeouts (exit 144) and 10x
   slowdowns followed. At 16-27 min each, 20 full runs are 5 to 9 hours of pytest wall time in one session.
4. **The hook comment is stale**: `.githooks/pre-commit:29` says "full suite, about 5 min".
5. **The tree is red now**: `tests/unit/t9/test_summary.py::test_known_fixture_exact_values` (`:43`) and
   `::test_in_contact_false_is_ignored` (`:84`) fail in the working tree (re-run 2026-11-16: 2 failed, 8 passed).
   This is the open blocker of `docs/status/opt_wave_1_5_code_review.md:17`. Both pin exact values, so they need a
   stated RE-PINNED decision or a code fix, not a quiet edit. A third failure seen only in the ext4 copy
   (`tests/unit/t4/test_cli.py::test_resume_with_changed_policy_sha_exits_1`) passes in the real tree.

## 3. Test quality

| Finding | Where | Action |
|---|---|---|
| Test reads source text instead of exercising behaviour | `tests/unit/t7/test_gate_cli.py:82` (`inspect.getsource(pcli._lower_priority)`) | Replace with a test that spies `psutil.Process` and asserts it is called with no pid (T7; not done here) |
| Exact-value pins that break on every behaviour change | `tests/unit/t9/test_summary.py:35-84` | Keep one known-fixture test; report every change as RE-PINNED |
| Source scans that are legitimate | `tests/unit/t4/test_boundary.py:81-84`, `tests/unit/t6/test_import_guard.py:46` | Keep: they are the fog and import boundary lints (C6 §4, CI-4). `CLAUDE.md` names them as the exception |
| Doc-drift checks | `tests/unit/t8/test_x4_deferred_doc.py:17`, `test_checklist_drift.py` | Keep: cheap, and they guard the M0 hand-off |
| No test is marked for speed; `game` is the only marker | `tests/unit/t7/conftest.py:10`, `tests/unit/t9/conftest.py:34` | `slow` marker added (§4, change 3) |

What already works and stays: stop rules fixed before the run (CI-1 pooled rate >= 0.90), property tests with a
mutation check per fix (`tests/unit/t4/test_single_tick_property.py`), disjoint owned-file sets, and
before/after numbers with "not demonstrated".

## 4. Ranked changes

Savings are estimates from the session numbers above; the method is given per row. "Applied" means edited in this
change set, for the coordinator's review.

| # | Change | Expected saving (how estimated) | Risk | State |
|---|---|---|---|---|
| 1 | Work from a clone on the WSL ext4 disk (or at least put the venv there with `UV_PROJECT_ENVIRONMENT`) | Collection 84.9 s to 6.3 s measured; unit tier under 2 min measured on a copy. Full suite expected well under half of 16-27 min; not measured | Windows tools reach the tree through `\\wsl$`; the M0 field kit and the game PC flow must be re-checked; the venv-only variant is unmeasured | Proposal P1 |
| 2 | Test tiers: targeted, then affected area with `-m "not slow"`, full suite once per wave by the coordinator; one pytest process at a time | About 12 of 20 full runs avoided per session: 3 to 5.5 hours of pytest wall time at 16-27 min each | A cross-area regression shows at wave end, not at hand-back. The area map sends core, daemon and adapter changes through the replay and synth chains, which is where the record/replay ordering bug lived | Applied: `CLAUDE.md`, agent definitions, `DISPATCH_TEMPLATE.md` §4 |
| 3 | `slow` marker on the 120-year RSS test; default run and CI unchanged | Removes a test that did not finish in 330 s from every area run | A memory regression is seen at wave end. The area map adds the test back for changes to daemon memory behaviour | Applied: `pyproject.toml`, `tests/unit/t4/test_boundary.py:87` |
| 4 | Pre-commit runs the fast tier; full suite in CI and at tier 3 | About 14-25 min per commit on this host | A commit can be locally green and red in CI | Proposal P2 |
| 5 | Cut the fixed subagent prefix: a `tools:` allowlist per SAE agent and project-level disabling of unrelated plugins and MCP servers | If 15k-20k of the 46.7k go (estimate, not measured): 10-13 % of subagent spend. Check: first-turn `usage` of the next dispatch | An allowlist may drop tools the agents use (`Monitor` 19 calls, `TaskStop` 5, `WebFetch` for scout and adversary). Trial on one agent first | Proposal P3 |
| 6 | Bound each run: range reads, output through `tail`, plans of 2,500 words at most, work orders that cite sections, one review per wave with a change-set list | Cache reads grow with turns x context. Two per-wave reviews the size of the Wave 1.5 one (2.5 M each) in place of the W1-2 review (8.7 M) save about 3 % of the session; keeping tool results under 20k characters removes up to 0.5 M tokens of fresh context that is then re-read every turn | A reviewer given a file list can miss a file nobody listed; the OUT-OF-SET hand-back line covers that | Applied: `CLAUDE.md`, agent definitions, template §2 |
| 7 | Tiered closure: Fable closure check only for SEMANTIC blocking items, reading only changed sections; the coordinator closes TEXT items | About 1 adversary run per session (roughly 1 M cache-read tokens, 1 %), plus a shorter loop | A semantic item mislabelled TEXT skips re-review. Rule: when in doubt it is SEMANTIC; the reviewer sets the tag, not the author | Applied: `CLAUDE.md` "Review gates", `sae-adversary.md`, `sae-reviewer.md` |
| 8 | Hand-back shapes with caps (implementer 250 words, reviewers 200 plus a 900-word file, scout 200) and fixed headings: FILES, OUT-OF-SET, BEYOND-SPEC, RE-PINNED | Roughly halves 63 reports of 365-863 words in the coordinator's context; a few percent of coordinator tokens. Main gain: silent scope growth and re-pins become visible | A cap can hide evidence; failing-test output and security findings are exempt | Applied: agent definitions |
| 9 | One ledger for contract versions, A-ids and proposal numbers | Removes the collision rework seen between Waves 1.5 and 3 (C10 v1.4 against 036/037, A-50..A-55) | The ledger goes stale if the architect skips it; reviewers check it | Applied: `docs/process/RESERVATIONS.md` (seeded; architect to confirm) |
| 10 | Short work orders from a template; standing rules live in `CLAUDE.md` and the agent definitions | Under 1 % of tokens; fewer contradictory or missing rules | None found | Applied: `DISPATCH_TEMPLATE.md` |
| 11 | "Test what matters" rules: behaviour named, public entry point, no source-text tests, no weakened assertions, RE-PINNED reporting, mutation checks for safety-critical fixes | Not a token saving. Fewer tests to write, run and re-pin | A rule cannot judge relevance; the reviewer checklist item 5 does | Applied: `CLAUDE.md` "Testing", `sae-implementer.md`, `sae-reviewer.md` |
| 12 | `pytest-xdist` for the full tier | 2-3x on 6 cores (typical, not measured here) | New dev dependency; subprocess, RSS and footprint tests (CI-4, `tests/integration/_lock.py`) may not be parallel-safe | Proposal P4 |

## 5. Decisions for the maintainer

| Id | Decision | Recommendation | Trade-off |
|---|---|---|---|
| P1 | Move the working clone (or only the venv) to ext4 | Yes; time one full run before and after | See change 1 |
| P2 | `.githooks/pre-commit`: replace the full suite with secret scan, ruff, mypy and `uv run pytest -x -q -m "not slow" tests/unit tests/contract`; keep the mod and Rust steps; full suite stays in CI (`ci.yml` already runs it on every push to `feat/**` and on pull requests) and in tier 3 before approval is asked. Also fix the "about 5 min" comment | Yes, after P1 is measured | Faster commits against the chance of a locally green, CI-red commit in e2e or integration |
| P3 | `tools:` allowlists in `.claude/agents/*.md` and project settings that switch off unrelated plugins and MCP servers for this repo | Trial on `sae-implementer` for one work order and compare first-turn tokens | See change 5 |
| P4 | Add `pytest-xdist` | Only if the full tier is still over 5 min after P1 | See change 12 |
| P5 | Read-only git for reviewers (`git diff`, `git status`, `git log`, `git show`; nothing that writes the index, tree or refs) | Yes. 15 of 63 subagents ran 114 read-only git commands (83 `diff`, 21 `status`) against the rule, because a reviewer has no other way to see a diff. `CLAUDE.md` now states the strict rule; without P5 reviewers read whole files from a change-set list, which costs more tokens | Loosens the wording of a settled rule, so it is the maintainer's call |
| P6 | Fix the stale counts elsewhere: `docs/developer_guide.md:22` ("nine contracts"; C10 exists), `docs/TEAM_PLAN.md` quality bar (still "pytest -x -q" untiered) | Yes, in the next docs pass (T8, architect) | None |

## 6. How to check that this worked

After the next wave, compare with this session: full-suite runs (target 2 or fewer per wave), subagent runs over
60 turns (was 4), median hand-back words per agent type, first-turn context tokens, closure rounds on TEXT items
(target 0), collisions in `RESERVATIONS.md` (target 0), and RE-PINNED lines reported against re-pins the reviewer
finds unreported (target 0).
