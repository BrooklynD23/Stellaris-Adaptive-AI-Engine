"""Run every ``game``-marked test and collect a shareable summary (X3 D5-D7).

Usage: ``uv run python3 tools/game/run_deferred.py --out DIR [--list] [-- <pytest args>]``.

Writes ``DIR/summary.md`` and ``DIR/inputs.json`` (shareable) and keeps everything raw (junit, pytest basetemp
with save/log copies) under ``DIR/private/`` (not shareable). Exit code is pytest's.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
_HEADING = re.compile(r"^#{2,4} +([A-Z][A-Z0-9]*-[A-Za-z0-9.-]+)")
_MSG_MAX = 200
_MIN_SECRET_LEN = 8

Runner = Callable[..., Any]


def deferred_index() -> dict[str, list[str]]:
    """Map each ``docs/handoff/deferred/*.md`` file name to the ids in its headings (D6)."""
    out: dict[str, list[str]] = {}
    for md in sorted((REPO / "docs" / "handoff" / "deferred").glob("*.md")):
        ids = [m.group(1) for ln in md.read_text(encoding="utf-8").splitlines() if (m := _HEADING.match(ln))]
        out[md.name] = ids
    return out


def format_index(index: dict[str, list[str]]) -> str:
    """Render the index as a markdown list grouped by file."""
    if not index:
        return "(no docs/handoff/deferred/*.md files found)\n"
    return "".join(f"- {name}: {', '.join(ids) if ids else '(no ids)'}\n" for name, ids in index.items())


def redactions(environ: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """(value, placeholder) pairs, longest first. Short flag values (``1``, ``24``) are kept (R2)."""
    env = os.environ if environ is None else environ
    base = [
        (v, f"<{k}>")
        for k, v in env.items()
        if (k.startswith("SAE_GAME_") or k == "SAE_PARSER_EXE")
        and v
        and ("/" in v or "\\" in v or len(v) >= _MIN_SECRET_LEN)
    ]
    base.append((str(Path.home()), "<HOME>"))
    # Windows junit messages show paths as reprs: forward slashes or doubled backslashes.
    pairs = [(x, name) for v, name in base for x in {v, v.replace("\\", "/"), v.replace("\\", "\\\\")}]
    return sorted(pairs, key=lambda p: -len(p[0]))


def redact(text: str, pairs: Sequence[tuple[str, str]]) -> str:
    """Replace each secret value by its placeholder."""
    for value, name in pairs:
        text = text.replace(value, name)
    return text


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")[:_MSG_MAX]


def summarize_junit(junit: Path) -> str:
    """Markdown header (counts) and results table from a junit file."""
    counts = {"pass": 0, "fail": 0, "skip": 0, "error": 0}
    rows: list[str] = []
    for tc in ET.parse(junit).getroot().iter("testcase"):
        outcome, msg = "pass", ""
        for tag, key in (("failure", "fail"), ("error", "error"), ("skipped", "skip")):
            el = tc.find(tag)
            if el is not None:
                outcome, msg = key, el.get("message") or (el.text or "")
                break
        counts[outcome] += 1
        did = next((p.get("value", "") for p in tc.iter("property") if p.get("name") == "deferred_id"), "")
        node = f"{tc.get('classname', '')}::{tc.get('name', '')}"
        rows.append(f"| {_cell(did)} | {_cell(node)} | {outcome} | {_cell(msg)} |")
    head = "pass {pass} / fail {fail} / skip {skip} / error {error}".format(**counts)
    table = "| deferred_id | nodeid | outcome | message |\n|---|---|---|---|\n" + "\n".join(rows)
    return f"{head}\n\n{table}\n"


def _parser_version(runner: Runner) -> str | None:
    exe = os.environ.get("SAE_PARSER_EXE")
    if not exe:
        return None
    try:
        r = runner([exe, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable: {type(exc).__name__}"
    return str(r.stdout).strip()


def main(argv: Sequence[str] | None = None, runner: Runner = subprocess.run) -> int:
    """Entry point; returns pytest's exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in args:
        i = args.index("--")
        args, extra = args[:i], args[i + 1 :]
    ap = argparse.ArgumentParser(prog="run_deferred", description=__doc__)
    ap.add_argument("--out", type=Path, help="output dir (required unless --list)")
    ap.add_argument("--list", action="store_true", help="print the deferred index and exit")
    ns = ap.parse_args(args)
    if ns.list:
        sys.stdout.write(format_index(deferred_index()))
        return 0
    if ns.out is None:
        ap.error("--out is required")
    out: Path = ns.out
    private = out / "private"
    (private / "tmp").mkdir(parents=True, exist_ok=True)
    junit = private / "junit.xml"
    cmd = [
        sys.executable, "-m", "pytest", "-m", "game", "-rs", "-p", "no:cacheprovider",
        "--junitxml", str(junit), "--basetemp", str(private / "tmp"), *extra,
    ]  # --basetemp keeps every tmp_path copy of a real save/log inside private/ (R3)
    code = int(runner(cmd, cwd=REPO).returncode)
    if junit.is_file():
        body = summarize_junit(junit)
    else:
        body = f"junit file missing (pytest exit {code}): no results to report\n"
    text = f"# Deferred game test summary\n\n{body}\n## Deferred index\n\n{format_index(deferred_index())}"
    pairs = redactions()
    (out / "summary.md").write_text(redact(text, pairs), encoding="utf-8")
    inputs = {
        "sae_game_vars_set": sorted(k for k in os.environ if k.startswith("SAE_GAME_")),
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "sae_parse_version": _parser_version(runner),
    }
    (out / "inputs.json").write_text(redact(json.dumps(inputs, indent=2), pairs), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
