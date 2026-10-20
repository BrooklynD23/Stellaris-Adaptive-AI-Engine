"""sae.adapters (owner: T2): live adapter and its factory."""

from __future__ import annotations

from sae.adapters.health import HealthFn, log_health
from sae.adapters.live import LiveAdapter
from sae.adapters.paths import resolve_paths
from sae.contracts.c6_adapter import AdapterMode, GameAdapter, GodSink, SaveParser
from sae.contracts.c8_config import SaeConfig, sae_dir

__all__ = ["LiveAdapter", "make_adapter"]

def make_adapter(
    config: SaeConfig, parser: SaveParser, god_sink: GodSink, *, health: HealthFn | None = None
) -> GameAdapter:
    """Build the live or release adapter (C8 section 5.1); replay/synth are built by the CLI.

    ``health`` is the C6 section 1.8 hook (T4 passes it); ``None`` means log only.
    """
    if config.adapter not in (AdapterMode.LIVE, AdapterMode.RELEASE):
        raise ValueError("use sae replay / sae synth")
    return LiveAdapter(
        resolve_paths(config.paths),
        parser,
        god_sink,
        health=health if health is not None else log_health,
        release=config.adapter is AdapterMode.RELEASE,
        record=config.record,
        sae_dir=sae_dir(config),
    )
