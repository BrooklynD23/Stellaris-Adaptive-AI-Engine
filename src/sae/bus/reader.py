"""Bus readers: whole-file scan, incremental tail, final ack lookup (C5 §5)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from sae.contracts.c5_bus import (
    BUS_EVENT_ADAPTER,
    GOD_BUS_EVENT_ADAPTER,
    BeliefUpdateEvent,
    ControlEvent,
    HealthEvent,
    IntentAckEvent,
    IntentEmittedEvent,
    IntentRejectedEvent,
    ObservationEvent,
    ObservationGodEvent,
    OutcomeEvent,
    RunStartedEvent,
    StrategySelectedEvent,
)

log = logging.getLogger(__name__)

__all__ = ["BusTail", "Event", "ReadResult", "SeqIssue", "final_acks", "iter_events", "read_events"]

Event = (RunStartedEvent | ObservationEvent | ObservationGodEvent | BeliefUpdateEvent | StrategySelectedEvent
         | IntentEmittedEvent | IntentRejectedEvent | IntentAckEvent | OutcomeEvent | ControlEvent | HealthEvent)

TAIL_BATCH_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class SeqIssue:
    """A gap or repeat: the event at valid-event index ``index`` had ``got`` instead of ``expected``."""

    index: int
    expected: int
    got: int


@dataclass(frozen=True)
class ReadResult:
    """Outcome of :func:`read_events`."""

    events: tuple[Event, ...]
    invalid: int
    seq_issues: tuple[SeqIssue, ...]
    partial_tail: bool


def _parse(line: bytes, god: bool) -> Event | None:
    try:
        adapter = GOD_BUS_EVENT_ADAPTER if god else BUS_EVENT_ADAPTER
        ev: Event = adapter.validate_json(line)
        return ev
    except ValidationError:
        return None


def iter_events(path: Path, *, god: bool = False) -> Iterator[Event]:
    """Stream valid events; invalid lines and a final line without LF are skipped."""
    with path.open("rb") as fh:
        for raw in fh:
            if raw.endswith(b"\n"):
                ev = _parse(raw, god)
                if ev is not None:
                    yield ev


def read_events(path: Path, *, god: bool = False) -> ReadResult:
    """Read a whole bus file, counting invalid lines and reporting seq gaps/repeats (no reordering)."""
    events: list[Event] = []
    issues: list[SeqIssue] = []
    invalid = 0
    partial = False
    expected = 0
    with path.open("rb") as fh:
        for raw in fh:
            if not raw.endswith(b"\n"):
                partial = True
                break
            ev = _parse(raw, god)
            if ev is None:
                invalid += 1
                continue
            if ev.seq != expected:
                issues.append(SeqIssue(len(events), expected, ev.seq))
            expected = ev.seq + 1
            events.append(ev)
    return ReadResult(tuple(events), invalid, tuple(issues), partial)


class BusTail:
    """Incremental reader: ``poll()`` returns events appended since the last call (LF-complete lines only)."""

    def __init__(self, path: Path, *, god: bool = False) -> None:
        self.path = path
        self.god = god
        self.offset = 0
        self.invalid = 0
        self.restarted = False

    def poll(self) -> list[Event]:
        """New valid events; restarts from 0 (``restarted`` set) when the file shrank below the offset."""
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.offset:
            self.offset = 0
            self.restarted = True
        if size == self.offset:
            return []
        with self.path.open("rb") as fh:
            fh.seek(self.offset)
            chunk = fh.read(TAIL_BATCH_BYTES)
        end = chunk.rfind(b"\n") + 1
        if end == 0 and len(chunk) == TAIL_BATCH_BYTES:  # one oversized line: skip it, never stall
            self.offset += len(chunk)
            self.invalid += 1
            return []
        self.offset += end
        out: list[Event] = []
        for raw in chunk[:end].splitlines(keepends=True):
            ev = _parse(raw, self.god)
            if ev is None:
                self.invalid += 1
            else:
                out.append(ev)
        return out


def final_acks(events: Iterator[Event] | list[Event] | tuple[Event, ...]) -> dict[tuple[int, int], IntentAckEvent]:
    """Last ``intent_ack`` per (country, seq)."""
    acks: dict[tuple[int, int], IntentAckEvent] = {}
    for ev in events:
        if isinstance(ev, IntentAckEvent):
            acks[(ev.payload.country, ev.payload.seq)] = ev
    return acks
