"""Atomic ``sae_in.ini`` writer with retry (T2 U1, C2)."""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from sae.adapters import health as H
from sae.adapters.health import HealthFn
from sae.contracts.c2_ini import IniFile, KnobCommand, render_ini
from sae.contracts.c3_intent import IntentBatch, compile_batch
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import EmitReceipt

RETRY_DELAYS = (0.1, 0.2, 0.4)


class IniWriter:
    """Writes ``<path>.tmp`` then replaces ``path``; retries on ``OSError``; never raises."""

    def __init__(
        self,
        path: Path,
        *,
        replace: Callable[[str | Path, str | Path], None] = os.replace,
        fsync: Callable[[int], None] = os.fsync,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        health: HealthFn = H.log_health,
    ) -> None:
        self._path = path
        self._tmp = path.with_name(path.name + ".tmp")
        self._lock = asyncio.Lock()
        self._replace, self._fsync, self._sleep, self._health = replace, fsync, sleep, health

    def _write_once(self, data: bytes) -> None:
        # Windows: os.replace raises PermissionError while the game holds the file open; the retry
        # schedule covers it. Not executed in CI on this machine (tests mock replace).
        with open(self._tmp, "wb") as f:
            f.write(data)
            f.flush()
            self._fsync(f.fileno())
        self._replace(self._tmp, self._path)

    async def write(self, text: str, seq: int, *, report: bool = True) -> EmitReceipt:
        """Write ``text`` (UTF-8) atomically; the receipt says whether the file is in place."""
        async with self._lock:
            ok = False
            try:
                receipt = await self._write_retry(text.encode("utf-8"), seq, report)
                ok = receipt.ok
                return receipt
            finally:
                if not ok:  # also on cancellation
                    try:
                        self._tmp.unlink()
                    except OSError:
                        pass

    async def _write_retry(self, data: bytes, seq: int, report: bool) -> EmitReceipt:
        err: OSError | None = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                await asyncio.to_thread(self._write_once, data)
                return EmitReceipt(
                    seq=seq, ok=True, path=str(self._path), bytes_written=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            except OSError as exc:
                err = exc
                if attempt < len(RETRY_DELAYS):
                    await self._sleep(RETRY_DELAYS[attempt])
        detail = H.truncate(f"{type(err).__name__}: {err}")
        if report:
            self._health(HealthKind.INI_WRITE_FAILED, detail)
        return EmitReceipt(seq=seq, ok=False, error="write_failed", detail=detail)


async def emit_batch(batch: IntentBatch, writer: IniWriter) -> EmitReceipt:
    """Compile, render and write a batch; a compile failure is a failed receipt."""
    try:
        text = render_ini(compile_batch(batch))
    except ValueError as exc:  # pydantic.ValidationError subclasses ValueError
        return EmitReceipt(seq=batch.seq, ok=False, error="write_failed", detail=H.truncate(f"compile: {exc}"))
    return await writer.write(text, batch.seq)


async def neutralise(writer: IniWriter, knobs: KnobCommand, reason: str, health: HealthFn) -> bool:
    """Atomically rewrite the file as a knob-only file (C2 section 2.1). Never raises, never unlinks.

    ``reason`` is ``close`` or ``multiplayer``. Success reports ``ini_neutralised``; failure after the retries
    reports ``ini_write_failed`` with ``neutralise <reason>: <cause>`` (not an EmitReceipt, no ack_alarm count).
    """
    try:
        # The seq of the internal receipt is unused: a knob-only file has no seq (C2 section 3 rule 6).
        rec = await writer.write(render_ini(IniFile(knobs=knobs)), 1, report=False)
        ok, cause = rec.ok, rec.detail
    except Exception as exc:  # defensive: write() itself never raises
        ok, cause = False, f"{type(exc).__name__}: {exc}"
    try:
        if ok:
            health(HealthKind.INI_NEUTRALISED, reason)
        else:
            health(HealthKind.INI_WRITE_FAILED, H.truncate(f"neutralise {reason}: {cause}"))
    except Exception:  # health hook must not break shutdown
        pass
    return ok
