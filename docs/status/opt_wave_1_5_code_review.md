# Wave 1.5 code review (line level)

2026-11-16, `sae-reviewer`. Uncommitted tree on `feat/opt-wave-a1` (base `bf2e56b`). Scope: plan rev 2 (WO-F out
of scope per coordinator), plan review items F1/F2/F3 and 4-7.

## Verdict: CHANGES REQUIRED (1 blocker, 1 major, 1 minor)

| WO | Verdict |
|---|---|
| WO-A contracts 038/039 | ACCEPT WITH CHANGES (finding 1) |
| WO-B parser 0.1.3 | ACCEPT (cargo not rerun here; WO-E runs it) |
| WO-C `core/features.py` + tests | ACCEPT WITH CHANGES (finding 2) |
| WO-D versions + t9 tests | REJECT until finding 0 is fixed |

## Fix before commit

0. **blocker, `tests/unit/t9/test_summary.py`: 2 of the file's tests fail.** Reproduced with
   `uv run pytest tests/unit/t9/test_summary.py`. Root cause: WO-D re-pinned only the version, but F1 and F2 (WO-C)
   change the summary values. In KNOWN, the frontiers are 2, 4, 0, 1, 0, 0, so only the 2201 month passes
   `MIN_FRONTIER`, and the first tick drops both categoricals. In `test_in_contact_false_is_ignored` the
   out-of-contact month resets the reporting set, so neither in-contact tick keeps fleet. Both changes are correct
   behaviour, so update the expectations (values measured):
   - line 43: `assert border[0] == pytest.approx(0.25) and border[1] == 1  # F1: only frontier 4 passes`
   - line 46: `assert f["fleet_power_cat_mean"] == (pytest.approx(0.6), 5)  # F2: first tick dropped`
   - line 47: `assert f["tech_cat_mean"] == (pytest.approx(0.6), 5)`
   - line 84: `assert s.features["fleet_power_cat_mean"].n_obs == 0  # F2: first tick + post-gap tick dropped`
   Also check `test_model.py` and `test_tune_cli.py` in the full run.

1. **major, `contracts/C10_profile_policy.md:145` and `:148`.** C10 v1.4 rewrote only the war rows. F1 and F2 also
   change what the profile averages, because `summary.py:_samples` takes `fv.border_fort_ratio`,
   `fv.fleet_power_cat` and `fv.tech_cat` from `extract`. Replace with:
   - 145: "mean pooled `fortified_border_starbases / frontier_systems` over months where one observer alone sees
     >= `MIN_FRONTIER` (3, HYP) frontier systems (`sae.core.features`)"
   - 148: "mean of the C4 category; fleet/tech months dropped by the reporting-set rule (first evidence tick,
     observer set changed) are excluded". Add a matching line to proposal 039.
2. **minor, `src/sae/core/features.py:101-103`.** A-55 names M0-5. M0-5 is the SNAP fleet-value check
   (`docs/m0_checklist.md:32`). The architect's M0-11 (save vs UI, `docs/m0_checklist.md:38`) is the right one,
   and CHANGES.md and 038 say the kit step is not wired yet. Replace "M0-5 checks ... war on the player." with
   "no kit step yet (T8 adds it to M0-11: war screen vs save `attackers` / `defenders`)." Also fix
   `docs/plans/opt/WAVE_1_5_PLAN.md:51` "(M0-5)" -> "(M0-11, kit step pending)".

## Rulings on the questions

1. **Property test is not vacuous.** A mutation run (scratch script, repo untouched) reverted each fix through
   `run_case`. Results: no F2, 96 of 116 cases caught; no F1 (`MIN_FRONTIER=1`), 20 caught; old `_at_war`, 16
   caught; every route caught at least once. Limit: the war cases feed `player_offensive_war_count=0` directly, so
   the parser rule is proved only by `parser/tests/golden.rs` and its fixtures.
2. **Parser rule matches C4 v1.3.** `features.rs:155-168` uses set membership, never list order.
   `war_ai_led_player_coattacker` puts the player first (`attackers [0, 5]`) and gives 0, which proves it. The
   player's own `sae_sid` is excluded. Nit: no fixture for the player plus an untagged co-attacker (should give 1).
   A country whose `sae_sid` is out of range is untagged (`extract.rs:323`), so its war counts as the player's;
   acceptable.
3. **F2 cannot starve permanently,** except when the set differs on every evidence tick (perturbation (h)). The set
   is stored on every evidence tick, including dropped ones, and the no-views path resets it to empty. Cost: one
   save per change. With N observers, one AI's contact flicker drops both categoricals for everyone. Upgrade path
   if (h) measures badly: take the median over the observers present in both ticks.
4. **A-55: M0-11 is right.** See finding 2.
5. **`at_war=None` for defensive wars matches C10 v1.4.** `summary.py:132-142` drops `None` from both the
   numerator and the denominator, and `n_obs=len(war)` matches the table. WO-D's claim holds.
