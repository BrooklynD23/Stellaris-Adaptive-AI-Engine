# X2 — M0 field kit (T8 lead, T7 CI)

Status: REVISED 2026-10-20 after `sae-adversary` review (`docs/status/X2_plan_review.md`, ACCEPT WITH CHANGES;
all 4 blocking and 4 required items folded in below). Branch `feat/x2-m0-field-kit`.

Goal: a person with **no dev environment** on the Windows PC that has Stellaris installed downloads one CI
artifact, runs one PowerShell script, plays the M0 weekend with `sae m0`, runs `sae m0 pack` and sends one zip
back. No Rust, no repo checkout, no admin, no pre-installed Python.

## 1. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | No contract change, no proposal 027. `sae m0` subcommands are "defined in T8's plan; entry point only is contracted" (C8 §5). `doctor`, `pack` and the `--kit-dir` flag are defined here. | C8 §5 row `sae m0` |
| D2 | `sae m0 pack` (Python) instead of `collect.ps1`. | testable game-free on Linux, counts toward coverage, one less script |
| D3 | Kit assembly is a Python script `tools/m0/field_kit/build_kit.py` (T8); the T7 CI job only calls it and uploads. | layout logic is unit-testable; T7 owns only YAML |
| D4 | `uv` is fetched as the **pinned release zip** from the official `astral-sh/uv` GitHub release and verified against a SHA-256 hardcoded in `setup.ps1`, extracted per-user to `%LOCALAPPDATA%\sae-kit\uv\`. Not `irm \| iex` of `install.ps1`. | same official source, per-user, but no unhashed remote script execution |
| D5 | `sae-parse.exe` is copied into `uv tool dir --bin` (same folder as `sae.exe`, on the user `PATH`). No `--set paths.parser_exe` needed: C8 §3 lookup finds it on `PATH`. | works for `sae m0` (which takes no `--set`) and later `sae daemon` |
| D6 | Kit dir resolution in `sae m0`: `--kit-dir` flag, else the repo root if `<repo>/mod/sae_bridge` exists (dev checkout), else the current directory. `{repo}` in check steps is filled with the kit dir. | installed wheel has no repo; user works from the kit folder |
| D7 | Doctor's C4 check: `sae-parse --version` format, then a **fixture round-trip** in a temp dir (`sae-parse fixture` then `sae-parse parse`), validated with `PARSE_OUTPUT_ADAPTER` (`schema_version == 1`). | `--version` carries no schema; the round-trip proves the binary runs on that PC (MSVC CRT) and speaks C4 |
| D8 | All process spawns in `sae.m0` stay behind the one `subprocess.run` in `evidence.py` (new `run_exe(exe, args)`; `run_parser` calls it). | keeps the static guard `test_kit_spawns_only_the_parser` true |
| D9 | Network on the field PC (asked once, listed before asking): uv zip from `github.com/astral-sh/uv` (if missing), a uv-managed CPython 3.12 from `github.com/astral-sh/python-build-standalone` (if none ≥ 3.11), `pydantic`/`psutil` from `pypi.org` at versions pinned in the kit's `constraints.txt`. | bundling Python and wheels is backlog (offline kit) |
| D10 | `sae-parse.exe` for the kit is built with `RUSTFLAGS=-C target-feature=+crt-static` (kit job only). | runs on a clean Windows PC without the VC++ redistributable (review B3) |
| D11 | CI verifies `$UvSha256` in `setup.ps1` against the 0.13.1 release's `.sha256` asset; mismatch fails the job. | a wrong hash fails in CI, not on the field PC (review B2) |
| D12 | `SHA256SUMS` is an integrity check (corrupt or partial download), not authenticity. The README says so. | a tampered kit can rewrite it (review R8) |

## 2. Artifact layout

CI artifact `sae-m0-field-kit-windows` (GitHub delivers it as one zip; retention 30 days; no release, no tag):

```text
sae-m0-field-kit-windows/
  setup.ps1                     bootstrap (this plan §3)
  README.md                     copy of tools/m0/README.md (Field kit section first)
  m0_checklist.md               copy of docs/m0_checklist.md
  m0_results_template.md        copy of tools/m0/m0_results_template.md
  sae-0.1.0-py3-none-any.whl    `uv build --wheel`
  constraints.txt               `uv export --no-dev --no-hashes --no-emit-project --format requirements-txt`
  bin/sae-parse.exe             `cargo build --release -j 1` (MSVC, windows-latest)
  mod/sae_bridge/**             mod/sae_bridge minus tests/, tools/, __pycache__/, *.md
  KIT_INFO.json                 {"git_sha", "built_at", "sae_version", "parser_version"}
  SHA256SUMS                    "<sha256>  <relative/posix/path>" for every file above except itself
```

## 3. Bootstrap flow (`setup.ps1`)

Runs under Windows PowerShell 5.1 and pwsh 7 (no 7-only syntax). Invoked as
`powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1` (process-scoped bypass, no admin).
Parameters: `-DryRun` (print every action as `DRY-RUN would: ...`; no download, no install, no copy, no PATH
change, no prompt, runs no external program; exit 0).

1. Verify every line of `SHA256SUMS` with `Get-FileHash`; any mismatch or missing file → stop, exit 1.
2. Locate uv: `Get-Command uv`, else `%LOCALAPPDATA%\sae-kit\uv\uv.exe`.
3. Print the download list (D9) with URLs, pinned uv version and SHA-256; ask `Continue? [y/N]`; anything but
   `y` → exit 0 with nothing changed.
4. If uv missing: `Invoke-WebRequest` the pinned zip to `$env:TEMP`, check SHA-256, `Expand-Archive` to
   `%LOCALAPPDATA%\sae-kit\uv\`.
5. `uv tool install --python 3.12 --reinstall -c constraints.txt <wheel>`.
6. `uv tool dir --bin` → copy `bin\sae-parse.exe` there. If that folder is not on the user `PATH`: ask, then
   `uv tool update-shell`; prepend it to `$env:PATH` for this session either way.
7. Preflight: `sae m0 --kit-dir "$PSScriptRoot" --results "$PSScriptRoot\m0_results" doctor` (results passed
   explicitly, review B4; prints PASS/WARN/FAIL, writes `<kit>\m0_results\doctor.txt`).
8. Print, never perform: mod copy source `<kit>\mod\sae_bridge` → destination `<user_dir>\mod\sae_bridge`
   (user_dir from doctor's output), then `sae m0 stage M0-2` for the `.mod` file, then the next command
   `cd <kit>; sae m0 steps M0-2`, and the uninstall lines (`uv tool uninstall sae`, delete `sae-parse.exe`
   from the bin folder, delete the kit folder).

The script **never** writes under `Documents\Paradox Interactive`, the Stellaris install or the user mod
folder (settled). A static test greps for it (§6).

## 4. `sae m0 doctor`

Read-only on game folders. Spawns only `sae-parse` (via `evidence.run_exe`). Writes `<results>/doctor.txt`.
Exit 0 if no FAIL, else 1. One line per check: `PASS|WARN|FAIL  <name>  <detail>`.

| Check | PASS | WARN | FAIL |
|---|---|---|---|
| sae | `importlib.metadata.version("sae")` + Python version | | |
| results folder | `_safe_results` accepts it | | inside a game folder |
| sae-parse found | resolved path (C8 §3) | | none found |
| sae-parse version | `--version` exit 0, matches `^sae-parse \d+\.\d+\.\d+$` | | otherwise |
| C4 schema | fixture round-trip validates, `schema_version == 1`, exit 0 | | otherwise (detail: exit code, first 200 chars) |
| user folder | exists | missing (game never run, or OneDrive: pass `--user-dir`); also WARN when both the shell Documents path and `%USERPROFILE%\Documents` hold a `Paradox Interactive\Stellaris` folder (A-33, UNVERIFIED) | |
| mod in kit | `<kit>/mod/sae_bridge/descriptor.mod` exists; prints copy source and destination; notes if destination already exists (read-only) | | missing |
| disk | ≥ 5 GiB free on the results drive | 1–5 GiB | < 1 GiB |

## 5. Privacy of returned data (`sae m0 pack`)

`sae m0 pack [--out <zip>]` (default `./m0_results_<UTC yyyymmdd-HHMMSS>.zip`, refused inside game folders via
the same guard) zips an **allowlist** from `<results>`:

- `env.json`, `M0-*.json` (excluding `M0-6.stage.json`), `M0-4.tail.jsonl`, `m0_results.md`, `doctor.txt`.
- Never: `stage/`, any `.log`, `.sav`, `.ini`, `.rrd`, `bus*.jsonl`, record dirs, anything not on the list.
- Text is rewritten on the way in: the user's home folder path (both `\` and `/` forms, case-insensitive) →
  `~`, and the bare user name (`getpass.getuser()`, whole word, case-insensitive) → `~` (review R7). Originals
  are untouched.
- Then each `M0-*.json` is JSON-loaded and redacted (review B1): drop `evidence.sample` (holds `name=<empire
  name>`, `evidence.py:209`); truncate `evidence.error.detail` to 200 chars (`ParseError.detail`, up to 2000,
  `evidence.py:262`); drop any `evidence.save.path` / `evidence.path` key (C4 `SaveInfo.path` carries
  `save games/<empire>/`); regex `name=[^"\n]*` (to end of string or line; names hold spaces) → `name=<redacted>` in `notes` and in all remaining strings.
  Non-JSON allowlisted text files (`m0_results.md`, `doctor.txt`, `M0-4.tail.jsonl`) get the same `name=` regex.
- Prints the manifest (file, bytes) and this notice: the zip holds check verdicts, timings, record types and
  dates, five numeric player-empire totals from M0-11/M0-14 (`god`: fleet power, systems, techs, alloys,
  fortified starbases), empire names redacted, parser error text cut to 200 chars, and whatever you typed in
  `--note`, `--value`, `--playset`, `--machine` (home path and user name scrubbed).
- Raw `game.log`, saves and `error.log` are never read into the zip (README "After").

## 6. Tests (all game-free)

| Test | File | Where it runs |
|---|---|---|
| doctor: every row PASS/WARN/FAIL with fake folders, fake parser script (`--version`, `fixture`, `parse`), OneDrive dual-folder WARN via mocked `windows_documents`, disk thresholds via mocked `shutil.disk_usage`, exit codes, `doctor.txt` written, game folder snapshot unchanged | `tests/unit/t8/test_m0_field_kit.py` | all |
| pack: allowlist, exclusions (`stage/`, `.log`, `.ini`, `bus.jsonl`), home-path and user-name scrub, refused inside user dir, manifest printed | same | all |
| pack redaction: an M0-5.json with `evidence.sample` + `name=Foo` in notes, an M0-11.json with a 2000-char `evidence.error.detail` and a save path → zip has no `sample`, no `Foo`, detail ≤ 200 chars, no `save games` | same | all |
| `--kit-dir` default resolution and `{repo}` fill in `steps` | same | all |
| existing `test_kit_spawns_only_the_parser`, `test_no_command_writes_to_the_game_folders` still pass (doctor and pack added to the latter's loop) | `tests/unit/t8/test_m0_kit.py` | all |
| build_kit: layout, mod exclusions, SHA256SUMS verifies, KIT_INFO fields, refuses a non-empty out dir | `tests/unit/t8/test_field_kit_build.py` | all |
| setup.ps1 static: parses with `[System.Management.Automation.Language.Parser]::ParseFile` with zero errors under `pwsh` and `powershell.exe` (each if present) | same file | Windows (skip if no PowerShell) |
| setup.ps1 `-DryRun` on a temp kit (built by build_kit from fakes), `LOCALAPPDATA`/`TEMP` pointed at tmp: exit 0, prints `DRY-RUN would:` lines incl. pinned uv URL when uv is hidden from `PATH`, no file created outside the kit, kit unchanged, user `Path` registry value unchanged | same file | Windows (skip elsewhere) |
| setup.ps1 static: `$UvSha256` matches `^[0-9a-f]{64}$` and is not one repeated character; passes `--results` to doctor; names `python-build-standalone` | same file | all |
| setup.ps1 text never targets `Paradox Interactive` with a write cmdlet (`Copy-Item`, `Set-Content`, `Out-File`, `New-Item`, `Move-Item`, `Expand-Archive`, `robocopy`) | same file | all |
| CI smoke: `setup.ps1 -DryRun` on the real assembled kit | `field-kit-windows` job | CI |
| CI: `$UvSha256` equals the 0.13.1 `.sha256` release asset; kit `sae-parse.exe` built with `+crt-static` | `field-kit-windows` job | CI |

Gates: `uv run pytest -x -q`, `uv run mypy --strict src`, `uv run ruff check`, coverage ≥ 90 % (`src/sae`).

## 7. Owned paths and work orders

| WO | Team | Batch | Files |
|---|---|---|---|
| X2-1 doctor, pack, kit-dir | T8 | 1 | `src/sae/m0/{cli,doctor,pack,evidence,checks}.py`, `tests/unit/t8/test_m0_field_kit.py`, `tests/unit/t8/test_m0_kit.py` |
| X2-2 build_kit + setup.ps1 | T8 | 1 | `tools/m0/field_kit/{build_kit.py,setup.ps1}`, `tests/unit/t8/test_field_kit_build.py` |
| X2-3 CI job | T7 | 1 | `.github/workflows/ci.yml` |
| X2-4 docs | T8 | 2 | `tools/m0/README.md`, `packaging/PLAN.md`, `CHANGELOG.md`, `docs/BUILD_REPORT.md`, `docs/TEAM_PLAN.md` (X2 row only, assigned by the orchestrator), `docs/status/T8.md`, `docs/components/T8.md` |

Interfaces fixed now so batch 1 runs in parallel:
`uv run tools/m0/field_kit/build_kit.py --out <dir> --wheel <whl> --parser-exe <exe> --constraints <txt> [--git-sha <sha>]`
(exit 0, layout §2); `sae m0 [--kit-dir <dir>] doctor`; `sae m0 pack [--out <zip>]`; `setup.ps1 [-DryRun]`.

## 8. Risks

| Risk | Mitigation |
|---|---|
| uv zip SHA-256 hardcoded wrong | X2-2 takes it from the release's own `.sha256` asset; CI step compares it to that asset on every run (D11); first real run fails closed |
| uv zip layout (`uv.exe` at the zip root) | UNVERIFIED by tests; labelled in `setup.ps1`; `Test-Path` after extract, fail with a message |
| Windows release cargo build cost on every push/PR | kit job gated `if: github.event_name != 'pull_request'` |
| `uv tool install -c` flag differs in the pinned uv | X2-2 checks `uv tool install --help` for the pinned version; fallback: `--with` pins |
| SmartScreen / Mark-of-the-Web blocks `sae-parse.exe` or the script | README tells the user `Unblock-File` / "More info → Run anyway"; `-ExecutionPolicy Bypass` per process; signed binary is backlog |
| PowerShell 5.1 incompatibilities | parse test under `powershell.exe`; CI DryRun smoke |
| Antivirus quarantines unsigned exe | doctor FAILs "sae-parse not found/not runnable" with that hint |
| Wrong user folder (OneDrive, A-33 UNVERIFIED) | doctor WARN; `--user-dir` in every command |
| Field PC has no internet | out of scope; backlog: offline kit with bundled Python + wheels |
| Artifact retention expires | `workflow_dispatch` rebuilds it |

## 9. UNVERIFIED items (to T8's M0 checklist, already present)

A-01 (mod `path=` form, M0-2), A-33 (shell Documents == game's folder, OneDrive), A-24 (monthly autosaves).
No new game assumption: the kit only adds packaging. Mod copy destination `<user_dir>\mod\sae_bridge` is the
existing M0-2 step.

## 10. Backlog

Offline kit (bundled CPython + wheels); signed `sae-parse.exe`; hashed `constraints.txt` once `uv tool install`
honours hashes; `Swatinem/rust-cache` for the kit job; mod zip release script (after M0-2); GitHub
release (maintainer approval, packaging/PLAN.md §5).
