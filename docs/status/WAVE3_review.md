# Wave 3 adversarial review (Fable, 2026-10-19)

Verdict: **accept with changes**. 0 release blockers against the MVP exit criteria. 6 non-blocking findings,
2 of which need a maintainer decision.

## BLOCKERS

None found.

## NON-BLOCKING (fix before tagging v0; none needs a contract bump)

| # | Where | Evidence | Scenario | Owner | Smallest fix |
|---|---|---|---|---|---|
| N1 | `src/sae/contracts/c4_parse.py:94` | Field says "Player systems visible to observer"; `parser/src/features.rs:356` sets `mine.len()` = all player systems. `contracts/C4_sae_parse.md:77` already states the total-with-approximation rule. | Reader of the model believes a fog mask exists; none does (A-19). Code matches the contract, the docstring does not. | architect | Reword the Field description to match C4 §(table) wording: "all player systems (v0 approximation, A-19)". |
| N2 | `src/sae/adapters/live.py:226` | `--record` appends raw `game.log` lines (incl. unredacted `PLYR`) under the run dir. T7 fog test (`tests/e2e/test_plyr_redaction.py`) covers `bus.jsonl` only. | A user shares a run dir for a bug report believing it is fog-legal. Not an engine leak (engine reads the assembler, not the recording). | T2 / T8 (user guide) | One sentence in `docs/user_guide.md` §(runs): the record dir is a raw log copy and contains god-view lines; share `bus.jsonl` only. |
| N3 | `src/sae/adapters/live.py:130`, `contracts/CHANGES.md` ("Open") | `sae_in.ini` is never deleted or neutralised on daemon close or on `mp=1`. Only (sid, seq) and `sae_managed` gate re-application. | Daemon dies in campaign A; weeks later user starts campaign B (sids restart at 1) and presses the hotkey out of habit: campaign A's econ/posture lands on an unrelated AI. Same for MP (user-guide §7 relies on the user). | **maintainer decision**, then T2 | On clean `close()` write a knob-only ini (no country lines) through the existing `AtomicIniWriter`; still never `unlink`. Recorded as "engine never deletes the file", so needs sign-off. |
| N4 | `src/sae/daemon/runs.py` | No prune/retention of `runs/<id>/` (`bus.jsonl`, `bus.god.jsonl`, `telemetry.rrd`, optional recordings with per-autosave `.sav` copies). | A player who records every session fills the disk; nothing warns. | T4 (runs) / T6 (bus) | `health` warning when `runs/` exceeds a configured size; document `--record` cost. No auto-delete. |
| N5 | `tests/integration/test_ci4_imports.py:19` | Whitelist = everything in the `uv.lock` runtime closure; only `FORBIDDEN_TOP` is a hard list. | Adding a GPU-pulling dep to `pyproject` passes CI-4 unless its import name is in the 11-name list. | T7 | Also assert the closure size/dist list against a committed expected list (fails on any new runtime dist). |
| N6 | `src/sae/adapters/live.py:62` (`include_ironman=release`) | LIVE mode ignores `ironman.sav`, so `meta.ironman` is never observed; the daemon keeps tailing and writing `sae_in.ini` in an Ironman game. Harmless (console blocked, UNVERIFIED A-20) but the "no live loop" rule is enforced by the game, not by SAE. | Ironman player runs `sae daemon`; sees `ack_alarm` every month with no explanation. | T2 / T4 | Parse `ironman.sav` meta-only in LIVE and raise a one-shot `health` "ironman: observe only" + suppress emits. Or document in user-guide §6 that `ack_alarm` is expected. **Maintainer** may prefer the doc route. |

## Checked and found clean

- **Fog**: god data leaves only via `Assembler._route_god` → `bus.god.jsonl`; `features.py` / `expansion.py` /
  `engine_tick.py` read `player_legal` (in-contact views) and `managed` self-state only; `LegalView` validator
  forbids features when not in contact; `logtail._redact` handles the three 012 leak shapes and the e2e test
  proves the sentinel reaches `game.log`, is absent from every `bus.jsonl` payload and present in `bus.god.jsonl`.
- **Machine/game safety**: `lower_self_priority` acts on the current pid only (`spawn.py:149`, `_win32.py:84`);
  sensors read only (no kill/suspend/nice on the game); parser worker per autosave, idle class, Job/cgroup/RLIMIT
  cap, `kill_current()` on close; logtail bounded (`MAX_READ` 1 MiB/poll, `MAX_PARTIAL` skip); inbox bounded
  (`MAX_COMMAND_BYTES`, regular-file check); bus `MAX_LINE_BYTES`; only writes under `user_dir` are `sae_in.ini`
  and its `.tmp` (atomic replace, retry on `PermissionError`).
- **MP guard**: `live.py:121`, `replay.py:305`, `synth.py:371` refuse writes while latest HELLO `mp=1`; the
  `mp` missing and no-reload-HELLO gaps are A-13 / X-08, documented in the checklist and user guide §7.
- **Commands**: 016 expiry implemented (`inbox.py:114`, session start / 60 s / 5 s future); `sae ctl` writes
  nothing unless `runs/CURRENT` names a live pid (`runlive.py:21`). No `_set_layer` leftovers.
- **Settled decisions**: no LLM imports; `FORBIDDEN_TOP` + `/proc/self/maps` GPU-lib check; CUSUM
  `change_score` only logged; `commitment_months=24`; safety floor → BASELINE; `outscale_siege`/`early_punish`
  distinct reason keys; three processes (daemon, worker exits, telemetry opt-in with no viewer spawn).
- **UNVERIFIED labelling**: README §1, user guide, `m0_checklist.md` (A-01..A-45, X-04..X-14), mod
  `descriptor.mod` and `UNVERIFIED.md`; `test_checklist_drift.py` ties tags to the checklist. R-1..R-6:
  "none run, none claimed" (`docs/status/T7.md:40`, README:55, bench/README). Likelihoods marked HYP
  (`belief.py:30`) and the user guide says they are synth-tuned.
- **Budgets**: CI-4 peak RSS via child `VmHWM` with ≥ 50 bus samples (non-vacuous); CI-5 breach under a 128 MiB
  cap on Linux; Windows cap deferred to R-5 and said so.
- **M0 kit**: `stage` writes to `m0_results/stage/` only, copy is manual, results dir refuses an install folder
  (`_INSTALL_MARKERS`); no `Popen` of the game; only subprocess is `sae-parse` on a save the user names. Runbook
  fits a weekend if M0-8 B runs overnight (`tools/m0/README.md:39`).
- **Docs vs code**: README/user-guide subcommands (`daemon replay synth ctl telemetry bench m0`) match
  `src/sae/cli.py:13-19`.

Bottom line: the integrated system keeps god-view data out of the engine and bus, touches nothing of the game's
except one ini file, and is honest about having never met the game. Fix N1 and N2 before tagging; N3 and N6 need
the maintainer to pick between code and documentation.