6. **Builder mixing:** it holds by construction. `profile/cli.py:187-197` rebuilds every summary from the bus and
   never reads cached ones. Nit: a guard in `model.py:104`:
   `if any(s.builder_version != BUILDER_VERSION for s in summaries): raise ValueError("mixed builder_version")`.
7. **Both re-targeted tests are legitimate.** `test_out_of_contact_ignored` still asserts the value (on tick 2) and
   now also checks the drop on tick 1. `test_border_ratio_and_median` moves frontier 2 -> 3 to pass the gate;
   (1+2)/(3+3)=0.5 still tests pooling, and the median is checked on a stable second tick.
8. **Back-compat holds.** The field defaults to `None`. Old `state.json` is covered by
   `test_hardening.py:test_old_state_json_without_categorical_sets_loads` and T-4. C4 §7 states which side ships
   first.

Also: the changelog.d fragments for T3, T4 and T9 match the code. The UNVERIFIED and HYP labels are present.
There is no fog leak: the field comes from public war membership, the same class as `player_war_count`, and only
in-contact views carry it.

## Re-review rev 3.1 (2026-11-17, `sae-reviewer`)

Scope: changes since the review above, against `docs/plans/opt/WAVE_1_5_OPTION2.md` rev 3 + rev 3.1. Ran:
`tests/unit/t4/test_single_tick_property.py`, `test_features.py`, `test_hardening.py`, `tests/unit/t9/test_summary.py`,
`tests/unit/t2/test_synth.py` (all pass); two mutation runs (scratchpad copy, `PYTHONPATH`); one repro script.

### Verdict: APPROVE WITH CHANGES (0 blocker, 1 major, 1 minor, 2 nit)

| Change | Verdict |
|---|---|
| `features.py` `_stable_cat`, `fleet_last` / `tech_last` | ACCEPT for the engine path; finding R1 for the profile path |
| `synth.py` borders (WO-F) | ACCEPT (3 and 4 frontier systems, fort fractions unchanged) |
| t4 property / features / hardening tests | ACCEPT |
| t9 `test_summary.py` re-pins | ACCEPT (each justified, below) |
| C10 rows 145/148, proposal 039 item 7 | ACCEPT text; row 148 is not yet true in code (R1) |
| A-55 -> M0-11 | ACCEPT with R2 |
| Hook, markers, `CLAUDE.md`, agents, `docs/process/*` | ACCEPT with N1 |

Prior findings 0, 1, 2: closed.

### Rule code vs spec

- Rule 1: no-views branch keeps `None` as `None`, non-`None` becomes `frozenset()` (`features.py:177-186`). Matches.
- Rule 2 (B1): `first_ev = memory.last_evidence_mi is None` (`:208`), and `prev is None` counts only then
  (`:133-134`). Pre-1.5 state (`last_evidence_mi` set, sets `None`) drops once. Matches.
- Rule 4 (A1): median over `prev ∩ current`, counted only if `== last` (`:137-139`). Equality is int vs int:
  `statistics.median_low` returns an element of the clamped int list, `fleet_last: int | None`, and pydantic
  round-trips it as int. No float issue. Empty intersection and empty `prev` both give `None`. Matches.
- Rule 5/6: empty `prev` drops one tick; sets stored on every evidence tick; `*_last` updated only when counted
  (`:234-237`). Matches. `reset_session` keeps all four fields.
- Mutation check (mine): `med == last` removed -> `test_t3_tick_after_change...` fails; `first_evidence` forced
  True -> `test_t4_old_memory_without_sets_drops_once` fails. Implementer's 9-41 per rule is consistent.
- Spec test list: differing-value gain, partial swap, loss shift, flicker, resumed session, pre-1.5 all present
  (`test_single_tick_property.py:84-99`). The flicker case asserts the shifted value is dropped on both legs.
  No vacuous case: `run_case` asserts `ev` and `k >= 12`, and the counted cases compare to a reference posterior.

### Re-pins (CLAUDE.md Testing)

- `test_summary.py:43` border `(0.25, 1)`: F1 gate, measured in the first review. Justified.
- `:46-47` fleet/tech means back to the pre-wave `(0.5, 6)` and `(4/6, 6)`: B1 counts the first month and the
  fixture has one observer, so no set change. Justified.
- `:86` `n_obs` 2 -> 1: the out-of-contact month is a real gap (rule 5), so 2202 drops once. Justified.
- `test_features.py:34-37`, `:56-64`: second tick is now redundant under B1 but harmless.

### Findings

