"""C6 v1.1 section 1.7 (replay/synth lockstep, proposal 011) and section 1.8 (adapter health hook, proposal 009)."""

from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from sae.contracts.c5_bus import HealthKind, HealthPayload, IntentAckPayload
from sae.contracts.c6_adapter import AdapterMode, GodSink, IntentAck, Observation, SaveParser
from sae.contracts.c8_config import SaeConfig
from sae.contracts.common import AckCode, AckStatus
from sae.daemon import run_engine, wiring

from .fake_synth import date_for
from .fakes import FakePlatform, FakeSink, make_deps
from .test_loop import CFG, FAST, EchoAdapter, run

HANDOFF_STEPS = 3  # mirrors the adapter obligation: hand pulse ACKs to the waiting consumer first


class PulseAdapter(EchoAdapter):
    """Zero-delay synth adapter that acks on the next pulse (like T2's echo model) and checks lockstep."""

    def __init__(self, months: int, sink: FakeSink, health: Callable[[HealthKind, str | None], None] | None) -> None:
        super().__init__(months, delay=0.0)
        self.sink, self.health = sink, health
        self.pending: list[IntentAck] = []
        self.yielded = 0
        self.violations: list[int] = []

    async def observations(self) -> AsyncIterator[Observation]:
        inner = super().observations()
        async for o in inner:
            for ack in self.pending:  # this pulse's ACKs first, then a handoff, then the observation
                self.acks_q.put_nowait(ack)
            self.pending.clear()
            for _ in range(HANDOFF_STEPS):
                await asyncio.sleep(0)
            if self.yielded == 1 and self.health is not None:
                self.health(HealthKind.LOG_PARSE_ERROR, "line 7: bad field")
            yield o
            self.yielded += 1
            seen = sum(1 for d in self.sink.drafts if d.type == "observation")
            if seen != self.yielded:  # pulled again before the previous observation was handled
                self.violations.append(self.yielded)

    async def emit(self, batch: Any) -> Any:
        receipt = await super().emit(batch)
        while not self.acks_q.empty():  # defer: the echo model answers on the next pulse
            self.pending.append(self.acks_q.get_nowait())
        return receipt


def _go(months: int, tmp: Path) -> tuple[PulseAdapter, FakeSink]:
    deps = make_deps(FakePlatform())
    sink: FakeSink = deps.sink  # type: ignore[assignment]
    built: list[PulseAdapter] = []

    def factory(parser: SaveParser, god: GodSink, *, health: Any = None) -> PulseAdapter:
        built.append(PulseAdapter(months, sink, health))
        return built[0]

    assert run(run_engine(CFG, factory, "r1", deps, tmp, None, **FAST)) == 0
    return built[0], sink


def test_lockstep_echo_acks_handled_before_next_observation(tmp_path: Path) -> None:
    ad, sink = _go(30, tmp_path)
    assert ad.mode is AdapterMode.SYNTH and ad.violations == []
    assert ad.batches, "the run must emit at least one batch"
    seqs = {b.seq for b in ad.batches}
    acked = {d.payload.seq for d in sink.drafts if d.type == "intent_ack"  # type: ignore[attr-defined]
             and isinstance(d.payload, IntentAckPayload) and d.payload.status is AckStatus.APPLIED}
    # Every batch except one emitted on the final pulse is APPLIED via the real loop.
    assert len(seqs - acked) <= 1
    order = [d.type for d in sink.drafts]
    for i, d in enumerate(sink.drafts):  # each ACK lands before the next observation after its emit
        if d.type == "intent_ack":
            assert "observation" in order[:i]
    assert not any(d.type == "intent_ack" and d.payload.code is AckCode.TIMEOUT  # type: ignore[attr-defined]
                   for d in sink.drafts)


def test_health_hook_passed_and_stamped(tmp_path: Path) -> None:
    _, sink = _go(3, tmp_path)
    hs = [d for d in sink.drafts if d.type == "health" and d.payload.component == "adapter"]  # type: ignore[attr-defined]
    assert len(hs) == 1
    p = hs[0].payload
    assert isinstance(p, HealthPayload) and p.kind is HealthKind.LOG_PARSE_ERROR and p.detail == "line 7: bad field"
    assert hs[0].game_date == date_for(1)


def test_factory_without_health_keyword_still_works(tmp_path: Path) -> None:
    deps = make_deps(FakePlatform())
    assert run(run_engine(CFG, lambda p, g: EchoAdapter(2, delay=0.0), "r1", deps, tmp_path, None, **FAST)) == 0


def test_live_adapter_factory_passes_health(monkeypatch: pytest.MonkeyPatch) -> None:
    got: dict[str, Any] = {}

    def make_adapter(config: SaeConfig, parser: Any, god: Any, *, health: Any = None) -> str:
        got["health"] = health
        return "adapter"

    m = types.ModuleType("sae.adapters")
    m.make_adapter = make_adapter  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sae.adapters", m)
    hook = lambda kind, detail: None  # noqa: E731
    assert wiring.live_adapter_factory(SaeConfig())(None, None, health=hook) == "adapter"  # type: ignore[arg-type,call-arg]
    assert got["health"] is hook
