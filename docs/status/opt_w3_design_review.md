# Wave 3 design (campaign memory) adversarial pre-implementation review

Reviewer: sae-adversary, 2026-11-15. Input: `docs/plans/opt/W3_DESIGN.md`. Checked against the working tree:
`core/engine.py`, `engine_tick.py`, `strategy.py`, `features.py`, `belief.py`, `outcome.py`, `state.py`,
`daemon/loop.py`, `statefile.py`, `runs_dir.py`, `adapters/assembler.py`, `live.py`, `tune/cli.py`,
`telemetry/mapping.py`, `tools/console/views.sql`, `tests/integration/test_ci4_imports.py`. No code changed.

**Verdict: ACCEPT WITH CHANGES.** W3-A0, W3-0, W3-4, W3-5 may start once B1-B8 are written into the design.
W3-1b and W3-1 do not start before that.

Earlier blocking items: C-review B1-B4 and validations 1-4 are carried; B9, B10, B13 are carried. B11 is half
carried (fixture yes, demo statements missing, see B8).

## Blocking changes

**B1 (W3-1, `core/carry.py`). A key change without a session reset must not be a campaign switch.**
`SaveWatcher` watches all of `save games`; `assembler.on_parse` rejects only saves more than 1 month ahead and then
overwrites `_folder_key` (`assembler.py:231-252`, `live.py:255`). A save touched in another folder (Steam Cloud
sync, a copied file) flips the key. With §4 row 8 that wipes belief and strategy mid-game, may merge another
campaign's prior, and flips back at the next autosave onto an older prior. Fix: row 8 fires only when
`obs.session_epoch` differs from the epoch at which `ident` was set (store `ident_epoch` in `CarryState`). Same
epoch and different key: keep ident and slice, emit one `invalid_event` ("foreign save key"). Tests: C-15 gets an
epoch bump; new C-15b same epoch, state unchanged, no `carry` event.

**B2 (W3-1, `daemon/carrystore.py`). Filter priors by run mode before the 64 cut.** There is no run retention
(`daemon/disk.py:8`), and bench, gate, synth and replay runs share `runs/`. Two failures: (a) 64 non-live runs push
the real prior out of the window; (b) after W3-4b a replay of the user's own recording writes the real key and
becomes the newest prior of the live game, with an older belief and window. Fix: keep only run ids whose mode
segment equals the current run's mode, then take 64. Tests: S-5 (a newer `-replay-` run with the same key is
ignored by a `-live-` run), S-3 counts same-mode dirs only.

**B3 (W3-1b, `core/regress.py`). A window opened after the load point must not survive the reload.** For
d <= 24 the design keeps the commitment via `reanchor`. A counter opened (for example by D1 pre-emption) at
2211.01 in the abandoned branch keeps running after a reload to 2210.01, where this timeline never showed that
evidence. Fix: `if strategy.commit_from_mi is not None and strategy.commit_from_mi > mi: reopen=True` (window
closes void, `select` re-decides from the decayed posterior). Same rule in the carry path. Tests: R-2b in-session,
C-10b carried.

**B4 (W3-1b). `window_void` must be sticky.** "True if a window is open" is ambiguous: after a first regression
`window_open` is already `None`, so a second regression could clear the flag and the outcome becomes a plain
reward 0. Exact rule: `window_void = s.window_void or s.strategy.commit_from_mi is not None`; cleared only where
`strat.opened_by == did`. Test R-6b: two regressions inside one window, outcome still void.

**B5 (W3-6, `tools/console/views.sql`). The audit view still reads void as 0.** `views.sql:85`
`arg_max(CAST(payload->>'reward' AS DOUBLE), seq)` is a third reward reader the design missed. Decision ids repeat
across runs (`d-YYYY.MM-NN`), and a carried window's outcome uses run A's id, so it can also join to an unrelated
run-B decision; that only happens after a regression, so the row is void. Fix: `outcome_reward` is NULL when
`payload->'metrics'->>'void' = '1.0'`, plus column `outcome_void`. Add to D-12.

**B6 (W3-A0). Close the reader hole left by dropping W3-3, and fix the detail grammar.**
- Dropping W3-3 is right: `bench/gate/metrics.py` reads no outcome. But W4-1 and W4-3 will add readers. Add one
  contract test in D-10: every file under `src/sae` and `tools/console` that reads an outcome `reward` (outside
  `core/outcome.py` and `contracts/`) also references `outcome_is_void` or `void`. Add "readers call
  `outcome_is_void`" to the W4-1 and W4-3 rows.
