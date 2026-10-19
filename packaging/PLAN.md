# Packaging plan

Status: plan only. Nothing is built or published yet; the mod descriptor and save layout are UNVERIFIED until M0.

## 1. Artefacts

| Artefact | Built from | Format | Built by |
|---|---|---|---|
| `sae_bridge-<ver>.zip` | `mod/sae_bridge/` minus `tests/`, `tools/`, `*.md` except a short `README` | zip whose root is `sae_bridge/` plus `sae_bridge.mod` (`path="mod/sae_bridge"`) | release script (after M0-2 confirms the descriptor) |
| `sae-<ver>-py3-none-any.whl` | `src/sae/` (hatchling, `pyproject.toml`) | wheel; console script `sae` | `uv build` |
| `sae-parse-<ver>-x86_64-pc-windows-msvc.zip` | `parser/` | `sae-parse.exe` + `LICENSE` | `cargo build --release -j 1` on Windows (MSVC) |
| `sae-parse-<ver>-x86_64-unknown-linux-gnu.tar.gz` | `parser/` | `sae-parse` | `cargo build --release -j 1` on Linux (dev, Proton users) |

One GitHub release per version carries all four plus `SHA256SUMS`.

## 2. Dependencies

- Runtime: `pydantic`, `psutil` only (`pyproject.toml`). No GPU packages, ever through M4 (settled); T7's CI-4
  scans the lock file for GPU packages.
- Optional extras (proposal 010-T6, accepted): `telemetry` = `rerun-sdk==0.39.0` for the third process;
  `console` = `duckdb>=1.1,<2` for offline analysis. The daemon never imports either.
- `sae-parse` is a static-ish Rust binary (jomini, zip); no runtime besides the MSVC CRT on Windows.

## 3. Install layout (player)

1. `pipx install sae-<ver>-py3-none-any.whl` (or `uv tool install`), giving `sae` on `PATH`.
2. Unzip `sae-parse` somewhere on `PATH`, or set `paths.parser_exe`.
3. Unzip the mod into the Stellaris user `mod/` folder **by hand** (SAE never writes there).
4. `sae daemon` (preset `balanced`).

## 4. Versioning

- Python package and mod share the minor version (`0.1.x`); the mod's HELLO `mod=` field reports it.
- `sae-parse --version` is independent; C4 `schema_version` must match the daemon.
- Contract wire versions (`SAE|v1|`, `sae_p_schema = 1`) only change with a contract major (contracts README §3).

## 5. Release checklist

1. M0 results recorded (`m0_results.md`) and go/no-go decided.
2. All tests green (`uv run pytest -x -q`, mypy, ruff, cargo clippy/test); mod checker green.
3. Merge `changelog.d/` with `packaging/merge_changelog.py`; set the version and date in `CHANGELOG.md`.
4. Build the four artefacts; record SHA-256.
5. Maintainer approves the diff before any git tag or push (`CLAUDE.md`).
6. Release gates R-1..R-6 (T7) for any release beyond a developer preview.

## 6. Backlog

Steam Workshop upload (after M3, mod page text with the honest pitch); signed Windows binary; a mod zip build
script once M0-2 confirms the descriptor and `path` form.
