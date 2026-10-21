# 014-T2 — `parser_exe` default also finds the repo debug build

Status: ACCEPTED-WITH-CHANGES (2026-10-19, architect; see CHANGES.md). Author: T2 lead. Owner: architect. Consumers: T4 (daemon parser), T7. Breaking: no.

## Change (C8 §3 `parser_exe` bullet)
"`parser_exe` = `sae-parse` on `PATH`, else `parser/target/release/sae-parse[.exe]`, else
`parser/target/debug/sae-parse[.exe]`, relative to the repo root." `sae replay`/`sae synth` resolve it once and write
the resolved path into the run's `paths.parser_exe` when the user left it `null`, so the daemon's governed parser
runs the same binary (today `wiring.default_deps` falls back to bare `sae-parse` on `PATH`).

## Why
Dev machines build with `cargo build -j 1` (debug); `sae synth` failed with "sae-parse not found" although the
binary existed. Implemented in `src/sae/adapters/paths.py` and `cli.py`, tested.
