"""Import boundary, C6 god-name grep, GPU-free, and the 120-year RSS budget."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "sae"
FORBIDDEN_PKGS = ("adapters", "bus", "governor", "platform", "control", "telemetry")
GPU = ("torch", "jax", "cupy", "pynvml", "duckdb", "polars")
MODULES = sorted(
    ".".join(p.relative_to(ROOT / "src").with_suffix("").parts).removesuffix(".__init__")
    for pkg in ("core", "daemon") for p in (SRC / pkg).glob("*.py")
)

# Explicit allowed third-party top-level modules for the daemon and core (pydantic's transitive deps listed).
ALLOWED_THIRD_PARTY = frozenset({
    "sae", "pydantic", "pydantic_core", "typing_extensions", "annotated_types", "typing_inspection",
    "psutil", "numpy",
})
# Interpreter/environment artefacts, not imports of ours.
# _virtualenv: venv bootstrap loaded from _virtualenv.pth by uv/virtualenv venvs, not a project import.
ENV_NOISE = frozenset({"__main__", "_virtualenv", "sitecustomize", "usercustomize"})

_PROBE = """
import importlib, sys
for m in {mods!r}:
    importlib.import_module(m)
bad = [m for m in sys.modules if any(m == 'sae.' + p or m.startswith('sae.' + p + '.') for p in {pk!r})]
bad += [m for m in sys.modules if m.split('.')[0] in {gpu!r}]
allowed = set({allowed!r}) | set(sys.stdlib_module_names) | set(sys.builtin_module_names) | set({noise!r})
bad += ['top:' + t for t in {{m.split('.')[0] for m in sys.modules}} - allowed if not t.startswith('_sysconfigdata')]
print(','.join(sorted(bad)))
"""

_RSS = """
import asyncio, sys, tempfile
from pathlib import Path
import psutil
from sae.contracts.c8_config import SaeConfig
from sae.contracts.common import Archetype
from sae.daemon import run_engine
from tests.unit.t4.fake_synth import Sim
from tests.unit.t4.fakes import FakeAdapter, FakePlatform, make_deps

proc = psutil.Process()
base = proc.memory_info().rss
sim = Sim(Archetype.BALANCED, 7)
obs = []
for _ in range(120 * 12):
    obs.append(sim.obs("log"))
    if sim.t % 12 == 6:
        sim.views, sim.as_of = sim._views(), f"{2200 + sim.t // 12}.07.01"
        obs.append(sim.obs("save"))
    sim.t += 1
with tempfile.TemporaryDirectory() as d:
    code = asyncio.run(run_engine(SaeConfig(), FakeAdapter(obs), "r", make_deps(FakePlatform()), Path(d), None,
                                  poll_s=0.5))
print(code, proc.memory_info().rss - base)
"""


def test_modules_found() -> None:
    assert "sae.daemon.loop" in MODULES and "sae.core.engine" in MODULES


def test_no_forbidden_imports_at_import_time() -> None:
    out = subprocess.run(
        [sys.executable, "-c", _PROBE.format(mods=MODULES, pk=FORBIDDEN_PKGS, gpu=GPU,
                                  allowed=sorted(ALLOWED_THIRD_PARTY), noise=sorted(ENV_NOISE))],
        capture_output=True, text=True, check=True, cwd=ROOT,
    )
    assert out.stdout.strip() == ""


def test_no_god_names_in_core_or_daemon() -> None:
    pat = re.compile(r"GodView|GodObservation|PlayerRecord|\.god\b|bus\.god")
    hits = [f"{p}:{i}" for pkg in ("core", "daemon") for p in (SRC / pkg).glob("*.py")
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if pat.search(line)]
    assert hits == []


def test_rss_growth_120_year_run_under_150_mib() -> None:
    pytest.importorskip("psutil")
    out = subprocess.run([sys.executable, "-c", _RSS], capture_output=True, text=True, check=True, cwd=ROOT)
    code, growth = out.stdout.split()[-2:]
    assert code == "0"
    assert int(growth) <= 150 * 1024 * 1024
