"""Assemble the Windows M0 field kit (stdlib only).

Usage: uv run tools/m0/field_kit/build_kit.py --out DIR --wheel WHL --parser-exe EXE --constraints TXT [--git-sha SHA]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
_WHEEL_RE = re.compile(r"^[A-Za-z0-9_.]+?-(\d[^-]*)-")
_MOD_IGNORE = shutil.ignore_patterns("tests", "tools", "__pycache__", "*.md")


def wheel_version(wheel: Path) -> str:
    """Return the version parsed from a wheel filename; raise ValueError if malformed."""
    m = _WHEEL_RE.match(wheel.name)
    if m is None or wheel.suffix != ".whl":
        raise ValueError(f"not a wheel filename: {wheel.name}")
    return m.group(1)


def _sums(out: Path) -> str:
    lines = []
    for p in sorted(out.rglob("*")):
        rel = p.relative_to(out).as_posix()
        if p.is_file() and rel != "SHA256SUMS":
            lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {rel}")
    return "\n".join(sorted(lines, key=lambda s: s.split("  ", 1)[1])) + "\n"


def build(
    out: Path,
    wheel: Path,
    parser_exe: Path,
    constraints: Path,
    git_sha: str | None = None,
    parser_version: str | None = None,
) -> None:
    """Assemble the kit into ``out`` (must be absent or empty)."""
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"--out is not empty: {out}")
    version = wheel_version(wheel)
    out.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve().parent
    shutil.copyfile(here / "setup.ps1", out / "setup.ps1")
    shutil.copyfile(REPO / "tools/m0/README.md", out / "README.md")
    shutil.copyfile(REPO / "docs/m0_checklist.md", out / "m0_checklist.md")
    shutil.copyfile(REPO / "tools/m0/m0_results_template.md", out / "m0_results_template.md")
    shutil.copyfile(wheel, out / wheel.name)
    shutil.copyfile(constraints, out / "constraints.txt")
    (out / "bin").mkdir()
    shutil.copyfile(parser_exe, out / "bin" / "sae-parse.exe")
    shutil.copytree(REPO / "mod/sae_bridge", out / "mod/sae_bridge", ignore=_MOD_IGNORE)
    info = {
        "git_sha": git_sha,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sae_version": version,
        "parser_version": parser_version,
    }
    (out / "KIT_INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    (out / "SHA256SUMS").write_text(_sums(out), encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    """CLI entry; returns the exit code (2 on a refused or invalid request)."""
    ap = argparse.ArgumentParser(description="Assemble the SAE M0 field kit.")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--wheel", type=Path, required=True)
    ap.add_argument("--parser-exe", type=Path, required=True)
    ap.add_argument("--constraints", type=Path, required=True)
    ap.add_argument("--git-sha", default=None)
    ap.add_argument("--parser-version", default=None)
    a = ap.parse_args(argv)
    try:
        build(a.out, a.wheel, a.parser_exe, a.constraints, a.git_sha, a.parser_version)
    except (FileExistsError, ValueError, OSError) as e:
        print(f"build_kit: {e}", file=sys.stderr)
        return 2
    print(f"kit written to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
