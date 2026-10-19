"""Pure planning function of ``bench/balloon.py`` (no allocation)."""

from __future__ import annotations

import importlib.util
import sys

import pytest

from .conftest import REPO

_spec = importlib.util.spec_from_file_location("balloon", REPO / "bench" / "balloon.py")
assert _spec is not None and _spec.loader is not None
balloon = importlib.util.module_from_spec(_spec)
sys.modules["balloon"] = balloon
_spec.loader.exec_module(balloon)

GB = 1024**3


def test_plan_chunks() -> None:
    """8 GB available, leave 1.2 GB -> enough 64 MiB chunks to pass the target."""
    p = balloon.plan(8 * GB, 1.2)
    assert p.chunks * 64 * 1024 * 1024 >= 8 * GB - p.leave_bytes
    assert (p.chunks - 1) * 64 * 1024 * 1024 < 8 * GB - p.leave_bytes


def test_plan_already_low() -> None:
    """Nothing to allocate when already below target."""
    assert balloon.plan(GB, 1.2).chunks == 0


def test_keep_going_guard() -> None:
    """Stops at the planned chunk count, at the target, and below the hard floor."""
    leave = int(1.2 * GB)
    assert balloon.keep_going(0, 3, 4 * GB, leave)
    assert not balloon.keep_going(3, 3, 4 * GB, leave)
    assert not balloon.keep_going(0, 3, leave, leave)
    assert not balloon.keep_going(0, 3, int(0.7 * GB), int(0.5 * GB))


def test_plan_refuses_below_floor() -> None:
    """Targets under 0.8 GB are refused."""
    with pytest.raises(ValueError):
        balloon.plan(8 * GB, 0.5)
    assert balloon.main(["--leave-gb", "0.5", "--dry-run"]) == 2
