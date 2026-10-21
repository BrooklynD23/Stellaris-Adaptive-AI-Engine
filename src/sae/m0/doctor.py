"""``sae m0 doctor``: preflight checks for the field kit (T8). Writes only ``<results>/doctor.txt`` and a temp dir."""

from __future__ import annotations

import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from sae.adapters.paths import ResolvedPaths, windows_documents
from sae.contracts.c4_parse import PARSE_OUTPUT_ADAPTER, FixtureCountry, FixtureSpec, ParseResult
from sae.m0 import evidence

GIB = 2**30
DOCTOR_FILE = "doctor.txt"
_VERSION_RE = re.compile(r"^sae-parse \d+\.\d+\.\d+$")
_STELLARIS = Path("Paradox Interactive") / "Stellaris"

Row = tuple[str, str, str]  # (PASS|WARN|FAIL, name, detail)


def check_versions() -> Row:
    """(a) sae and Python versions."""
    try:
        sae = importlib.metadata.version("sae")
    except importlib.metadata.PackageNotFoundError:
        sae = "unknown"
    return "PASS", "versions", f"sae {sae}, python {sys.version.split()[0]}"


def check_results(refusal: str | None, results_dir: Path) -> Row:
    """(b) the results folder is not inside a game folder (``refusal`` is the guard's message, if any)."""
    if refusal is not None:
        return "FAIL", "results", refusal
    return "PASS", "results", str(results_dir)


def check_parser(exe: Path | None) -> Row:
    """(c) sae-parse resolved and (d) answers ``--version``."""
    if exe is None or not exe.is_file():
        return "FAIL", "sae-parse", "not found: pass --parser-exe, or use the bin/ folder of the kit"
    return "PASS", "sae-parse", str(exe)


def check_parser_version(exe: Path) -> Row:
    """(d) ``--version`` exits 0 and prints ``sae-parse X.Y.Z``."""
    try:
        code, out, _err, _wall = evidence.run_exe(exe, ["--version"], 15.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "FAIL", "parser-version", str(exc)[:200]
    if code == 0 and _VERSION_RE.match(out.strip()):
        return "PASS", "parser-version", out.strip()
    return "FAIL", "parser-version", f"rc {code}: {out[:200]!r}"


def _spec_json() -> str:
    spec = FixtureSpec(
        name="doctor", date="2200.01.01",
        countries=(FixtureCountry(country_id=0, name="Doctor Test", sid=1, is_player=True),),
    )
    return spec.model_dump_json()


def check_schema(exe: Path) -> Row:
    """(e) round trip: build a fixture save, parse it, validate the output as C4 (schema_version 1)."""
    name = "c4-schema"
    try:
        with tempfile.TemporaryDirectory(prefix="sae-doctor-") as tmp:
            spec, sav = Path(tmp) / "s.json", Path(tmp) / "s.sav"
            spec.write_text(_spec_json(), encoding="utf-8")
            code, _out, err, _w = evidence.run_exe(exe, ["fixture", "--spec", str(spec), "--out", str(sav)])
            if code != 0:
                return "FAIL", name, f"fixture rc {code}: {err[:200]}"
            code, out, _err, _w = evidence.run_exe(exe, ["parse", "--save", str(sav)])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "FAIL", name, str(exc)[:200]
    try:
        doc = PARSE_OUTPUT_ADAPTER.validate_python(json.loads(out))
    except (ValueError, ValidationError):
        return "FAIL", name, f"rc {code}: {out[:200]}"
    if code == 0 and isinstance(doc, ParseResult) and doc.schema_version == 1:
        return "PASS", name, f"parser {doc.parser_version}, schema_version 1"
    return "FAIL", name, f"rc {code}: {out[:200]}"


def check_user_dir(
    user_dir: Path, *, platform: str = sys.platform, documents: Callable[[], Path] | None = None,
    home: Path | None = None,
) -> list[Row]:
    """(f) the Stellaris user folder exists; on Windows, warn when two candidate folders differ (A-33)."""
    rows: list[Row] = []
    if user_dir.is_dir():
        rows.append(("PASS", "user-dir", str(user_dir)))
    else:
        rows.append(("WARN", "user-dir", f"{user_dir} missing: pass --user-dir (OneDrive moves Documents)"))
    if platform == "win32":
        a = (documents or windows_documents)() / _STELLARIS
        b = (home or Path.home()) / "Documents" / _STELLARIS
        if a.exists() and b.exists() and a != b:
            # UNVERIFIED(A-33): M0 compares the game's real folder with both of these.
            rows.append(("WARN", "user-dir-dual", f"A-33 UNVERIFIED: both {a} and {b} exist; use --user-dir"))
    return rows


def check_mod(kit: Path, user_dir: Path) -> Row:
    """(g) the mod ships in the kit; print the by-hand copy (never performed here)."""
    src = kit / "mod" / "sae_bridge"
    if not (src / "descriptor.mod").exists():
        return "FAIL", "mod", f"{src}/descriptor.mod missing from the kit"
    dest = user_dir / "mod" / "sae_bridge"
    note = f" (note: {dest} already exists)" if dest.exists() else ""
    return "PASS", "mod", f"copy by hand: {src} -> {dest}{note}"


def check_disk(results_dir: Path) -> Row:
    """(h) free space where the results go: >= 5 GiB PASS, 1-5 GiB WARN, < 1 GiB FAIL."""
    probe = results_dir.resolve()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    status = "PASS" if free >= 5 * GIB else "WARN" if free >= GIB else "FAIL"
    return status, "disk", f"{free / GIB:.1f} GiB free at {probe}"


def run_doctor(
    paths: ResolvedPaths, results_dir: Path, kit: Path, refusal: str | None, *,
    platform: str = sys.platform, documents: Callable[[], Path] | None = None, home: Path | None = None,
) -> tuple[str, int]:
    """Run every check; return the report text and the exit code (1 if any FAIL)."""
    rows: list[Row] = [check_versions(), check_results(refusal, results_dir)]
    parser_row = check_parser(paths.parser_exe)
    rows.append(parser_row)
    if parser_row[0] == "PASS" and paths.parser_exe is not None:
        rows += [check_parser_version(paths.parser_exe), check_schema(paths.parser_exe)]
    rows += check_user_dir(paths.user_dir, platform=platform, documents=documents, home=home)
    rows += [check_mod(kit, paths.user_dir), check_disk(results_dir)]
    text = "\n".join(f"{s}  {n}  {d}" for s, n, d in rows)
    if refusal is None:
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / DOCTOR_FILE).write_text(text + "\n", encoding="utf-8")
    return text, 1 if any(s == "FAIL" for s, _n, _d in rows) else 0
