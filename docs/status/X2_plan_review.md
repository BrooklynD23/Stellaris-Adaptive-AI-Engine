# X2 field-kit plan — adversarial review (sae-adversary, Fable 5.1)

Date 2026-10-20. Plan: `docs/plans/X2_field_kit_plan.md` (DRAFT). Verdict: **ACCEPT WITH CHANGES**.

## Blocking (fix in the work orders before batch 1)

1. **Privacy notice is false: M0-5.json and M0-11/M0-14.json carry names and paths.**
   `src/sae/m0/evidence.py:209` writes `sample: ["sid=.. fleet=.. name=<empire name>"]` (player's custom empire
   name). `evidence.py:270` dumps `ParseResult.save.path` via `god`? No, but `evidence["error"]` (line 262) dumps
   `ParseError.detail` (up to 2000 chars of parser text) and nothing stops `SaveInfo.path` (`c4_parse.py:45`,
   `save games/<empire>/...`) from reaching results in future checks. Fix in X2-1 `pack.py`: after the home
   scrub, JSON-load every `M0-*.json`, drop `evidence.sample`, truncate `evidence.error.detail` to 200 chars,
   and regex `name=[^"]*` → `name=<redacted>` in `notes`; test it. Reword the notice: "empire name redacted".
2. **uv SHA-256 placeholder passes every test.** X2-2 may ship a placeholder; the static test only checks
   `^[0-9a-f]{64}$` and `-DryRun` never downloads, so a wrong hash is first discovered on the field PC.
   Fix in X2-3: one extra pwsh step in `field-kit-windows`: download
   `.../0.13.1/uv-x86_64-pc-windows-msvc.zip.sha256`, compare to the `$UvSha256` line in `setup.ps1`,
   fail the job on mismatch. Also assert in the static test that the value is not all one character.
3. **`sae-parse.exe` may not run on a clean PC.** Rust MSVC target links `vcruntime140.dll` dynamically by
   default; no `crt-static` anywhere in `parser/`. Fix in X2-3: set
   `RUSTFLAGS: -C target-feature=+crt-static` on the kit's cargo build step only (not the `windows` job). Doctor's
   round-trip (D7) then stays as the runtime proof, not the only defence.
4. **doctor results path is ambiguous between §3 step 7 and §4.** Plan says `<kit>\m0_results\doctor.txt`, but
   `--results` defaults to `./m0_results` relative to cwd. Fix in X2-2: `setup.ps1` passes
   `--results "$PSScriptRoot\m0_results"` explicitly; README's `cd <kit>` then matches.

## Required (non-blocking, same batch)

5. `uv tool update-shell` edits the user PATH registry value; `-DryRun` test must also assert
   `[Environment]::GetEnvironmentVariable('Path','User')` is unchanged after the run (one line).
6. `uv tool install --python 3.12` downloads CPython from `github.com/astral-sh/python-build-standalone`;
   name that host in the consent list (D9 says only "uv-managed CPython").
7. Home scrub misses OneDrive folders outside `%USERPROFILE%` (e.g. `D:\OneDrive\...`) and the bare username
   in `--machine` text. Add `%USERNAME%` as a whole-word scrub target → `~`.
8. `SHA256SUMS` is integrity, not authenticity (tampered kit rewrites it). Say so in README; do not call it
   a security check.

## Suggestions

- CI cost: a release cargo build on windows-latest on every `feat/**` push and PR is ~8–12 min. Gate the job
  with `if: github.event_name != 'pull_request'` or add `Swatinem/rust-cache` (pinned by SHA).
- Zip layout of the uv release (uv.exe at root) is UNVERIFIED by any test; label it in `setup.ps1`.
- `constraints.txt` is `--no-hashes`; backlog a hashed export once uv tool install honours it.

## Checked and clean

Settled rule (no launch, no game-folder writes): doctor/pack read-only, static grep + loop test cover it.
D8 single `subprocess.run` guard holds. Contract: C8 §5 confirms no proposal needed. Files disjoint across
X2-1..X2-4. Four required jobs untouched (X2-3 adds one job only). Fairness: no engine path touched.

Bottom line: the design is right-sized and the no-touch rule is enforced by tests; the plan ships only once the
zip stops leaking empire names, the uv hash is verified in CI, and the exe links the CRT statically.

## Plan review response (T8 lead, 2026-10-20)

All 4 blocking items fixed in `docs/plans/X2_field_kit_plan.md` and in the revised work orders; required items
5–8 and two suggestions folded in. Nothing deferred from Blocking or Required.

| # | Item | Resolution | Where |
|---|---|---|---|
| B1 | pack privacy notice false | `pack.py` JSON-redacts each `M0-*.json` after the home scrub: drop `evidence.sample`, truncate `evidence.error.detail` to 200, drop `evidence.save.path`/`evidence.path`, `name=…` → `name=<redacted>` in notes and all strings (also in `.md`/`.txt`/`.jsonl`). Test with M0-5 sample + M0-11 long detail. Notice reworded ("empire names redacted"). | plan §5, §6; WO X2-1 |
| B2 | uv hash placeholder passes tests | CI pwsh step downloads `uv-x86_64-pc-windows-msvc.zip.sha256` (0.13.1), compares to `$UvSha256` parsed from `setup.ps1`, fails on mismatch. Static test also rejects a single repeated character. | plan D11, §6; WO X2-3, X2-2 |
| B3 | `sae-parse.exe` needs VC++ runtime | Kit job's cargo step sets `RUSTFLAGS: -C target-feature=+crt-static`; `windows` job untouched. | plan D10; WO X2-3 |
| B4 | doctor results path ambiguous | `setup.ps1` passes `--kit-dir "$PSScriptRoot" --results "$PSScriptRoot\m0_results"`; static test asserts it. | plan §3 step 7; WO X2-2 |
| R5 | user PATH registry may change in DryRun | DryRun test asserts `[Environment]::GetEnvironmentVariable('Path','User')` unchanged. | §6; WO X2-2 |
| R6 | python-build-standalone host unnamed | Named in D9 and in the consent list. | D9; WO X2-2 |
| R7 | bare user name not scrubbed | `getpass.getuser()` whole-word, case-insensitive → `~`. | §5; WO X2-1 |
| R8 | SHA256SUMS overclaimed | D12; README calls it integrity, not authenticity. | D12; WO X2-4 |
| S1 | CI cost | Kit job `if: github.event_name != 'pull_request'`. rust-cache → backlog. | §8; WO X2-3 |
| S2 | uv zip layout unverified | Labelled UNVERIFIED in `setup.ps1`; `Test-Path` after extract. | §8; WO X2-2 |
| S3 | unhashed constraints | Backlog. | §10 |

UNVERIFIED for T8's M0 checklist: none new (packaging only; A-01, A-33, A-24 unchanged). The uv zip layout is a
third-party tool assumption, not a Stellaris one.
