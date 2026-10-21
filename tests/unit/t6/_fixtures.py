"""Shared helpers for T6 bus tests: drafts built from the C5 doc examples, deterministic clocks."""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sae.contracts.c5_bus import BUS_EVENT_ADAPTER, HealthEvent
from sae.contracts.c6_adapter import AdapterMode
from sae.contracts.c8_config import PresetName, SaeConfig
from sae.core.state import EventDraft

RUN_ID = "20261019-031500-synth-0a1b"
_DOC = Path(__file__).parents[3] / "contracts" / "C5_bus.md"


def cfg(preset: PresetName = "balanced") -> SaeConfig:
    """A config with the given preset and the synth adapter."""
    return SaeConfig(preset=preset, adapter=AdapterMode.SYNTH)


def clocks() -> tuple[Callable[[], datetime], Callable[[], int]]:
    """Deterministic (wall, mono) callables."""
    n = itertools.count()
    base = datetime(2026, 10, 19, 3, 15, tzinfo=UTC)
    return (lambda: base + timedelta(milliseconds=next(n)), lambda: 1000 * (next(n) + 1))


def example_drafts() -> list[EventDraft]:
    """One draft per C5 doc example line (run_started excluded)."""
    text = _DOC.read_text(encoding="utf-8")
    out: list[EventDraft] = []
    for block in re.findall(r"```jsonl model=BusEvent\n(.*?)```", text, re.S):
        for line in block.splitlines():
            ev = BUS_EVENT_ADAPTER.validate_json(line)
            if ev.type != "run_started":
                out.append(EventDraft(type=ev.type, payload=ev.payload, game_date=ev.game_date,
                                      decision_id=ev.decision_id))
    return out


def health_draft(detail: str = "x", date: str | None = None) -> EventDraft:
    """A valid health draft."""
    h = next(d for d in example_drafts() if d.type == "health")
    assert isinstance(h.payload, type(h.payload))
    return EventDraft(type="health", payload=h.payload.model_copy(update={"detail": detail}), game_date=date)


def is_health(ev: object) -> bool:
    """True for a HealthEvent."""
    return isinstance(ev, HealthEvent)
