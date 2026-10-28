"""Build a replay session dir from a real ``game.log`` and an autosave folder (X3 D4).

Usage: ``make_recording.py --log FILE --saves DIR --out DIR [--parser-exe EXE]``. ``--out`` must be new or empty
(else exit 2). Output layout is the one documented in ``sae.adapters.replay``. Labels are left to X4.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from sae.adapters.paths import resolve_paths
from sae.adapters.recording import RecordGate
from sae.contracts.c4_parse import PARSE_OUTPUT_ADAPTER, ParseResult
from sae.contracts.c8_config import PathsConfig, RecordConfig

_log = logging.getLogger("make_recording")
_DATE = re.compile(r"SAE\|v\d+\|[A-Z]+\|(\d{4}\.\d{2}\.\d{2})")
_PREFIX = re.compile(r"^\d{12}-")
_PARSE_TIMEOUT_S = 120


def last_segment(lines: list[str]) -> list[str]:
    """SAE lines (prefix before ``SAE|`` dropped) from the last HELLO onwards."""
    sae = [ln[ln.find("SAE|") :] for ln in lines if "SAE|" in ln]
    hellos = [i for i, ln in enumerate(sae) if "|HELLO|" in ln]
    return sae[hellos[-1] :] if hellos else sae


def _date(line: str) -> str | None:
    m = _DATE.match(line)
    return m.group(1) if m else None


def save_date(exe: str, save: Path) -> str:
    """``meta.date`` of ``save`` via ``<exe> parse --save X --sections meta``; raises ``ValueError`` on failure."""
    r = subprocess.run(
        [exe, "parse", "--save", str(save), "--sections", "meta"],
        capture_output=True, text=True, timeout=_PARSE_TIMEOUT_S, check=False,
    )
    out = PARSE_OUTPUT_ADAPTER.validate_json(r.stdout)
    if not isinstance(out, ParseResult) or r.returncode != 0:
        raise ValueError(f"{save.name}: parse failed (exit {r.returncode})")
    return str(out.meta.date)


def build(log: Path, saves: Path, out: Path, exe: str) -> int:
    """Write the session dir; returns the number of saves placed."""
    raw = log.read_bytes().decode("utf-8", errors="replace").splitlines()
    gate = RecordGate(RecordConfig(), lambda kind, detail: _log.warning("record gate %s: %s", kind, detail))
    lines = gate.lines(last_segment(raw))
    dates = [d for ln in lines if (d := _date(ln)) is not None]
    if dates != sorted(dates):
        print("warning: game dates are not monotonic (reload?); using the last HELLO segment", file=sys.stderr)
    data = [(ln + "\n").encode("utf-8") for ln in lines]
    ends: list[int] = []  # byte size after each line
    total = 0
    for b in data:
        total += len(b)
        ends.append(total)
    # month boundaries: size after the last line of each (year, month)
    ts: list[int] = []
    for i, ln in enumerate(lines):
        d = _date(ln)
        nxt = _date(lines[i + 1]) if i + 1 < len(lines) else None
        if d is not None and (nxt is None or nxt[:7] != d[:7]) and (not ts or ends[i] > ts[-1]):
            ts.append(ends[i])
    if not ts or ts[-1] != total:
        ts.append(total)
    plan: list[tuple[int, Path]] = []
    for sav in sorted(saves.glob("*.sav")):
        sd = save_date(exe, sav)
        # ponytail: offset = after the last line dated <= meta.date; live poll order may differ (UNVERIFIED X3-U1),
        # upgrade = record live via record_dir in make_adapter
        off = max((ends[i] for i, ln in enumerate(lines) if (d := _date(ln)) is not None and d <= sd), default=0)
        plan.append((off, sav))
    plan.sort(key=lambda p: (p[0], p[1].name))
    out.mkdir(parents=True, exist_ok=True)
    (out / "game.log").write_bytes(b"".join(data))
    stamps = "".join(f"{o} {(k + 1) * 1_000_000_000}\n" for k, o in enumerate(ts))
    (out / "game.log.ts").write_text(stamps, encoding="utf-8")
    (out / "saves").mkdir()
    for off, sav in plan:
        shutil.copy2(sav, out / "saves" / f"{off:012d}-{_PREFIX.sub('', sav.stem)}.sav")
    return len(plan)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: 0 ok, 1 failure, 2 usage."""
    ap = argparse.ArgumentParser(prog="make_recording", description=__doc__)
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--saves", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--parser-exe", default=None)
    ns = ap.parse_args(argv)
    if ns.out.exists() and (not ns.out.is_dir() or any(ns.out.iterdir())):
        print(f"error: --out {ns.out} must be new or empty", file=sys.stderr)
        return 2
    if not ns.log.is_file() or not ns.saves.is_dir():
        print("error: --log must be a file and --saves a directory", file=sys.stderr)
        return 2
    exe = ns.parser_exe or os.environ.get("SAE_PARSER_EXE")
    if not exe:
        found = resolve_paths(PathsConfig()).parser_exe
        exe = str(found) if found else None
    if not exe:
        print("error: no sae-parse found (--parser-exe, SAE_PARSER_EXE, PATH or parser/target)", file=sys.stderr)
        return 2
    try:
        n = build(ns.log, ns.saves, ns.out, exe)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"recording written to {ns.out} ({n} saves)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
