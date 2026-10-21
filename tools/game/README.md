# tools/game

Stdlib plus `sae.*` scripts for the game-deferred tests (X3). No package; run with `uv run python3`.

## run_deferred.py

```powershell
uv run python3 tools/game/run_deferred.py --out C:\sae_out -- -k X3-G1
uv run python3 tools/game/run_deferred.py --list
```

Runs `pytest -m game` with `--junitxml <out>/private/junit.xml --basetemp <out>/private/tmp`, then writes
`<out>/summary.md` (counts, per-test table, deferred index from `docs/handoff/deferred/*.md`) and
`<out>/inputs.json` (names of the `SAE_GAME_*` vars set, platform, Python, `sae-parse --version`). Args after `--`
go to pytest. Exit code is pytest's.

## make_recording.py

```powershell
uv run python3 tools/game/make_recording.py --log game.log --saves "save games\campaign" --out C:\rec1
```

Builds a replay session dir (`game.log`, `game.log.ts`, `saves/<offset:012d>-<name>.sav`) from a real log and an
autosave folder; `--out` must be new or empty. Uses the last HELLO segment, PLYR redaction via `RecordGate`, and
`--parser-exe` / `SAE_PARSER_EXE` / PATH / repo build. Save placement by game date is approximate (UNVERIFIED X3-U1).

## Privacy

Shareable: `summary.md`, `inputs.json` (env values and your home path are replaced by `<VAR>` / `<HOME>`).
NOT shareable: everything under `private/` (`junit.xml`, `tmp/` with copies of saves and logs) and any recording.
