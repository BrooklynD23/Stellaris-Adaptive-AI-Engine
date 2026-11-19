# Opt Waves 1-2 code review (sae-reviewer, 2026-11-16)

Scope: uncommitted tree on `feat/opt-wave-a1` vs HEAD `9dcee50`, work orders in
`docs/plans/opt/IMPLEMENTATION_WAVES.md` §2, §9, §10, plus late landings (features `vis` on `systems_owned`,
`sae play` re-exec skip, replay flush fix, W1-9 numbers). The tree changed while this review ran (other agents);
findings reference the tree as of 2026-11-16 ~01:10.

**Overall: ACCEPT WITH CHANGES.** One blocker found during review (record/replay divergence) is now fixed in the
tree by T2; one must-fix item remains (B3, `engine_ms` scope).

## Verdict per work order

| Order | Verdict | Note |
|---|---|---|
| W1-1 pre-emption (`core/strategy.py`) | ACCEPT | Rules 1-8 of C3 §6.1 match lines 97-135; tests (a)-(i), floor-fail (§9.1) and reanchor present. Exposed the replay ordering bug (B1) |
| W1-2 clamp + fog fix (`features.py`, `belief.py`) | ACCEPT | B2 fixed; `vis` keyed on `systems_owned is not None` with its test. Bottom clamp justified (item 5) |
| W1-3 parser fast path 0.1.2 | ACCEPT | Item 1 ruling below |
| W1-4 bench `--background` | ACCEPT | |
| W1-5 gate exit 5 | ACCEPT WITH CHANGES | N2 (source-text test) |
| W1-6 tuner memo | ACCEPT | Memo keys verified: `fast_lls` depends on (fold, kappa); `fold_score` takes no threshold |
| W1-7 `sae play` order + re-exec skip | ACCEPT | N5 |
| W1-8 `feel_commitment` | ACCEPT | N6 |
| W1-9 numbers | ACCEPT WITH CHANGES | N7 |
| W2-1 governor spm | ACCEPT | Matches C7 v1.2 §4.3 items 3-5 |
| W2-2 loop sampler + 4 keys | ACCEPT WITH CHANGES | B3 (`engine_ms` scope) |
| W2-3 sensors private bytes | ACCEPT | Linux `RssAnon` and Windows `memory_info().private` both present; last-good kept |
| W2-4 telemetry/views | ACCEPT | Old bus reads NULL, tested |
| W2-5 field kit + M0 rows | ACCEPT | |
| Contracts 031-035, C7 §4.4 wording | ACCEPT | Versions and status lines consistent; C8 now v1.8 for the re-exec consequence |

## Blocking issues

1. **B1 (blocker, now fixed in tree) record/replay divergence.**
   `tests/unit/t2/test_synth_record.py::test_five_year_turtle_record_replay_same_strategy_sequence` failed:
   `At index 2 diff: ('2200.03.01', 'baseline') != ('2200.03.01', 'outscale_siege')`.
   Reproduced; passes at HEAD; bisected to `core/strategy.py` alone (features/belief alone pass).
   Root cause: `ReplayAdapter._walk` applied a save at offset `consumed` before the assembler flushed the
   preceding poll's log observation, so in replay the 2200.03 save overtook its HB; live/synth flush first.
   The engine decides once per game month on the first observation, so replay decided a month earlier. Pre-W1
   the 24-month hold masked it; pre-emption reacts on one tick and exposed it.
   Fix landed (T2): `src/sae/adapters/replay.py:218-222` flushes `self._asm` before applying saves, new test
   `tests/unit/t2/test_replay.py::test_save_does_not_overtake_its_polls_log_observation`. Equivalent to the
   fix I verified in a scratch copy (swap `_on_line` before `_apply_saves`: all of `tests/unit/t2` green).
   Process note: W1-1 handed back without a full-suite run; §9.2 requires listing every broken test.
2. **B2 (major, now fixed in tree) ruff red.** `src/sae/core/features.py:135` E501 (134 > 120) seen mid-review;
   `ruff check` is clean on the final tree.
3. **B3 (major) `engine_ms` over-counts.** `src/sae/daemon/loop.py:375-380` times all of `_handle_obs`,
   which includes `check_disk()` (a recursive `runs/` walk, line 392) and `save()` (state.json fsync). C5 v1.5
   names engine work and its bus writes only; on a Windows HDD these can push p95 over the 50 ms stack-flip
   threshold (C7 §4.7) for reasons a msgspec flip cannot fix. Fix (line 392):
   `t = time.perf_counter(); self.check_disk(); self.engine_s -= time.perf_counter() - t`, and either exclude
   `save()` the same way or add "and the `state.json` save" to the C5 `engine_ms` row.

