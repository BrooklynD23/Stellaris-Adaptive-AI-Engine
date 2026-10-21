"""``sae m0 pack``: zip the allowlisted result files with paths, user name and empire names scrubbed (T8).

Reads only ``<results>``; originals are never modified.
"""

from __future__ import annotations

import fnmatch
import getpass
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from sae.m0.results import ENV_FILE, STAGE_FILE, SUMMARY_FILE

_TEXT_FILES = ("M0-4.tail.jsonl", SUMMARY_FILE, "doctor.txt")
_NAME_RE = re.compile(r'name=[^"\n]*')
_SAVE_RE = re.compile(r'(save games[\\/]+)[^\\/"\'\n]+', re.IGNORECASE)
_DETAIL_MAX = 200
MIN_USER_LEN = 3

NOTICE = (
    "This zip holds check verdicts, timings, record types and dates, the 5 numeric player-empire totals from "
    "M0-11/M0-14 with empire names redacted, parser error text cut to 200 chars, and any text you typed in "
    "--note/--value/--playset/--machine, with the home path and user name scrubbed."
)


class PackError(Exception):
    """The results folder is missing or has nothing to pack (exit 2)."""


def _scrubber(home: Path, user: str) -> re.Pattern[str]:
    h = str(home)
    forms = {h, h.replace("\\", "/"), h.replace("/", "\\")}
    parts = [re.escape(f) for f in sorted(forms, key=len, reverse=True) if f]
    if len(user) >= MIN_USER_LEN:
        parts.append(rf"\b{re.escape(user)}\b")
    return re.compile("|".join(parts) or r"(?!x)x", re.IGNORECASE)


def _clean(text: str, scrub: re.Pattern[str]) -> str:
    return _SAVE_RE.sub(r"\1<redacted>", _NAME_RE.sub("name=<redacted>", scrub.sub("~", text)))


def _trim_evidence(ev: dict[str, Any]) -> None:
    for key in ("sample", "save", "path"):
        ev.pop(key, None)
    err = ev.get("error")
    if isinstance(err, str):
        ev["error"] = err[:_DETAIL_MAX]
    elif isinstance(err, dict):
        for key in ("detail", "message"):
            if isinstance(err.get(key), str):
                err[key] = err[key][:_DETAIL_MAX]


def _walk(node: Any, scrub: re.Pattern[str]) -> Any:
    if isinstance(node, str):
        return _clean(node, scrub)
    if isinstance(node, list):
        return [_walk(v, scrub) for v in node]
    if isinstance(node, dict):
        if isinstance(node.get("evidence"), dict):
            _trim_evidence(node["evidence"])
        return {k: _walk(v, scrub) for k, v in node.items()}
    return node


def _redact_json(text: str, scrub: re.Pattern[str]) -> str:
    try:
        data: Any = json.loads(text)
    except ValueError:
        data = {"redacted": "unparseable"}
    else:
        data = _walk(data, scrub)
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def allowlisted(results_dir: Path) -> list[Path]:
    """Top-level files of ``results_dir`` that may be packed."""
    out: list[Path] = []
    for p in sorted(results_dir.iterdir()):
        if not p.is_file() or p.name == STAGE_FILE:
            continue
        if p.name == ENV_FILE or p.name in _TEXT_FILES or fnmatch.fnmatch(p.name, "M0-*.json"):
            out.append(p)
    return out


def _login() -> str:
    try:
        return getpass.getuser()
    except (OSError, KeyError):  # no passwd entry / no env
        return ""


def build_zip(
    results_dir: Path, out: Path, *, home: Path | None = None, user: str | None = None,
) -> list[tuple[str, int]]:
    """Write the scrubbed zip to ``out``; return the manifest as ``(name, bytes)``."""
    if not results_dir.is_dir():
        raise PackError(f"results folder {results_dir} does not exist")
    files = allowlisted(results_dir)
    if not files:
        raise PackError(f"results folder {results_dir} has no result files to pack")
    scrub = _scrubber(home if home is not None else Path.home(), user if user is not None else _login())
    manifest: list[tuple[str, int]] = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            raw = p.read_text(encoding="utf-8", errors="replace")
            text = _redact_json(raw, scrub) if p.suffix == ".json" else _clean(raw, scrub)
            data = text.encode("utf-8")
            zf.writestr(p.name, data)
            manifest.append((p.name, len(data)))
    out.write_bytes(buf.getvalue())
    return manifest


def render_manifest(manifest: list[tuple[str, int]], out: Path) -> str:
    """Manifest table plus the privacy notice."""
    rows = [f"  {name:28} {size:>9} bytes" for name, size in manifest]
    return "\n".join([f"wrote {out}", *rows, "", NOTICE])
