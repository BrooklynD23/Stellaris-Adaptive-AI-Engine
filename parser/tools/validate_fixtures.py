"""Validate every parser fixture against the C4 pydantic contract.

For each ``parser/fixtures/<name>``: validate ``spec.json`` with ``FixtureSpec``, run the debug binary on
``save.sav`` and validate stdout with ``PARSE_OUTPUT_ADAPTER`` (must be an ok ``ParseResult``).

Usage: ``uv run python3 parser/tools/validate_fixtures.py [path/to/sae-parse]``. Exits non-zero on any failure.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sae.contracts.c4_parse import PARSE_OUTPUT_ADAPTER, FixtureSpec, ParseResult

PARSER_DIR = Path(__file__).resolve().parent.parent


def check(binary: Path, fixture: Path) -> list[str]:
    """Return the problems found for one fixture directory (empty when clean)."""
    problems: list[str] = []
    try:
        FixtureSpec.model_validate_json((fixture / "spec.json").read_bytes())
    except (OSError, ValueError) as exc:
        problems.append(f"spec.json: {exc}")
    proc = subprocess.run(
        [str(binary), "parse", "--save", str(fixture / "save.sav")],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        problems.append(f"parse exit {proc.returncode}: {proc.stdout.decode(errors='replace')[:300]}")
        return problems
    try:
        out = PARSE_OUTPUT_ADAPTER.validate_json(proc.stdout)
    except ValueError as exc:
        problems.append(f"stdout does not validate: {exc}")
        return problems
    if not isinstance(out, ParseResult):
        problems.append("stdout is not a ParseResult")
    return problems


def main(argv: list[str]) -> int:
    """Validate all fixtures; return the process exit code."""
    binary = Path(argv[1]) if len(argv) > 1 else PARSER_DIR / "target" / "debug" / "sae-parse"
    if not binary.is_file():
        print(f"binary not found: {binary} (run: cd parser && cargo build -j 1)", file=sys.stderr)
        return 2
    fixtures = sorted(p for p in (PARSER_DIR / "fixtures").iterdir() if p.is_dir())
    if not fixtures:
        print("no fixtures found", file=sys.stderr)
        return 2
    failed = 0
    for fixture in fixtures:
        problems = check(binary, fixture)
        print(f"{'FAIL' if problems else 'ok  '} {fixture.name}")
        for p in problems:
            print(f"     {p}")
        failed += bool(problems)
    print(f"{len(fixtures) - failed}/{len(fixtures)} fixtures valid")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