## Rulings on flagged items

1. Debug-only `SAE_PARSE_FORCE_FULL`: acceptable. `cfg!(debug_assertions)` is compile-time false in release
   (no `[profile.release] debug-assertions` override), so release ignores it. The forced path is a real function
   argument (`run_parse(.., force_full)`, `load(path, meta_only)`). The corrupt-gamestate test is a canary: if the
   hook ever stopped working the fast-vs-full comparisons would not silently pass.
2. `test_policy.py:425` posture EARLY_PUNISH -> HOLD_COUNTERPUNCH: legitimately re-targeted. The turtle sim now
   runs `outscale_siege` (offensive) from the first confident tick, so an offensive flip is a no-op; the test's
   claim ("another table changes output") is unchanged and defensive still proves it.
   `test_no_switch_inside_window` re-target is correct: early switch is allowed only from an abstention window,
   only to a non-baseline `table` counter with `commit_until = mi + 24`.
3. `months >= 1` in `test_monthly_health_and_spm_samples`: justified. That test drives real asyncio scheduling,
   so HBs per poll are not deterministic; the deterministic cases (k=2, k=0, wall division) are pinned by new
   unit tests in `tests/unit/t4/test_loop.py`. Keep.
4. Gate priority test reading source text: not good enough (N2).
5. Bottom clamp at min mu: required, not scope creep. Without it, rates in [0, 0.5) raise P(turtle) (narrow turtle
   sd), violating the W1-2 spec's own monotonicity test. Recorded in the T4 fragment.
6. Refusal before config: acceptable. C8 v1.7 says "before any stage work"; the probe needs no config and
   fails open (`_game_running` returns False on probe error). A broken config surfaces as exit 3 after the game
   closes.

## Non-blocking notes

1. **N1** `features.py` back-compat: a pre-W1 `state.json` has `vis=None`, so the first evidence tick after
   upgrade starts a new visibility run (rate `None` for one window). Harmless; mention in the T4 fragment.
2. **N2** `tests/unit/t7/test_gate_cli.py` `test_priority_is_own_process_only` greps source. Replace with
   behaviour: monkeypatch `sae.profile.cli.os.nice` / `psutil.Process` with spies and assert `Process()` was
   called with no pid and `nice` with `_NICE_IDLE`. File also has CRLF line endings; normalise.
3. **N3** `loop.py` `_count_hb`: a backward jump in the same poll keeps `pending_k` from before the jump and emits
   it with the reloaded date. Rare; reset `pending_k = 0` on `o.m < self.prev_m`.
4. **N4** `sensors.py` Windows calls `memory_info()` twice per poll (rss, private). Reuse one call.
5. **N5** `launcher.py` imports private `_is_sae_alive`, `_read_current` from `sae.daemon.runs`. Fails safe
   (a live unrelated `sae` pid in CURRENT only skips the update). Make them public when next touched.
6. **N6** `stage1.commitment_gaps` measures the first hold from the trace start; a trace that starts mid-window
   would under-count. Fine for replay traces that start at campaign month 0.
7. **N7** `tools/bench/demo_v1_measure.py:28-46` ignores the synth exit code (`new[0]` raises IndexError on a
   failed run). Check `r.returncode` and record failures. DEMO_V1_NUMBERS "lag 0" is measured from the first
   confident tick, so it is 0 by construction under D1; say so next to the study's 21.

## Checks

- Fog-of-war: no engine file reads god-view; features use `player_legal` only.
- Priority/affinity: gate and play lower only their own process; no game handle.
- Daemon imports: no new module imports in `loop.py`.
- CHANGELOG fragments T3-T9 match the code; user guide 8c/8e match `launcher.py` and `gate/cli.py`.
- Run here: `mypy --strict src` clean; `ruff check` clean (final tree); full pytest -x stopped at B1 before the
  fix; after the fix, the touched suites (t2 synth_record + replay, t4 features/strategy/belief/loop, t5, t7 gate,
  t9 launcher + fit_memo, contract versions) pass (1 skip). Full suite and `cargo test -j 1` not re-run here
  (full pytest exceeds 18 min on this shared host).
