# Wave A plan review (sae-adversary, pre-implementation)

Verdict: **ACCEPT WITH CHANGES.** Items 5, 7a, 9-memo and 10 go ahead with the changes below. **Item 8 is
rejected for Wave A** and moves to Wave D, gated on an M0 folder count. Reviewed 2026-11-14 against the code, not
the reports. Sources: `docs/plans/opt/SYNTHESIS.md` §2/§5, `daemon-io.md`, `profile-offline.md`, `stack.md`,
`contracts/C3_intents.md`, `C5_bus.md`, `C6_game_adapter.md`.

## Per item

| # | Item | Verdict | Blocking? |
|---|---|---|---|
| 5 | fsync policy | ACCEPT WITH CHANGES: gate by adapter mode, leave LIVE unchanged | yes (B1, B2) |
| 7a | `stdin=DEVNULL` in `sae play` | ACCEPT WITH CHANGES: the proposed test passes without the fix | yes (B3) |
| 8 | Hot-folder save polling | REJECT for Wave A, move to Wave D | yes (B4) |
| 9 | Tuner grid memo | ACCEPT WITH CHANGES: a stronger equality test | no |
| 10 | Kit consent hosts + M0-12 row | ACCEPT WITH CHANGES: 3 more wrong hosts; the row measures the wrong metric | yes (B5, B6) |

## Item 5: fsync (`src/sae/bus/writer.py:174-186`, `src/sae/daemon/statefile.py:23-30`)

Verified in code:
- Today the writer fsyncs on every month change, per file (`writer.py:175-177`).
- `save_state` always fsyncs (`statefile.py:29`) and runs on every decided month (`loop.py` `_on_obs`, `self.save()`).
- SYNTH and REPLAY cannot resume: `sae daemon` refuses them (`daemon/cli.py:110-111`), and `daemon/__init__.py:30`
  calls `acquire_current(resume=False)`.
- Contract: C5 §1 says "`fsync` at most once per game month and on exit", so fewer syncs stay compliant. The
  label "none" is correct.
- The C3 §5 seq floor comes from `state.json`, not from the bus, so keeping the `state.json` fsync in LIVE is
  load-bearing. The report was right on that point.

Attack:
1. **The gain is offline only.** The -45 to -55 % is a SYNTH number. In LIVE the time rule saves 2 fsyncs per game
   month (about 5-8 ms on ext4, and NTFS is UNVERIFIED) on a loop that idles 99 %. In exchange, after a power cut
   the bus can lose up to 30 s of events. At fast speed that is several game months of `intent_emitted` and
   `belief_update` rows. `state.json`, which is still synced, is then ahead of the bus, and the profile and tuner
   read a bus with silent holes. There is no seq gap to detect, because `_recover` continues from the tail.
   Process crashes lose nothing either way, because `os.write` reaches the OS cache.
2. **The smallest alternative removes the debate.** Skip the fsync when `config.adapter` is SYNTH or REPLAY, both in
   `_Stream` and in `save_state`. LIVE and RELEASE stay byte-for-byte as today. It is about 1 h instead of 3 h, and
   it yields the whole measured gain.
3. **The time rule depends on the real clock.** `BusWriter` uses `time.monotonic_ns` by default. SYNTH finishes 30
   years in about 7 s, so "30 s since the last sync" means it effectively never syncs. That is fine, but it is the
   mode gate disguised as a clock rule.

Required:
- **B1.** Replace "month change and ≥ 30 s" with a mode gate. Add `durable = config.adapter in (LIVE, RELEASE)` to
  `BusWriter`, and pass it to `_Stream` so `append` and `sync` skip `os.fsync` when it is false. Add
  `save_state(path, state, *, fsync=True)`, and have `_Loop.save` pass `fsync=adapter.mode not in LOCKSTEP_MODES`.
  Keep the fsync on close in every mode, because C5 says "and on exit". Revisit the time rule for LIVE only after
  M0 times `os.fsync` on the game PC (daemon-io validation 4).
- **B2.** Replace the validation test. "Identical bus content hash" proves nothing: fsync never changes bytes, and
  `t_wall`, `t_mono_ns` and `run_id` differ on every run, so a raw hash fails anyway. Corrected tests:
  (a) In `tests/unit/t6/test_bus_writer.py`, a parametrised version of `test_fsync_once_per_month`. With the
  adapter set to live it gives 2 + 2 calls, as today. With synth or replay it gives 0 calls during emits and 2
  calls on close.
  (b) In `tests/unit/t4/test_statefile.py`, `fsync=False` gives `["replace"]` only.
  (c) A 30-year synth run with fsync counted gives exactly 2 bus-writer fsyncs and 0 `state.json` fsyncs. A
  normalised comparison (drop `t_wall` and `t_mono_ns`) is equal with and without the change for a fixed seed.
  Keep the wall-time halving as a reported number, not as a pass/fail gate, because it depends on the WSL load.

