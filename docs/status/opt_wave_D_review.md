# Wave D + stack decision: adversarial plan review

Reviewer: `sae-adversary`, 2026-11-14. Plan under review: `docs/plans/opt/SYNTHESIS.md` §2, §4, §5, §6 with
`parser.md`, `profile-offline.md`, `stack.md`. Pre-implementation gate. No code changed.

**Verdict: ACCEPT WITH CHANGES.** Gating the streaming filter on M0-11 is right, but M0-11 as written cannot trip
the gate, and today's memory metric under-reports production parses on Windows. 7b has no M0-11 dependency.

## 1. Facts checked in code

| Claim | Code | Finding |
|---|---|---|
| Production parse memory metric is wrong on Windows | `parser/src/cost.rs:56` reads `PeakWorkingSetSize`; `background.rs:22` sets `PROCESS_MODE_BACKGROUND_BEGIN` | Confirmed. The live `parse_rss_peak_bytes` health metric (`src/sae/daemon/loop.py:276`) inherits the 32 MiB cap. The Job cap (`c7_governor.py:86`) limits commit, not working set |
| M0-11 measures production mode | `src/sae/m0/evidence.py:244` runs `parse --save` with no `--background` | False. M0-11 records non-production CPU and wall time. Its RSS is truthful only because the flag is missing |
| `MAX_SPAN` is per entry | `extract.rs:254-263`; callers at 526, 547, 563, 584, 607 tape one country, system, starbase or war entry each | Confirmed. 16 MiB per entry is safe unless one real country entry is larger (UNVERIFIED) |
| Gate can reuse the existing guard | `profile/cli.py:116-134`, `tune/cli.py:210-212` | Two copies already exist. Detection fails open (probe error means "not running"). Priority touches `psutil.Process()` (self) only, so it respects the settled rule |
| Flip metrics exist | `sensors.py:173-183` | `daemon_rss_bytes` is psutil `rss` = Windows **working set** (shared pages included), not private working set. `daemon_cpu_pct` exists. **No engine-time metric exists anywhere** (`src/sae/daemon`, `src/sae/core`) |
| Gamestate ceiling | `input.rs:16-17`: zip 256 MiB, gamestate 400 MiB | At the cap a full parse commits about 460 MB. That is under the 1 GiB Job cap but large on a starved 8 GB game PC |

## 2. Per item

### D1 streaming filter: ACCEPT WITH CHANGES
1. **Blocking: make M0-11 able to trip the trigger.** It parses a fresh monthly autosave, which will be early-game
   and small. Add to the M0-11 procedure (`src/sae/m0/checks.py:196`): a read-only parse of a **copy of the largest
   existing save** on the game PC, run once with `--background`. Record text bytes, commit (PeakPagefileUsage) and
   the largest single entry span.
2. **Blocking: change the trigger** from "commit > 250 MB" to "commit > 250 MB **or** commit > 25 % of
   `avail_ram_bytes` at parse time". Both values are already health metrics, so live telemetry keeps checking it
   after M0.
3. **Blocking: derive the filter whitelist from `Need`** (`extract.rs:29-31`), the same keys the extractor reads.
   A second hand-kept list will drift once A-19 changes key paths after M0-11. Schedule D1 after those A-19 fixes.
4. **Corrected validation test.** "Output identical on real save" is too weak.
   - Run a differential test on the synthetic save, `pathological_2x`, the M0-11 saves and a mutation corpus
     (unbalanced braces, `\"` and `{` inside quoted strings, a truncated tail).
   - Require identical C4 stdout, **exit code and failure kind**.
   - Run the filter with injected window sizes 1, 2, 7 and 4096 bytes and require byte-identical filtered text, so
     lexer state that crosses a window boundary is proven.
   - List every allowed divergence (for example `MAX_ENTRIES`, `split.rs:12`) in C4 §6 notes.

