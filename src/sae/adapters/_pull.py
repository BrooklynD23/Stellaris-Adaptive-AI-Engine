"""Shared pieces of the pull-driven replay and synth adapters (T2 U5): ack box and ack-model plant."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from sae.adapters.health import HealthFn
from sae.adapters.simulator import ModSimulator
from sae.contracts.c2_ini import render_ini
from sae.contracts.c3_intent import IntentBatch, compile_batch
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import EmitReceipt, IntentAck

_log = logging.getLogger("sae.adapters.pull")


#: Scheduler steps a pull generator yields after handing out an observation. T4's loop feeds observations
#: into an unbounded queue, so without this a max-speed walk would run to EOF before the consumer's first
#: ``emit`` (0 acks). Mitigation only; the real fix is T4's (proposal 011-T2).
HANDOFF_STEPS = 8


async def handoff() -> None:
    """Give the consumer tasks several scheduler turns before the next pulse is computed."""
    for _ in range(HANDOFF_STEPS):
        await asyncio.sleep(0)


def write_ini_sync(batch: IntentBatch, path: Path, health: HealthFn) -> tuple[EmitReceipt, str | None]:
    """Compile and write ``path`` atomically with no thread hop, so ``emit`` is one scheduler step.

    Returns the receipt and the written text (None on failure). Never raises.
    """
    try:
        text = render_ini(compile_batch(batch))
        data = text.encode("ascii")
    except (ValueError, UnicodeError) as exc:  # pydantic ValidationError is a ValueError
        return EmitReceipt(seq=batch.seq, ok=False, error="write_failed", detail=f"compile: {exc}"[:200]), None
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        health(HealthKind.INI_WRITE_FAILED, str(exc)[:200])
        return EmitReceipt(seq=batch.seq, ok=False, error="write_failed", detail=str(exc)[:200]), None
    receipt = EmitReceipt(
        seq=batch.seq, ok=True, path=str(path), bytes_written=len(data), sha256=hashlib.sha256(data).hexdigest()
    )
    return receipt, text


class AckBox:
    """Ack list the observation generator fills and :meth:`drain` empties; ends after :meth:`finish`."""

    def __init__(self) -> None:
        self._items: deque[IntentAck] = deque()
        self._evt = asyncio.Event()
        self._done = False

    def push(self, ack: IntentAck) -> None:
        """Make an ack available to the ack iterator."""
        self._items.append(ack)
        self._evt.set()

    def finish(self) -> None:
        """No more acks will arrive; ``drain`` ends once empty. Idempotent."""
        self._done = True
        self._evt.set()

    async def drain(self) -> AsyncIterator[IntentAck]:
        """Yield acks as they arrive until :meth:`finish`."""
        while True:
            while self._items:
                yield self._items.popleft()
            if self._done:
                return
            self._evt.clear()
            await self._evt.wait()


@dataclass(frozen=True)
class AckModelSpec:
    """Parsed ``--ack-model``: ``echo``, ``from_recording``, ``drop:<p>`` or ``delay:<k>``."""

    kind: str
    p: float = 0.0
    k: int = 0


def parse_ack_model(text: str) -> AckModelSpec:
    """Parse an ack-model string. Raises ``ValueError`` on anything else."""
    if text in ("echo", "from_recording"):
        return AckModelSpec(text)
    name, sep, arg = text.partition(":")
    try:
        if sep and name == "drop" and 0.0 <= (p := float(arg)) <= 1.0:
            return AckModelSpec("drop", p=p)
        if sep and name == "delay" and (k := int(arg)) >= 0:
            return AckModelSpec("delay", k=k)
    except ValueError:
        pass
    raise ValueError(f"bad ack model {text!r}: expected echo|drop:<p 0..1>|delay:<k>|from_recording")


class AckPlant:
    """Decides when a written ini file reaches the simulator (``echo``/``drop``/``delay``).

    ``submit`` is called by ``emit``; ``tick`` once per pulse/HB *before* that pulse's apply step, so
    an echoed file runs at the first pulse produced after ``emit`` returned.
    """

    def __init__(self, sim: ModSimulator, spec: AckModelSpec, seed: int) -> None:
        self._sim = sim
        self._spec = spec
        self._rng = random.Random(seed)
        self._ticks = 0
        self._queue: list[tuple[int, str]] = []

    def submit(self, text: str) -> None:
        """Take a freshly written ini text."""
        if self._spec.kind == "drop" and self._rng.random() < self._spec.p:
            return
        k = self._spec.k if self._spec.kind == "delay" else 0
        self._queue.append((self._ticks + k + 1, text))

    def tick(self) -> None:
        """One pulse is about to be applied: stage every file that is due."""
        self._ticks += 1
        due = [t for d, t in self._queue if d <= self._ticks]
        self._queue = [(d, t) for d, t in self._queue if d > self._ticks]
        for text in due:
            self._sim.run_ini(text)