- `format_carry(carried_from, parts, refused)` has no `prior` argument, yet example 3 prints `prior=`. Fix:
  signature gains `prior: RunId | None`; key order `carried_from|parts|carry_refused|prior`; `CarryDecision.prior`.

**B7 (W3-0). The golden does not prove what the design says it proves.**
- The tree holds uncommitted Wave 1/2 work. Generate the golden only on the commit where Waves 1-2 are merged;
  store that commit hash and the per-event-type counts next to the sha so a failure can be located.
- Synth has no reload, pause or force, so W3-1b (unflagged) is not covered and R-4 "equals today's `_reset`" has
  nothing to compare against after the edit. Add a second golden captured before any edit: a hand-built
  observation sequence through `engine.on_observation` with a same-month HELLO (d = 0), a pause/resume, a
  force/clear and a normal window close; store drafts and final state. R-4 compares against it.
- Add a flag-on case with phase resumed under flag off: on load with `cfg.carry_state` false, set `carry=None`
  (W3-1, one line). Otherwise a resumed hold emits a `carry` event with the flag off, against §7.

**B8 (design text, architect). Write the demo v3 statements (B11).** The design has one line of demo wording.
Required list: flag off by default; identity rests on UNVERIFIED A-46; `player_sid` is almost always the same
value in single player, so identity is in practice folder key plus date; reload fairness is a decay bound, not
exact (posterior 0.95 after 24 abandoned months is still 0.68); carry is state persistence, not learning; all
evidence is replay of one synth recording until the game-PC run.

## Non-blocking

1. Policy `None == None` gives a full carry across a code change. Store the built-in sha
   (`core/policy.py`) instead of `None` in `CarryIdent`.
2. Refuse the carry when `len(P.belief.posterior) != len(ARCHS)`; a changed archetype enum crashes
   `belief.update` (`zip(strict=True)`).
3. `state_version` stays 1: old to new is safe. New to old fails closed with the misleading text "corrupt
   (invalid fields)", and W3-1b writes `window_void` for every user, flag or not. Acceptable; put the rollback
   note in the changelog and `docs/user_guide.md`.
4. A normal "continue" loads an autosave older than the quit date, so D is often 1-12: the carried window is
   then void and the belief decays. Expected; have the game-PC run record the void share.
5. The hold also blocks governor knob-only batches (same as pause). State it; measure in game-PC step 4.
6. `sae ctl status` shows `baseline, suspended=false` during a hold. One text line in W3-6 would do.
7. Optional narrowing of the known hole: refuse when D >= 1 and the loaded date is within 12 months of the
   campaign's first date. A legal reload there loses almost nothing.
8. Follow-up proposal for identity should compare two sources: the mod nonce in HELLO (T1) and a save-side galaxy
   id read by the parser (T3, no mod release, works under Ironman). Both UNVERIFIED.
9. Exact alternative to the decay bound: keep the last few `(mi, posterior)` evidence points (about 3 per 24
   months) and restore the one at the load month. Needs the maintainer, since B3 of the C review was approved.
10. Game-PC step 6 recordings contain full saves; keep them out of git or trimmed.
11. CI-4: `carrystore` and `c10_profile` import only contracts and `statefile`. D-9's "appear when the flag is
    on" is vacuous (`loop.py` imports them always); drop that clause.

## The 3 defaults

| # | Default | Verdict |
|---|---|---|
| 1 | Accept the same-name hole behind the default-off flag; nonce as a follow-up | **Safe, adopt.** The hole needs an old campaign that ended within about 3 game years of its start (D <= 24 against a new game at 2200), so the carried state is at most 3 evidence ticks, then decayed. See notes 7-8 |
| 2 | Refuse regression of 25-60 months at carry | **Safe, adopt.** |
| 3 | Keep W3-6 in Wave 3 | **Adopt, and it grows by B5.** Without it the console and Rerun show void as reward 0 |

## Architecture check (after the blocking changes)

| Area | Rating | Why |
|---|---|---|
| 1. Performance and memory | ON TARGET | +220 bytes of state, no resident growth; scan is on `%LOCALAPPDATA%`, normally 1-2 reads; worst case 64 reads once per run, bounded by B2. A-52 stays UNVERIFIED |
| 2. Telemetry | AT RISK | `views.sql` reads void as 0 (B5); detail grammar drifts (B6); the hold is invisible until it ends |
| 3. Adaptive AI engine | AT RISK | Identity rests on A-46 and a near-constant `player_sid`; foreign-save key flip (B1); abandoned-branch window survives (B3); default off, so shipped behaviour only gains W3-1b |
| 4. Learning the profile from saves | AT RISK | Void marking protects labels (good) but only if every reader skips (B5, B6); carry itself learns nothing; dropped windows leave no row |