### D2 zlib-rs: ACCEPT WITH CHANGES
It saves 25 ms per parse. Ship it only bundled with D1, never alone. Run the D1 mutation corpus over truncated and
corrupt zips through zlib-rs, because it replaces the all-safe-Rust miniz_oxide on hostile input.

### D3 `MAX_SPAN` 16 MiB: ACCEPT WITH CHANGES
Set `MAX_SPAN = max(16 MiB, 4 × largest entry span seen at M0-11)`. Fix the "10x" comment at `extract.rs:23-24` and
in `parser/README.md`. Validation: the existing `oversize_span_is_format` test plus a 16 MiB dense span that exits 4
under the Job cap on Windows CI.

### 7b gate refusal + idle priority: ACCEPT WITH CHANGES, move to Wave A
1. It has no M0-11 dependency. Call the existing `profile/cli.py` `_game_running`/`_lower_priority` (or move them
   once to `sae.platform`); do not write a third copy.
2. Give the refusal its own exit code, so CI and scripts cannot read it as "release gate FAILED". Document it in
   the C8 §5 proposal.
3. Validation: inject sensors with `game_running=True` and assert the exit code, that stage1 is never called, and
   that priority was set only on the own process.
4. Process-name truth (A-43) lives in M0-12, which is **stretch**. Move the process-name record into M0-11 (core),
   because the governor's PLAYING state also depends on it.

## 3. Ordering errors (A→D)
1. **Blocking:** Wave B row 2 must land **before the X2 field kit is built for the M0 weekend**. Otherwise the game
   PC measures the old binary and every D trigger is judged on the wrong numbers.
2. **Blocking:** `cost.rs` must report commit (`PeakPagefileUsage`) whichever background mode is approved. The Job
   cap and D1's trigger are both about commit. Today this is listed only as a fallback.
3. Wave A item 10 (M0-12 working-set row) must define its metric first (see §4).
4. Add parser.md's "several parser processes for bulk re-parse" to SYNTHESIS §7. It touches the three-process rule
   and is missing there.

## 4. Stack decision: AGREE, with two corrections
1. "3.13/3.14 measured worse" is overstated. Three runs on a machine at load 5 to 6.5 give overlapping CPU ranges
   (1.19-1.27 against 1.17-1.29 s). Write "no gain; +1.1 to +3.3 MiB". The free-threaded build (+10 MiB, slower) is
   the only clear loss. The conclusion stands.
2. **Blocking (measurability):**
   - "Private working set > 100 MiB" is not what any tool records. Restate it as psutil `memory_info().private`
     (commit) > 100 MiB, recorded once at M0-12, with `daemon_rss_bytes` p95 alongside.
   - "Engine work > 50 ms per game month" has no metric. Measure it with REPLAY of the M0 bus on the game PC (CPU
     seconds / months), or add an `engine_ms` health key (C5 §3 additive).
3. The msgspec estimate (~13 MiB) matches the layer table (6.5 + 6.3 - 1.2). It omits rewriting the custom
   validators (`common.py:22` date normalisation) and JSON Schema export. Keep 2 to 3 weeks as a floor.

## 5. Do-not-do list
Agree with every entry. Add "removing EcoQoS" (parser.md §4) and "live telemetry for players" (profile-offline §4).

## 6. Architecture targets
| Area | Rating | Why |
|---|---|---|
| Performance and memory | AT RISK | Late-game parse memory is unmeasured, and the Windows metric under-reports until Wave B |
| Telemetry | AT RISK | `parse_rss_peak_bytes` is wrong on Windows today; no engine-time metric for the flip threshold |
| Adaptive engine | ON TARGET | D touches nothing adaptive; wave C carries it |
| Learn profile from saves | AT RISK | Old late-game saves are the input. D1 must use one whitelist, and the gate must test a large save |

Next action: add the "largest existing save, `--background`, record commit and max span" step to M0-11 in
`src/sae/m0/checks.py:196`.
