"""Filled ``EngineConfig`` for core tests: C8 v1.3 defaults rho/clip/threshold to null, core needs numbers."""

from __future__ import annotations

from typing import Any

from sae.contracts.c8_config import EngineConfig


def eff(**kw: Any) -> EngineConfig:
    """``EngineConfig`` with the built-in detector numbers filled (what the daemon passes to core)."""
    return EngineConfig(**{"rho": 0.02, "likelihood_clip": 3.0, "belief_threshold": 0.45, **kw})
