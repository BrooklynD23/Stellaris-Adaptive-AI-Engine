"""sae.governor (owner: T5): the pure Resource Governor and its factory."""

from __future__ import annotations

import os

from sae.contracts.c7_governor import GovernorConfig, HealthLevel
from sae.contracts.c8_config import BridgeConfig
from sae.governor.core import ResourceGovernor

__all__ = ["ResourceGovernor", "make_governor"]


def make_governor(
    cfg: GovernorConfig,
    min_level: str = "H0",
    preset: str = "balanced",
    *,
    cpu_count: int | None = None,
    emit_period: int | None = None,
) -> ResourceGovernor:
    """Build the Governor. ``cpu_count`` defaults to ``os.cpu_count()``; ``emit_period`` to ``BridgeConfig()``."""
    return ResourceGovernor(
        cfg,
        HealthLevel(min_level),
        preset,
        cpu_count=cpu_count if cpu_count is not None else (os.cpu_count() or 1),
        emit_period=emit_period if emit_period is not None else BridgeConfig().emit_period,
    )
