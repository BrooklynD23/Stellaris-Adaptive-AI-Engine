"""CI-4 imports: the game-free loop pulls in only stdlib plus the runtime closure of ``sae``."""

from __future__ import annotations

import importlib.metadata
import sys
from pathlib import Path

from ._lock import load_lock, runtime_closure
from ._run import run_child

FORBIDDEN_TOP = {
    "torch", "jax", "jaxlib", "cupy", "rerun", "rerun_bindings", "duckdb", "polars", "pyarrow",
    "pynvml", "onnxruntime", "tensorflow",
}
FORBIDDEN_SAE = {"sae.telemetry", "sae.bench", "sae.m0", "sae.profile", "sae.tune"}


def _allowed() -> set[str]:
    dist_to_imports = importlib.metadata.packages_distributions()
    imports_of: dict[str, set[str]] = {}
    for imp, dists in dist_to_imports.items():
        for d in dists:
            imports_of.setdefault(d.lower().replace("_", "-"), set()).add(imp)
    out: set[str] = set(sys.stdlib_module_names) | set(sys.builtin_module_names) | {"sae"}
    for dist in runtime_closure(load_lock()):
        out |= imports_of.get(dist, set())
    return out


def test_game_free_loop_imports(parser_exe: Path, tmp_path: Path) -> None:
    """Every top-level module is allowed; forbidden ones are absent; the loop really ran."""
    facts, _ = run_child(2, tmp_path, parser_exe)
    mods: list[str] = facts["modules"]
    allowed = _allowed()
    # Underscore names are stdlib C accelerators or private helpers (e.g. _thread, _json, _pydantic_core
    # internals, __main__); public non-stdlib names must still be in `allowed`.
    # sitecustomize: injected by the host environment (WSL/venv hook), not by sae.
    top = {m.split(".")[0] for m in mods}
    unexpected = sorted(t for t in top if t not in allowed and not t.startswith("_") and t != "sitecustomize")
    assert unexpected == []
    assert not top & FORBIDDEN_TOP
    assert not FORBIDDEN_SAE & set(mods)
    for needed in ("sae.daemon.loop", "sae.adapters", "sae.governor"):  # non-vacuity
        assert needed in mods
    print("sae modules:", sorted(m for m in mods if m.startswith("sae")))
    print("non-stdlib top-level:", sorted(top - set(sys.stdlib_module_names) - set(sys.builtin_module_names)))