Bottom line: the design is sound in shape and carries the earlier blocking items, but two paths still let state
from another campaign or an abandoned timeline act on the live game (B1, B3) and one reader still scores void as
0 (B5). Fix B1-B8 in the design text, then start W3-A0 and W3-0.

## Closure check (rev 2)

Reviewer: sae-adversary, 2026-11-16. Input: `docs/plans/opt/W3_DESIGN.md` rev 2 (§15 map),
`docs/plans/opt/IMPLEMENTATION_WAVES.md` §10. Re-read against the tree: `adapters/assembler.py:144-262`,
`core/strategy.py:57-66`, `core/engine_tick.py:89-101`, `contracts/common.py:61`. Not a fresh review.

**Verdict: GO for Wave 3 code, with two text edits (E1, E2) made before W3-A0 and W3-0 start.** No new gate round.

| Item | Status | Evidence / exact fix |
|---|---|---|
| B1 | CLOSED (one accepted residual, ruling 2) | §2.2 `ident_epoch`, §4 rows 8-9, rule 6, C-15/C-15b/C-15c. Checked: `on_parse` never bumps the epoch (only HELLO and log date regression call `_bump`), so an older-dated foreign save stays in the same epoch and the gate holds |
| B2 | CLOSED | §4.2 step 1 filters on the mode segment before the 64 cut; `RunId` pattern has exactly `live\|replay\|synth\|release`; S-3, S-5 |
| B3 | CLOSED | §3 rule A evaluated before `reanchor` (which does overwrite `commit_from_mi`, `strategy.py:62`); §4.1 merge step 3 reuses it; R-2b, C-10b |
| B4 | CLOSED | §3 rule B, single clear site, R-6b. Implementation note: the void copy must read `s.window_void` before the `opened_by == did` clear on a `reopen` tick (R-1 and R-2b fail otherwise) |
| B5 | CLOSED | W3-6 spec, §7 "Void readers", D-12 |
| B6 | PARTLY CLOSED | Grammar and W4 duty are done. D-10b as written is red on the day W3-A0 lands: today `telemetry/mapping.py`, `tune/cli.py` and `tools/console/views.sql` each have `outcome` + `reward` and no `void` (scan run 2026-11-16: exactly these 3 trip, 0 others). Every later order runs `pytest -x`. **E1:** W3-A0 ships D-10b with `xfail(strict=True, reason="until W3-2 and W3-6")`; the architect removes the marker at wave integration; the wave is not done while the marker exists |
| B7 | PARTLY CLOSED | Hash, counts, G-1 and D-4b are in. **E2:** (a) the coordinator hands W3-0 the hash together with a clean-tree statement (`git status --porcelain` empty for `src/`, `parser/`, `tests/`); the tree today has about 55 modified files, a golden taken on it would carry the wrong hash. (b) W3-1b step 0 says "same merged commit" but runs after W3-A0: reword to "merged commit plus W3-A0, before any T4 source edit". (c) the W3-0 normaliser pops `carry` / `window_void` with a default, since the keys do not exist at capture time |
| B8 | CLOSED | §10.1, six statements, all required points present |

Rulings on the designer's open items:

1. **B6 Wave 4 duty: confirmed.** `IMPLEMENTATION_WAVES.md` §10 carries the text for W4-1 and W4-3. It is an
   amendment paragraph, not in the row cells; the Wave 4 plan gate must check it again.
2. **B1 residual: not blocking for code, blocking for turning the flag on.** The carry half is behind the
   default-off flag; the evidence half is today's assembler behaviour (`_save = result`), not new in Wave 3.
   But the residual is worse than §13 item 6 says: once a foreign key is taken as the first keyed observation of
   an epoch, the real campaign's key is the "foreign" one for the rest of that epoch, and the run's `state.json`
   is saved under the foreign ident with the real campaign's evidence. Required (W3-5, text only): state this in
   §13 item 6, and add to §10 step 5: "zero `foreign save key` events over the six sessions, else the flag stays
   off". Do not build a narrowing rule now.
3. **B7 hash from the coordinator: fine**, with E2 (a).
4. **`foreign_epoch`: accept.** One int; the engine is pure, so the once-per-epoch dedupe has to live in state and
   survive a re-exec. It sits inside `carry`, so C-13 and flag-off byte equality are untouched.

Bottom line: the two paths that let another campaign or an abandoned branch act on the live game are closed in the
design, and the void reader hole is closed. Apply E1 and E2, then start W3-A0 and W3-0.
