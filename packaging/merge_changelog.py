"""Merge ``changelog.d/<TEAM>.md`` fragments into the ``[Unreleased]`` section of ``CHANGELOG.md`` (T8, Wave 3).

Fragment format (one file per team): ``### <Kind> (<team label>)`` headings, each followed by Markdown bullets.
Kinds follow Keep a Changelog. The merged section groups by kind, then by team (``#### <team label>``).
Fragments are not deleted: the maintainer removes them after approving the diff.

Usage: ``uv run python3 packaging/merge_changelog.py [--check] [--root <repo>]``. ``--check`` prints the new
``CHANGELOG.md`` and writes nothing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

KINDS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
UNRELEASED = "## [Unreleased]"
_HEADING = re.compile(r"^### (\w+) \((.+)\)\s*$")


class FragmentError(ValueError):
    """A fragment breaks the ``### <Kind> (<team>)`` format."""


def parse_fragment(name: str, text: str) -> list[tuple[str, str, str]]:
    """Return ``(kind, team label, body)`` per heading of one fragment."""
    out: list[tuple[str, str, str]] = []
    current: tuple[str, str] | None = None
    body: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("#"):
            match = _HEADING.match(line)
            if match is None or match.group(1) not in KINDS:
                raise FragmentError(f"{name}:{n}: expected '### <{'|'.join(KINDS)}> (<team>)', got {line!r}")
            if current is not None:
                out.append((*current, "\n".join(body).strip()))
            current, body = (match.group(1), match.group(2)), []
        elif current is None and line.strip():
            raise FragmentError(f"{name}:{n}: text before the first heading")
        else:
            body.append(line)
    if current is not None:
        out.append((*current, "\n".join(body).strip()))
    return out


def build_section(fragments: dict[str, str]) -> str:
    """Render the ``[Unreleased]`` body from fragments (sorted by file name)."""
    entries = [e for name in sorted(fragments) for e in parse_fragment(name, fragments[name])]
    lines: list[str] = []
    for kind in KINDS:
        chosen = [(team, body) for k, team, body in entries if k == kind and body]
        if not chosen:
            continue
        lines += [f"### {kind}", ""]
        for team, body in chosen:
            lines += [f"#### {team}", "", body, ""]
    return "\n".join(lines).rstrip() + "\n"


def merge(changelog: str, section: str) -> str:
    """Replace the body of ``[Unreleased]`` (up to the next ``## `` heading) with ``section``."""
    if UNRELEASED not in changelog:
        raise FragmentError(f"CHANGELOG.md has no '{UNRELEASED}' heading")
    head, rest = changelog.split(UNRELEASED, 1)
    nxt = re.search(r"^## ", rest, flags=re.MULTILINE)
    tail = rest[nxt.start():] if nxt else ""
    return f"{head}{UNRELEASED}\n\n{section}" + (f"\n{tail}" if tail else "")


def main(argv: list[str] | None = None) -> int:
    """CLI entry: 0 ok, 2 bad fragment or missing file."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true", help="print the result, write nothing")
    args = parser.parse_args(argv)
    changelog_path = args.root / "CHANGELOG.md"
    try:
        fragments = {p.name: p.read_text(encoding="utf-8") for p in sorted((args.root / "changelog.d").glob("*.md"))}
        result = merge(changelog_path.read_text(encoding="utf-8"), build_section(fragments))
    except (FragmentError, OSError) as exc:
        print(f"merge_changelog: {exc}", file=sys.stderr)
        return 2
    if args.check:
        print(result, end="")
        return 0
    changelog_path.write_text(result, encoding="utf-8")
    print(f"merged {len(fragments)} fragment(s) into {changelog_path}; review the diff, then delete changelog.d/*.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
