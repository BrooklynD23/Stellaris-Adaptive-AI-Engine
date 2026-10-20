"""The daemon hot path must never import rerun/duckdb/pyarrow/polars/torch or sae.telemetry."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).parents[3] / "src" / "sae"
_MODS = ["sae.bus", "sae.control", "sae.control.cli", "sae.daemon.wiring", "sae.daemon.loop",
         "sae.daemon.cli", "sae.cli"]
_FORBIDDEN = {"rerun", "duckdb", "pyarrow", "polars", "torch"}


@pytest.mark.parametrize("mod", ["sys", *_MODS])
def test_no_heavy_imports(mod: str) -> None:
    """One interpreter per module: a Windows start-up failure (0xC0000142) names the import; ``sys`` is control."""
    code = (f"import sys\nimport {mod}\n"
            + "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
            + repr(_FORBIDDEN) + " or m == 'sae.telemetry' or m.startswith('sae.telemetry.'))\n"
            + "print(','.join(bad))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False,
                         stdin=subprocess.DEVNULL)
    assert out.returncode == 0, f"rc={out.returncode:#x} stderr={out.stderr}"
    assert out.stdout.strip() == ""


_IMPORT = re.compile(r"^\s*(from|import)\s+(rerun|duckdb)\b", re.M)


def _hits(sub: str) -> list[str]:
    return [p.relative_to(_SRC).as_posix() for p in (_SRC / sub).rglob("*.py")
            if _IMPORT.search(p.read_text(encoding="utf-8"))]


def test_sources_clean() -> None:
    for sub in ("bus", "control"):
        assert _hits(sub) == []


def test_rerun_only_in_stream_and_blueprint() -> None:
    rx = re.compile(r"^\s*(from|import)\s+rerun\b", re.M)
    hits = {p.relative_to(_SRC).as_posix() for p in _SRC.rglob("*.py") if rx.search(p.read_text(encoding="utf-8"))}
    assert hits <= {"telemetry/stream.py", "telemetry/blueprint.py"}
