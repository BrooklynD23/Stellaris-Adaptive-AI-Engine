"""T4-internal daemon ports.

These Protocols are T4-internal ports, satisfied structurally by T6/T5 code (bus writer, control
inbox, governor, sensors, platform); T4 never imports those packages.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sae.contracts.c6_adapter import AdapterFactory, GodSink
from sae.contracts.c7_governor import Governor, Platform, Sensors
from sae.contracts.c9_control import ControlCommand, ControlResult
from sae.core.state import EventDraft

__all__ = ["AdapterFactory", "BusSink", "ControlInbox", "DaemonDeps", "InboxItem"]


class BusSink(GodSink, Protocol):
    """Where the daemon writes bus events. Extends C6 ``GodSink`` (``write_god``), so it can be handed to adapters."""

    def emit(self, draft: EventDraft) -> None:
        """Stamp and persist one event draft."""
        ...

    def flush(self) -> None:
        """Flush buffered output."""
        ...

    def close(self) -> None:
        """Flush and release files. Idempotent."""
        ...


@dataclass(frozen=True)
class InboxItem:
    """One control inbox entry: a valid command, a ready-made error, or both (``expired``, C9 v1.1 section 3.1)."""

    command: ControlCommand | None
    error: ControlResult | None
    handle: str


class ControlInbox(Protocol):
    """File inbox of C9 commands."""

    def poll(self) -> Sequence[InboxItem]:
        """Return new items, oldest first. Each item is returned at most once, until ``complete`` is called."""
        ...

    def complete(self, item: InboxItem, result: ControlResult) -> None:
        """Record the result and move the item to done."""
        ...


@dataclass(frozen=True)
class DaemonDeps:
    """Everything the daemon loop needs from outside, injected."""

    sink: BusSink
    governor: Governor
    sensors: Sensors
    platform: Platform
    inbox: ControlInbox
    parser_exe: str
    monotonic: Callable[[], float]
    reexec: Callable[[], None]
    #: Daemon session start (C9 section 3.1): commands issued before it are expired at pickup.
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
