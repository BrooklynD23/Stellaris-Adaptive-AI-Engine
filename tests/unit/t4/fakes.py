"""Fakes for daemon tests (reused by U6): Governor, Sensors, Platform, WorkerHandle, sink, inbox, adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from sae.contracts.c3_intent import IntentBatch
from sae.contracts.c6_adapter import (
    AdapterMode,
    EmitReceipt,
    GameClock,
    GodObservation,
    IntentAck,
    Observation,
)
from sae.contracts.c7_governor import (
    GameState,
    GovernorDecision,
    HealthLevel,
    SensorSample,
    SpmSample,
    TelemetryTier,
    WorkerOutcome,
    WorkerSpec,
)
from sae.contracts.c9_control import ControlResult
from sae.contracts.common import GameDate
from sae.core.state import EventDraft
from sae.daemon.ports import DaemonDeps, InboxItem


def decision(**kw: Any) -> GovernorDecision:
    """A permissive H0 decision with overrides."""
    base: dict[str, Any] = {
        "state": GameState.PLAYING, "level": HealthLevel.H0, "parse_every_n": 1, "parse_window_only": False,
        "worker_threads": 2, "worker_mem_cap_bytes": 1 << 30, "kill_worker": False,
        "telemetry_tier": TelemetryTier.T1, "knob_request": None, "trigger": None, "daemon_should_exit": False,
    }
    base.update(kw)
    return GovernorDecision(**base)


class FakeGovernor:
    """Records calls; ``poll`` returns the scripted decision."""

    def __init__(self, dec: GovernorDecision | None = None) -> None:
        self.dec = dec or decision()
        self.outcomes: list[WorkerOutcome] = []
        self.months: list[SpmSample] = []
        self.boundary: GameDate | None = None
        self.suspended = False

    def poll(self, sample: SensorSample) -> GovernorDecision:
        return self.dec

    def on_month(self, sample: SpmSample) -> None:
        self.months.append(sample)

    def on_worker(self, outcome: WorkerOutcome) -> None:
        self.outcomes.append(outcome)

    def set_next_boundary(self, date: GameDate | None) -> None:
        self.boundary = date

    def suspend(self, suspended: bool) -> None:
        self.suspended = suspended


class FakeSensors:
    """Returns a fixed healthy sample."""

    def __init__(self, rss: int = 50 << 20) -> None:
        self.rss = rss
        self.t = 0.0

    def sample(self) -> SensorSample:
        self.t += 2.0
        return SensorSample(t_mono_s=self.t, available_ram_bytes=4 << 30, game_running=True,
                            daemon_rss_bytes=self.rss, daemon_cpu_pct=1.0)


class FakeWorker:
    """Worker that returns canned output, optionally hanging until killed."""

    def __init__(self, code: int | None, out: bytes = b"", hang: bool = False) -> None:
        self.code, self.out, self.hang = code, out, hang
        self.killed = False
        self._ev = asyncio.Event()

    async def wait(self) -> tuple[int | None, bytes, bytes]:
        if self.hang:
            await self._ev.wait()
            return None, b"", b""
        return self.code, self.out, b""

    def kill(self) -> None:
        self.killed = True
        self._ev.set()


class FakePlatform:
    """Hands out queued workers and records specs."""

    def __init__(self, *workers: FakeWorker) -> None:
        self.workers = list(workers)
        self.specs: list[WorkerSpec] = []

    lowered = 0

    def lower_self_priority(self) -> None:
        self.lowered += 1

    async def spawn_worker(self, spec: WorkerSpec) -> FakeWorker:
        self.specs.append(spec)
        return self.workers.pop(0)


class FakeSink:
    """Collects drafts and god observations."""

    def __init__(self) -> None:
        self.drafts: list[EventDraft] = []
        self.god: list[GodObservation] = []
        self.flushed = 0
        self.closed = False

    def emit(self, draft: EventDraft) -> None:
        self.drafts.append(draft)

    def write_god(self, obs: GodObservation) -> None:
        self.god.append(obs)

    def flush(self) -> None:
        self.flushed += 1

    def close(self) -> None:
        self.closed = True


class FakeInbox:
    """Scripted inbox items; records completions."""

    def __init__(self, *items: InboxItem) -> None:
        self.items = list(items)
        self.done: list[tuple[InboxItem, ControlResult]] = []

    def poll(self) -> Sequence[InboxItem]:
        out, self.items = self.items, []
        return out

    def complete(self, item: InboxItem, result: ControlResult) -> None:
        self.done.append((item, result))


class FakeAdapter:
    """Scripted adapter: yields given observations then ends."""

    mode = AdapterMode.SYNTH

    def __init__(self, obs: Sequence[Observation] = (), acks: Sequence[IntentAck] = ()) -> None:
        self._obs, self._acks = list(obs), list(acks)
        self.batches: list[IntentBatch] = []
        self.closed = False

    async def observations(self) -> AsyncIterator[Observation]:
        for o in self._obs:
            yield o

    async def acks(self) -> AsyncIterator[IntentAck]:
        for a in self._acks:
            yield a

    async def emit(self, batch: IntentBatch) -> EmitReceipt:
        self.batches.append(batch)
        return EmitReceipt(seq=batch.seq, ok=True)

    def clock(self) -> GameClock:
        return GameClock(game_date=None, m=None, session_epoch=0)

    async def close(self) -> None:
        self.closed = True


def make_deps(platform: FakePlatform, governor: FakeGovernor | None = None, sink: FakeSink | None = None,
              inbox: FakeInbox | None = None) -> DaemonDeps:
    """Assemble DaemonDeps from fakes."""
    return DaemonDeps(
        sink=sink or FakeSink(), governor=governor or FakeGovernor(), sensors=FakeSensors(), platform=platform,
        inbox=inbox or FakeInbox(), parser_exe="sae-parse", monotonic=lambda: 0.0, reexec=lambda: None,
    )