## Item 7a: `stdin=DEVNULL` (`src/sae/profile/launcher.py:44`)

Verified: `subprocess.run(..., timeout=600)` inherits stdin, and `_ask_split` calls `input()` when stdin is a TTY
(`profile/cli.py:137-145`). The fix is correct and a one-liner. Pending candidates are already reported
(`cli.py:214-215`).

Attack:
1. **The proposed test is vacuous.** "`sae play` on a pipe ends < 1 s" already passes today, because on a pipe
   `isatty()` is False and the prompt never runs. The stall only happens on a TTY.
2. **Windows behaves differently.** CPython reports `isatty()` as True for the `NUL` device (a character device;
   UNVERIFIED for 3.12 here). In that case `input()` runs, hits EOF and returns None through the existing
   `except EOFError`. The result is correct, but the prompt text is printed into the `sae play` console.
3. The message "asked again next update" (`cli.py:215`) is now false for `sae play` users.

Required:
- **B3.** Test under a pseudo-terminal. On Linux, run `sae play` through `pty.fork()` or `pty.spawn` with a fixture
  run that creates a split candidate. Assert that the update child exits within 5 s, that the candidate stays
  pending, and that the daemon starts. Add a unit test asserting `subprocess.run` receives
  `stdin=subprocess.DEVNULL`. On Windows CI, run the same update with `stdin=DEVNULL` and assert it exits within
  5 s (this covers the `NUL` isatty path).
- Optional: change the message to "run `sae profile update` in a terminal to answer".

## Item 8: hot-folder save polling (`src/sae/adapters/savewatch.py:65-121`): REJECT for Wave A

1. **Ironman safety regression on a settled decision.** Ironman detection comes only from this watcher: the
   `ironman.sav` meta probe at `live.py:256-272` latches `error="ironman"`. Loading an existing Ironman campaign
   from a cold folder delays the latch from about 8 s to about 38 s (15 × 2 s + 8 s, and more with jitter on the
   `to_thread` hop). During that window the engine can emit to an Ironman game. "Ironman has no live loop" is
   settled.
2. **"Contract: none" is wrong.** The frozen C6 §1 `live` row says the adapter watches `save_dir/*/`, and that a
   save is eligible after 2 s stable and 8 s since its last change. Polling cold folders once every 30 s changes
   observable latency, so this needs at least a C6 note.
3. **Two latent bugs a straight implementation would ship:**
   (a) The prune `self._seen = {p: e ... if p in stats or p in failed}` (line 99) drops cold-folder entries on
   every hot-only poll. The next full rescan then re-adds them with `done=False`, and **old saves are re-reported
   every 30 s.**
   (b) `best = max(eligible, key=lambda p: stats[p][1])` (line 117) raises `KeyError` for an eligible cold entry
   that was not stat'd this poll. Computing eligibility from stale entries also skips the stability re-check: a
   file still being written could pass.
4. **The gain is unproven at real folder counts.** At 1 to 60 folders the cost is 1 to 4 ms/s (0.1 to 0.4 % of a
   core). Only 300 folders crosses the 1 % flip line, and folder counts are A-46, UNVERIFIED.

- **B4.** Move item 8 to Wave D, gated on M0 recording the save-folder count on the game PC. If it is revived, it
  needs all of the following:
  - List the top-level folders on every poll (one `listdir`), so a new campaign folder is found immediately and
    promoted to hot.
  - Prune `_seen` only on a full rescan.
  - Compute eligibility only for paths stat'd on this poll.
  - Write a C6 §1 proposal stating the cold-folder latency.
  - Add an explicit Ironman bound: a new folder's `ironman.sav` is probed within 1 poll of the folder appearing.
  - Test deterministically with the existing `listdir`/`stat`/`monotonic` injection, counting `listdir` calls
    instead of measuring CPU ms/s, which is noisy in CI. Cover: an old cold save is never re-reported across 3
    rescans; a cold save is reported exactly once and only after two stats ≥ 8 s apart; a growing file is never
    reported.

## Item 9: tuner memo (`src/sae/tune/fit.py:195-197`)

