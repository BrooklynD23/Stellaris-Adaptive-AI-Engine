# M0 weekend runbook

Goal: by Sunday evening, `m0_results/m0_results.md` answers the go/no-go rule. Every test and assumption is in
[`docs/m0_checklist.md`](../../docs/m0_checklist.md); `sae m0 steps <id>` prints the exact steps for one test
with your paths filled in.

**The kit never launches Stellaris, never writes in the install folder, the `mod/` folder or the Stellaris user
folder.** You start the game, copy files and type console commands yourself; `sae m0` stages files under
`m0_results/stage/` and reads the evidence you produce.

## Before the weekend (30 min)

1. `uv sync` and `cd parser && cargo build --release -j 1` (M0-11 needs `sae-parse`).
2. `uv run sae m0 list` — every check shows `pending`.
3. Check the detected folder: `uv run sae m0 steps M0-2`. If your Documents folder is on OneDrive or elsewhere,
   add `--user-dir "<your Stellaris user folder>"` to every `sae m0` command.
4. Back up `<user_dir>/mod/` and your save folder.

## Saturday morning — channels (≈ 3 h)

| # | Test | Command |
|---|---|---|
| 1 | header | `sae m0 init --game-version <v> --checksum <c> --starnet-build <id or vanilla> --playset "<mods>"` |
| 2 | M0-1 StarNet hook | `sae m0 check M0-1 --starnet-dir "<workshop folder>"` |
| 3 | M0-2 mod loads | `sae m0 stage M0-2`, copy by hand, launch, 1 month, quit, `sae m0 check M0-2` |
| 4 | M0-3 log lines | 3 months, `sae m0 check M0-3`; save mid-month, reload, 1 month, check again; `sae m0 record M0-3 ...` |
| 5 | M0-4 flush | `sae m0 tail --seconds 300` while playing; pause just after a month starts; Ctrl+C is fine; `sae m0 record M0-4 ...` |
| 6 | M0-5 values | 12 months with an AI in contact; `sae m0 check M0-5` (prints managed sids, player sid, latest `m`); then **save as `m0_8_base` before M0-6** (the unflagged twin for M0-8) |
| 7 | M0-6 run file | `sae m0 stage M0-6 --sid <s> --m <m> --target <player sid>`, copy, console `run sae_in.ini`, next month, `sae m0 check M0-6`; retry = stage again (seq bumps itself) |

Meet another empire early (small galaxy, few empires): SAE only manages AIs in contact with you (A-32), and M0-5
and M0-6 need one.

## Saturday afternoon — save parse and A/B start (≈ 3 h)

| # | Test | Command |
|---|---|---|
| 8 | M0-11 parse | monthly autosaves on (A-24, UNVERIFIED); `sae m0 check M0-11 --save "<autosave>"`; compare with the UI; `sae m0 record M0-11 ...` |
| 9 | M0-8 A/B | load `m0_8_base` from step 6 (Stellaris has no seed field, UNVERIFIED); game B: load, econ=military via the M0-6 procedure, APPLIED ack, console `observe`, 15 years at fastest speed (overnight); game A on Sunday morning: load, `observe`, 15 years. One game at a time |
| 10 | M0-12 baseline (stretch) | 12 months mod off vs on on your real save; `sae m0 record M0-12 ...` |

## Sunday (≈ 4 h)

| # | Test | Command |
|---|---|---|
| 11 | M0-8 result | after game A finishes, read both fleet powers; `sae m0 record M0-8 pass\|fail --value fleet_a=<n> --value fleet_b=<n>` |
| 12 | M0-7 (only if M0-1 passed) | 10-year corvette test; else `sae m0 record M0-7 skip --note "M0-1 failed"` |
| 13 | M0-14 Ironman (stretch) | `sae m0 check M0-14 --save "<ironman.sav>"`, `sae m0 record M0-14 ...` |
| 14 | M0-13 subplan (stretch) | `sae m0 record M0-13 ...` |
| 15 | later tests | `sae m0 record M0-9 skip`, `M0-10 skip`, `M1-P skip`, `R-3 skip`, `R-5 skip` |
| 16 | summary | `sae m0 summary` → `m0_results/m0_results.md` |

## After

Send `m0_results/` (JSON files, `M0-4.tail.jsonl`, `m0_results.md`) to the team; the kit stores record types and
dates only. If you also ran `sae daemon` or `sae synth --record`: share `bus.jsonl` and `.rrd` files, not the record
dir: it holds the raw game log with player data. Remove `<user_dir>/sae_in.ini`
and the mod by hand if you want a clean install.

`m0_results_template.md` here is the same table for recording on paper.
