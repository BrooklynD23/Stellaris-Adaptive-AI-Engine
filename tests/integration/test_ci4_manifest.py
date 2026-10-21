"""CI-4 manifest: ``uv.lock`` holds no GPU package and the runtime closure stays lean."""

from __future__ import annotations

import fnmatch
import tomllib

from ._lock import load_lock, runtime_closure
from .conftest import REPO

GPU_PATTERNS = (
    "torch*", "jax*", "cupy*", "nvidia-*", "pynvml", "nvidia-ml-py", "onnxruntime*", "tensorflow*",
    "pycuda", "vulkan*", "*directml*", "triton",
)


def test_no_gpu_package_in_lock() -> None:
    """No lock entry (extras included) matches a GPU pattern; non-vacuity: the lock is populated."""
    lock = load_lock()
    assert "pydantic" in lock and "rerun-sdk" in lock
    bad = [n for n in lock if any(fnmatch.fnmatch(n, p) for p in GPU_PATTERNS)]
    assert bad == []


# Exact runtime distribution set of `sae` (pydantic, psutil and what they resolve to). Any change fails CI-4 on
# purpose: review the new distribution for GPU code and import cost, then edit this set in the same change
# (Wave 3 finding N5).
EXPECTED_RUNTIME = frozenset({
    "annotated-types", "psutil", "pydantic", "pydantic-core", "typing-extensions", "typing-inspection",
})


def test_runtime_closure_is_pinned() -> None:
    """The runtime closure is exactly EXPECTED_RUNTIME; additions or removals must be deliberate."""
    closure = runtime_closure(load_lock())
    added, removed = sorted(closure - EXPECTED_RUNTIME), sorted(EXPECTED_RUNTIME - closure)
    assert not added and not removed, (
        f"runtime closure changed: added {added}, removed {removed}. Check each added distribution for GPU code "
        "and daemon import cost, then update EXPECTED_RUNTIME in tests/integration/test_ci4_manifest.py."
    )


def test_runtime_closure_is_lean() -> None:
    """Second check: telemetry and console extras never leak into the runtime closure."""
    closure = runtime_closure(load_lock())
    assert not closure & {"rerun-sdk", "duckdb", "polars", "pyarrow"}


def test_pyproject_runtime_deps() -> None:
    """pyproject runtime dependencies are exactly pydantic and psutil."""
    deps = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    names = {d.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip().lower() for d in deps}
    assert names == {"pydantic", "psutil"}
