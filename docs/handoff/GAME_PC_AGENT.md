# Game PC agent runbook

You are the agent on the Stellaris PC. The PC time is short: follow the order below, critical first. Every game
behaviour here is **UNVERIFIED** until you run it; record what you saw, not what the docs expect.

## 1. Prerequisites

1. Windows, PowerShell, `uv`, `git`.
2. A git clone of this repo and `uv sync` in it. REQUIRED for pytest: the X2 field kit has no tests. (~10 min,
   needs network; fallback: a clone copied on a USB stick.)
3. `sae-parse.exe`: the `bin\` folder of CI artifact `sae-m0-field-kit-windows`, or
   `cd parser; cargo build --release -j 1`.

## 2. Safety

1. Never edit the Stellaris install folder or the user mod folder without the human's permission.
2. You never launch the game. The human does. You stage files and read evidence.
3. Quit Stellaris before parsing saves: `sae-parse` exits 6 on a sharing violation.

## 3. PowerShell env setup

```powershell
$out = "C:\sae_out"; New-Item -ItemType Directory -Force $out | Out-Null
$env:SAE_PARSER_EXE = "<path>\sae-parse.exe"
$env:SAE_GAME_SAVES_DIR = "<Documents>\Paradox Interactive\Stellaris\save games\<campaign>"
$env:SAE_GAME_LOG = "<copy of logs\game.log>"              # copied AFTER quitting
$env:SAE_GAME_IRONMAN_SAVE = "<path>\ironman.sav"          # optional, M0-14
$env:SAE_GAME_EXPECT_ACK = "1"                              # only if the log is from the M0-6 session
$env:SAE_GAME_MAX_SAVES = "24"                              # optional cap, newest first
# optional: $env:SAE_GAME_RECORDING = "<ready replay dir>"
```

## 4. Producing each input

| Var | How |
|---|---|
| `SAE_GAME_SAVES_DIR` | One campaign folder with >= 13 monthly autosaves: mod enabled, monthly autosave (A-24), play 12+ months (the M0-5 session). |
| `SAE_GAME_LOG` | After quitting, copy `logs\game.log` of that same session (M0-3/5/6). |
| `SAE_GAME_RECORDING` | Optional. Else built from LOG + SAVES_DIR into `$out\private\tmp` (or `uv run python3 tools/game/make_recording.py --log <log> --saves <dir> --out <new dir>`). |
| `SAE_GAME_IRONMAN_SAVE` | A 2-month Ironman game with the mod (M0-14); pick the `.sav`. |
| `SAE_PARSER_EXE` | See prerequisites. |

## 5. Session plan (budget about 4 h; stop at the budget)

| # | Step | Est. | How |
|---|---|---|---|
| 1 | Critical: M0-2, M0-3, M0-5, M0-6, M0-11 | 2-3 h | `uv run sae m0 steps M0-2` (and so on), then `uv run sae m0 --parser-exe $env:SAE_PARSER_EXE stage / check / record` (global `--parser-exe` goes before the subcommand). Steps: `docs/m0_checklist.md`, `tools/m0/README.md`. |
| 2 | X3 deferred run (game quit) | 25 min | `uv run python3 tools/game/run_deferred.py --list`, then `uv run python3 tools/game/run_deferred.py --out $out` (details: `docs/handoff/deferred/X3.md`). |
| 3 | Other deferred files | per file | See the index below. |
| 4 | M0-14 Ironman, then X3-G6 | 30 min | `uv run sae m0 --parser-exe $env:SAE_PARSER_EXE check M0-14 --save $env:SAE_GAME_IRONMAN_SAVE`, then `uv run sae m0 record M0-14`, then the X3-G6 command in `docs/handoff/deferred/X3.md`. |
| 5 | Stretch, only if time allows: M0-12, M0-13, R-1..R-6 | rest | See references. |

## 6. Deferred index (never hard-code)

```powershell
Get-ChildItem docs/handoff/deferred/*.md
uv run python3 tools/game/run_deferred.py --list
```

Every `### <ID> — title` heading in those files is one entry. Run each entry's command as written there; X2 and X4
write their files in parallel with this one. Generic row for those ids in the results template.

## 7. Reference tables (link, do not copy)

| Ids | Source | Where to record |
|---|---|---|
| M0-1..M0-14 | `docs/m0_checklist.md` section A (command column: `sae m0 check/record <id>`, pass rule: "pass" column) | `sae m0 record <id>` plus the template row |
| A-01..A-46 | `docs/m0_checklist.md` section B | template notes column; `sae m0` output |
| R-1..R-6 | `bench/README.md` (commands `uv run sae bench overhead/stutter/script-cost/bus ...`) | template R rows plus the CSVs it names |
| V-1..V-5 | `docs/TEAM_PLAN.md` "Validation backlog" (V-3/V-5 via X3-G9/G11; V-1 via X4) | template V rows |

## 8. Privacy

1. No raw `game.log`, saves, recordings, or anything under `$out\private\` leaves the PC unless the maintainer
   says so. That includes `junit.xml` and `private\tmp` (pytest's basetemp, which holds save and log copies).
2. Share only: `summary.md`, `inputs.json`, the filled results template, and the `sae m0` pack output.
3. After the maintainer confirms receipt: `Remove-Item -Recurse -Force $out\private`.

## 9. Finish

Fill `docs/handoff/GAME_PC_RESULTS_TEMPLATE.md` (copy it to `$out\results.md`), mark every un-run entry
"not run" with a reason, and report failures with exit code and `summary.md` line.