R1. **major, SEMANTIC, `src/sae/profile/summary.py:99-107` (with `features.py:208-237`).** The profile bypasses
the reporting-set rule on log ticks. Log observations carry the last save's `player_legal` with the old
`legal_as_of` (`adapters/assembler.py:279-290`), so `new_ev` is False; the stored set was updated to the current
set on the save tick, so `who == prev` and `_stable_cat` returns the full median. `_samples` keeps one sample
per month from every tick, so the months after a dropped save count the value the save dropped. Repro:
save `{2:-2}`, save `{2:-2, 3:2, 4:2}` (counts -2 by the guard), log tick next month -> `fleet_power_cat == 2`,
`new_ev False`. Same for the post-gap drop. The engine is unaffected (`belief.update` ignores non-evidence
ticks), so CI-1 and e2e numbers stand, but C10 row 148 ("months it drops are excluded") is false for real
campaigns with monthly log lines. The t9 fixtures give every log tick a fresh `legal_as_of`, so no test sees it.
Fix (T9, `summary.py:_samples`): carry the evidence tick's verdict to the log ticks that reuse its save:
```python
fleet = tech = None  # before the loop
...
memory, fv, new, _k = extract(memory, k.obs)
if new or not views:
    fleet, tech = fv.fleet_power_cat, fv.tech_cat
```
and pass `fleet`, `tech` to `_Sample` instead of `fv.*`. Add one `test_summary.py` case: save with a set change
that drops, then a stale log tick in the next month; assert `n_obs` excludes both months. Mutation: revert ->
the case fails.

R2. **minor, TEXT, `src/sae/core/features.py:105-106`.** "M0-11 checks the parser field against a save ..." reads
as if the check exists; `docs/m0_checklist.md:127-129` and `CHANGES.md:23` say no kit step is wired. Replace with
"M0-11 will check it (kit step pending, T8): war screen vs save `attackers` / `defenders`."

N1. **nit, TEXT, `.githooks/pre-commit:2`.** Header says "the same checks CI runs"; the hook now runs the fast
tier only (`tests/unit tests/contract`, not `slow`; e2e, integration and `tests/test_cli.py` are skipped). CI
(`.github/workflows/ci.yml:32`) still runs the full suite, so the gate is intact. Reword to "a fast subset of the
checks CI runs". Rules in `CLAUDE.md` tiers, `pyproject.toml` marker text and the hook agree otherwise;
`RESERVATIONS.md` next-free A-56 / 040 matches A-55 and 039 in use.

N2. **nit, SEMANTIC, `tests/unit/t9/test_summary.py:146-154`.** `test_builder_version_is_0_2_0` pins a constant
and fails on no behaviour. Keep only if the maintainer wants a version tripwire; otherwise delete.

Not checked here: cargo (unchanged since the first review), full suite (coordinator tier 3).

## Closure of rev 3.1 findings (2026-11-18, coordinator)

- **R1 (SEMANTIC) fixed:** `src/sae/profile/summary.py:_samples` carries the last evidence tick's `fleet_power_cat` and
  `tech_cat` to the log ticks that reuse its save (`if new or not views`). New test
  `test_summary.py::test_stale_log_tick_inherits_the_dropped_save` (fixture `make_run(..., as_of=)` sets
  `legal_as_of` per month). Mutation: `if new or not views` -> `if True` makes it fail; restored, `tests/unit/t9` passes.
  Awaiting the reviewer's closure check on `summary.py:96-115` and that test only.
- **R2 (TEXT) closed by coordinator:** `features.py` docstring now says the M0-11 kit step is pending (T8).
- **N1 (TEXT) closed by coordinator:** `.githooks/pre-commit` header says "a fast subset of the checks CI runs".
- **N2 (SEMANTIC nit) open for the maintainer:** `test_builder_version_is_0_2_0` kept for now; delete if no version
  tripwire is wanted.

## Closure check (2026-11-18, sae-reviewer)

Verdict: **CLOSED** (R1, R2, N1). No new findings. N2 stays with the maintainer.

- **R1:** `summary.py:_samples` (lines 96-113) matches the specified fix. Checked: no in-contact views -> `extract`
  returns an empty vector, so fleet/tech are None (gap month excluded, as before); views present implies a save
  (`assembler.py:280,290`), so `not new` with views is always a reused save; the first tick is new evidence;
  reload truncation happens in `_kept_observations` before `_samples`, so a non-new tick after a reload reuses the
  same save; `border` and `at_war` from `fv` are unaffected (no reporting-set rule, same save -> same value).
- **Test:** `test_stale_log_tick_inherits_the_dropped_save` is non-vacuous and matches C10 row 148. Own mutation in a
  scratchpad copy (`_Sample(..., fv.fleet_power_cat, fv.tech_cat, ...)`, i.e. the pre-fix code): FAILED,
  `At index 0 diff: 1.0 != 0.0`. Fixed code: passes.
- **Tier 1:** `uv run pytest -x -q tests/unit/t9 tests/unit/t4` passed (2 skipped); `uv run ruff check` clean;
  `uv run mypy --strict src/sae/profile/summary.py` clean.
- **R2, N1:** wording reads correctly (`features.py:105`, `.githooks/pre-commit:2`).
