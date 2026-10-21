"""make_adapter and daemon wiring smoke (T2 U4)."""

from __future__ import annotations

from typing import Any

import pytest

from sae.adapters import LiveAdapter, make_adapter
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import AdapterMode, GameAdapter, GodObservation
from sae.contracts.c8_config import SaeConfig


class Sink:
    def __init__(self) -> None:
        self.drafts: list[Any] = []

    def write_god(self, obs: GodObservation) -> None:  # pragma: no cover - unused
        pass

    def emit(self, draft: Any) -> None:
        self.drafts.append(draft)


class Parser:
    async def parse(  # pragma: no cover - unused
        self, save_path: str, *, sections: tuple[str, ...] | None = None
    ) -> None:
        return None


def _cfg(mode: AdapterMode) -> SaeConfig:
    return SaeConfig(adapter=mode)


def test_live_and_release() -> None:
    live = make_adapter(_cfg(AdapterMode.LIVE), Parser(), Sink())
    rel = make_adapter(_cfg(AdapterMode.RELEASE), Parser(), Sink())
    assert isinstance(live, LiveAdapter) and live.mode is AdapterMode.LIVE
    assert isinstance(rel, LiveAdapter) and rel.mode is AdapterMode.RELEASE


@pytest.mark.parametrize("mode", [AdapterMode.REPLAY, AdapterMode.SYNTH])
def test_other_modes_rejected(mode: AdapterMode) -> None:
    with pytest.raises(ValueError, match="sae replay / sae synth"):
        make_adapter(_cfg(mode), Parser(), Sink())


def test_health_none_logs_only() -> None:
    sink = Sink()
    a = make_adapter(_cfg(AdapterMode.LIVE), Parser(), sink)
    assert isinstance(a, LiveAdapter)
    a._health(HealthKind.RECONNECT, "x")  # C6 section 1.8: no bus event without a hook
    assert sink.drafts == []


def test_health_is_keyword_only() -> None:
    with pytest.raises(TypeError):
        make_adapter(_cfg(AdapterMode.LIVE), Parser(), Sink(), lambda k, d: None)  # type: ignore[misc]


def test_explicit_health_wins() -> None:
    got: list[HealthKind] = []
    a = make_adapter(_cfg(AdapterMode.LIVE), Parser(), Sink(), health=lambda k, d: got.append(k))
    assert isinstance(a, LiveAdapter)
    a._health(HealthKind.ACK_ALARM, None)
    assert got == [HealthKind.ACK_ALARM]


def test_wiring_smoke() -> None:
    wiring = pytest.importorskip("sae.daemon.wiring", exc_type=ImportError, reason="sae.daemon/core mid-change (T4)")
    live_adapter_factory = wiring.live_adapter_factory
    a = live_adapter_factory(_cfg(AdapterMode.LIVE))(Parser(), Sink())
    assert isinstance(a, GameAdapter)
