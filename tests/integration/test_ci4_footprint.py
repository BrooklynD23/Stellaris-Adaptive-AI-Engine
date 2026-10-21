"""CI-4 footprint: peak RSS of a 5-year synth run stays within the 150 MiB budget."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from ._run import run_child

MIB = 1024 * 1024
LIMIT = 150 * MIB
GPU_LIBS = ("libcuda", "libnvidia", "libvulkan", "libd3d12", "dxcore", "libOpenCL")


@pytest.fixture(scope="module")
def child(parser_exe: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One fresh-process 5-year synth run shared by both tests."""
    return run_child(5, tmp_path_factory.mktemp("ci4"), parser_exe)


def test_peak_rss_and_health(child: tuple[dict[str, Any], list[dict[str, Any]]]) -> None:
    """Peak RSS (fresh process, peak not steady) and every bus ``daemon_rss_bytes`` are <= 150 MiB."""
    facts, events = child
    print(f"peak RSS: {facts['peak'] / MIB:.1f} MiB")
    assert facts["peak"] <= LIMIT
    rss = [
        e["payload"]["metrics"]["daemon_rss_bytes"]
        for e in events
        if e["type"] == "health" and "daemon_rss_bytes" in (e["payload"].get("metrics") or {})
    ]
    assert len(rss) >= 50  # non-vacuity
    assert max(rss) <= LIMIT


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc/self/maps is Linux only")
def test_no_gpu_library_mapped(child: tuple[dict[str, Any], list[dict[str, Any]]]) -> None:
    """No GPU library is mapped at exit. Cheap and near-vacuous: it only sees libraries already loaded."""
    facts, events = child
    assert facts["maps"]  # the maps file was really read
    assert not [lib for lib in GPU_LIBS if lib in facts["maps"]]
    assert len(events) >= 50