Verified: `_score` recomputes `fast_lls(vectors, model)` for every grid point. `fast_lls` does not depend on `rho`
or `clip` (`bench/gate/metrics.py:106-108`). The threshold is not scored (`fit.py:66-72`), so 180 points collapse
to the (kappa, rho, clip) cells. The model cache `(i, kappa)` already exists (`fit.py:221`). The memo is valid and
small. The citation `212-233` is close enough.

- Required (non-blocking): "policy sha256 unchanged" only checks the chosen point; a broken memo can still pick the
  same winner. Add a test on a synth fixture with ≥ 2 kappas and ≥ 2 thresholds asserting that `loco(...)` with the
  memo equals the unmemoised result for every `Candidate`, every fold score included. Keep the memo dicts local to
  `loco()`, not module-global (memory is +16 MB, and it is freed on return).
- Note: the memo hard-codes "threshold is unscored". If profile-offline r9 later scores the threshold, add
  `threshold` to the key.

## Item 10: kit hosts and M0-12 (`tools/m0/field_kit/setup.ps1`)

Checked against setup.ps1 and primary sources. The `releases.astral.sh` claim is **largely true** but incomplete,
and the cited line is off by one: the CPython line is `setup.ps1:59`, not `:58`.

- uv's source documents a default Astral mirror for CPython downloads at
  `releases.astral.sh/github/python-build-standalone/releases/download/`, with **fallback to GitHub** when the
  mirror fails ([uv-python downloads.rs](https://docs.rs/uv-python/0.0.28/src/uv_python/downloads.rs.html),
  [uv environment reference](https://docs.astral.sh/uv/reference/environment/)). So "an allowlist would block it"
  is overstated: a refused connection falls back, but a silently dropped connection may hang until timeout.
  Whether 0.13.1 on Windows does this is UNVERIFIED.
- The report missed 3 more errors in the consent text:
  - Line 59, "only if no Python >= 3.11", is false. Line 96 runs `uv tool install --python 3.12`, so uv downloads
    3.12 whenever no 3.12 is present, even if 3.11 or 3.13 is installed.
  - Line 60: wheels come from `files.pythonhosted.org`; `pypi.org` serves only the index.
  - Line 58: GitHub release downloads redirect to a `*.githubusercontent.com` asset host.

Required:
- **B5.** Make the text match what the script does. Two options:
  1. Set `$env:UV_PYTHON_INSTALL_MIRROR = 'https://github.com/astral-sh/python-build-standalone/releases/download'`
     before step 5, so only one documented host is used (it takes precedence over the Astral mirror per the uv
     reference), and keep GitHub in the text.
  2. List `releases.astral.sh` (with GitHub as fallback).

  Either way, also fix the 3.12 condition, add `files.pythonhosted.org` and the GitHub asset host, and update X2
  plan D9 to match. Verify with `uv -v tool install` on the Windows CI runner and record the hosts it contacts.
- **B6.** The M0-12 row as proposed records `daemon_rss_bytes`, which is psutil `memory_info().rss`
  (`platform/sensors.py:175`). On Windows that is the **total working set, shared pages included**, but the stack
  flip threshold is "**private** working set > 100 MiB". Have the M0-12 row record both: `daemon_rss_bytes` from
  the bus, and Task Manager's "Memory (private working set)" read by hand. Or restate the threshold against the
  working set. Also note that M0-12 is marked *stretch* (`docs/m0_checklist.md:39`): the only evidence that can
  flip the stack would then be optional. Mark this row required.
- The doc edits fall under the CLAUDE.md docs and CHANGELOG rule and need maintainer approval before any git command.

## Architecture status (after the required changes)

| Area | Status | Why |
|---|---|---|
| 1. Performance and memory | ON TARGET | Offline fsync and tuner wins are real. LIVE is untouched. Nothing adds resident memory. |
| 2. Telemetry | AT RISK | The M0-12 row measures working set, not the private working set the flip rule uses (B6). Bus durability is unchanged in LIVE only if B1 is taken. |
| 3. Adaptive AI engine | ON TARGET | No decision path changes once item 8 is deferred. Item 8 as written would delay save observations and the Ironman latch. |
| 4. Learning the profile from saves | ON TARGET | The 7a fix keeps `sae play` from stalling. The tuner memo cuts the LOCO loop 54 s to 12 s with an equality test. Split questions still need a manual `sae profile update` (message fix optional). |

Bottom line: Wave A is safe once fsync is gated by mode instead of time, item 8 is pulled for its Ironman and
re-report bugs, and the validation tests check something the change can actually break. Most of the cost is in
tests, not code. The total drops from about 13 h to about 6 h.
