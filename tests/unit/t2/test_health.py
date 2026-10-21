"""safe_health wrapper (T2)."""

from __future__ import annotations

from sae.adapters.health import safe_health
from sae.contracts.c5_bus import HealthKind


def test_safe_health_swallows_and_passes_through() -> None:
    got: list[HealthKind] = []

    def bad(k: HealthKind, d: str | None) -> None:
        got.append(k)
        raise RuntimeError("x")

    safe_health(bad)(HealthKind.RECONNECT, None)
    assert got == [HealthKind.RECONNECT]
