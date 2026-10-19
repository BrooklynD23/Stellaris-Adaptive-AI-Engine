"""Live and release adapter: tails ``game.log``, watches autosaves, writes ``sae_in.ini`` (T2 U4)."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from sae.adapters import health as H
from sae.adapters.assembler import Assembler
from sae.adapters.health import HealthFn
from sae.adapters.ini_writer import IniWriter, emit_batch, neutralise
from sae.adapters.logtail import LogTailer, parse_lines
from sae.adapters.paths import ResolvedPaths
from sae.adapters.savewatch import IRONMAN_NAME, SaveWatcher
from sae.contracts.c2_ini import KnobCommand
from sae.contracts.c3_intent import IntentBatch
from sae.contracts.c4_parse import ParseResult
from sae.contracts.c5_bus import HealthKind
from sae.contracts.c6_adapter import (
    AdapterMode,
    EmitReceipt,
    GameClock,
    GodSink,
    IntentAck,
    Observation,
    SaveParser,
)

_log = logging.getLogger("sae.adapters.live")
_Item = Observation | IntentAck | None


class LiveAdapter:
    """GameAdapter over the real game files. One pump task feeds an ack queue and an observation queue."""

    def __init__(
        self,
        paths: ResolvedPaths,
        parser: SaveParser,
        god_sink: GodSink,
        *,
        health: HealthFn,
        release: bool = False,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        poll_s: float = 2.0,
        record_dir: Path | None = None,
    ) -> None:
        self._paths = paths
        self._parser = parser
        self._release = release
        self._health = H.safe_health(health)
        health = self._health
        self._sleep = sleep
        self._mono = monotonic
        self._poll_s = poll_s
        self._gate = H.new_gate()
        self._tailer = LogTailer(paths.game_log, health=health, gate=self._gate)
        # A-20/A-34 UNVERIFIED: M0 checks the ironman.sav name and meta.ironman.
        self._watcher = SaveWatcher(
            paths.save_dir, monotonic=monotonic, include_ironman=True, baseline_ironman=release
        )
        self._asm = Assembler(god_sink, health, self._gate)
        self._writer = IniWriter(paths.ini_path, health=health)
        self._obs_q: asyncio.Queue[_Item] = asyncio.Queue()
        self._ack_q: asyncio.Queue[_Item] = asyncio.Queue()
        self._pump_task: asyncio.Task[None] | None = None
        self._parse_task: asyncio.Task[ParseResult | None] | None = None
        self._parse_epoch = 0
        self._closed = False
        self._ironman_detected = False
        self._mp_seen = False
        self._record_dir = record_dir
        self._rec_size = 0
        if record_dir is not None:
            (record_dir / "saves").mkdir(parents=True, exist_ok=True)

    @property
    def mode(self) -> AdapterMode:
        """LIVE, or RELEASE when constructed with ``release=True``."""
        return AdapterMode.RELEASE if self._release else AdapterMode.LIVE

    @property
    def ironman_detected(self) -> bool:
        """True once the meta probe saw ``meta.ironman`` (A-20 UNVERIFIED); all writes are then refused."""
        return self._ironman_detected

    def clock(self) -> GameClock:
        """Current game time as last observed."""
        return self._asm.clock()

    # -- iterators ----------------------------------------------------------------------------

    def _ensure_pump(self) -> None:
        if self._pump_task is None and not self._closed:
            self._pump_task = asyncio.create_task(self._pump())

    async def _drain(self, q: asyncio.Queue[_Item]) -> AsyncIterator[Observation | IntentAck]:
        self._ensure_pump()
        while True:
            item = await q.get()
            if item is None:
                q.put_nowait(None)  # a later iterator after close must not hang
                return
            yield item

    async def observations(self) -> AsyncIterator[Observation]:
        """Observations until :meth:`close`."""
        async for item in self._drain(self._obs_q):
            if isinstance(item, Observation):
                yield item

    async def acks(self) -> AsyncIterator[IntentAck]:
        """ACKs until :meth:`close` (release mode: none ever arrive)."""
        async for item in self._drain(self._ack_q):
            if isinstance(item, IntentAck):
                yield item

    # -- emit / close ---------------------------------------------------------------------------

    async def emit(self, batch: IntentBatch) -> EmitReceipt:
        """Write the batch to ``sae_in.ini``. Never raises."""
        if self._closed:
            return EmitReceipt(seq=batch.seq, ok=False, error="closed")
        if self._release:
            return EmitReceipt(seq=batch.seq, ok=False, error="release_mode")
        if self._ironman_detected:
            return EmitReceipt(seq=batch.seq, ok=False, error="ironman")
        if self._asm.mp_blocked:
            return EmitReceipt(seq=batch.seq, ok=False, error="multiplayer")
        if self._asm.emit_blocked:
            return EmitReceipt(seq=batch.seq, ok=False, error="write_failed", detail="schema_skew")
        try:
            return await emit_batch(batch, self._writer)
        except Exception as exc:
            _log.exception("emit failed")
            return EmitReceipt(seq=batch.seq, ok=False, error="write_failed", detail=str(exc)[:200])

    def _knobs(self) -> KnobCommand:
        last = self._asm.last_knobs
        if last is None:
            return KnobCommand(emit_period=1, quiet=0)  # mod defaults (C2 section 2.1)
        return KnobCommand(emit_period=last[0], quiet=last[1])

    async def neutralise(self, reason: str = "close") -> bool:
        """Rewrite ``sae_in.ini`` as a knob-only file (C2 section 2.1); never deletes. Safe after :meth:`close`.

        False (nothing written) in release mode, when no file exists, or for ``multiplayer`` while Ironman
        observe-only is latched. The ``close`` rewrite still happens under Ironman (maintainer decision,
        proposal 017 as amended): only intent writes are blocked there.
        """
        blocked = self._ironman_detected and reason != "close"
        if self._release or blocked or not self._paths.ini_path.exists():
            return False
        return await neutralise(self._writer, self._knobs(), reason, self._health)

    async def close(self) -> None:
        """Cancel the pump and any parse; end both iterators. Idempotent."""
        if self._closed:
            return
        self._closed = True
        for task in (self._pump_task, self._parse_task):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._obs_q.put_nowait(None)
        self._ack_q.put_nowait(None)
        await self.neutralise("close")  # after the pump has stopped (C6 section 1.9)

    # -- pump -------------------------------------------------------------------------------------

    async def _pump(self) -> None:
        while True:
            items: list[IntentAck | Observation] = []
            try:
                lines = await asyncio.to_thread(self._tailer.poll)  # blocking file I/O off the loop
                items = self._process(lines)
                if self._asm.mp_blocked and not self._mp_seen:
                    await self.neutralise("multiplayer")  # HELLO mp=1: stale country lines must not apply in MP
                self._mp_seen = self._asm.mp_blocked
                if self._parse_task is None:
                    path = await asyncio.to_thread(self._watcher.poll)
                    if path is not None:
                        self._start_parse(path)
            except Exception as exc:
                self._report(f"poll: {type(exc).__name__}: {exc}")
            for it in items:
                (self._ack_q if isinstance(it, IntentAck) else self._obs_q).put_nowait(it)
                # Hand off to that queue's feeder before the next put: T4 runs one feeder per queue
                # into one shared inbox, so without a yield the later-woken feeder's items reorder.
                await asyncio.sleep(0)
            await self._sleep(self._poll_s)

    def _report(self, detail: str) -> None:
        _log.exception("adapter error: %s", detail)
        try:
            self._health(HealthKind.INVALID_EVENT, H.truncate(detail))
        except Exception:
            _log.exception("health callback failed")

    def _poll_once(self) -> list[IntentAck | Observation]:
        """One poll; returns acks and observations in delivery order."""
        out = self._process(self._tailer.poll())
        if self._parse_task is None:
            path = self._watcher.poll()
            if path is not None:
                self._start_parse(path)
        return out

    def _process(self, lines: list[str]) -> list[IntentAck | Observation]:
        """Assemble one poll's lines (and a finished parse) into items; no file I/O except recording."""
        out: list[IntentAck | Observation] = []
        self._record_lines(lines)
        clk = self._asm.clock()
        key = (clk.session_epoch, (clk.game_date or "")[:7])
        for rec in parse_lines(lines, self._health, key, self._gate):
            try:
                out.extend(self._asm.on_record(rec))
            except Exception as exc:
                self._report(f"record: {type(exc).__name__}: {exc}")
        out.extend(self._apply_parse())
        out.extend(self._asm.flush())
        return out

    def _apply_parse(self) -> list[Observation]:
        task = self._parse_task
        if task is None or not task.done():
            return []
        self._parse_task = None
        if task.cancelled():
            return []
        exc = task.exception()
        if exc is not None:
            _log.error("parse task failed: %r", exc)
            self._health(HealthKind.PARSE_FAILED, H.truncate(repr(exc)))
            return []
        result = task.result()
        return list(self._asm.on_parse(result, self._parse_epoch)) if result is not None else []

    def _start_parse(self, path: Path) -> None:
        self._parse_epoch = self._asm.clock().session_epoch
        if path.name == IRONMAN_NAME and not self._release:
            self._parse_task = asyncio.create_task(self._probe_ironman(path))
        else:
            self._parse_task = asyncio.create_task(self._record_then_parse(path))

    async def _record_then_parse(self, path: Path) -> ParseResult | None:
        await self._record_save(path)
        return await self._parser.parse(str(path))

    async def _probe_ironman(self, path: Path) -> ParseResult | None:
        """Meta-only parse of ``ironman.sav`` (C6 section 1.10); latches observe-only. Always returns None so
        the meta-only result never reaches the assembler."""
        await self._record_save(path)
        result = await self._parser.parse(str(path), sections=("meta",))
        if result is not None and result.meta.ironman is True and not self._ironman_detected:
            self._ironman_detected = True
            self._health(HealthKind.IRONMAN_OBSERVE_ONLY, "ironman: observe only")
        return None

    # -- recording ----------------------------------------------------------------------------------

    def _record_lines(self, lines: list[str]) -> None:
        """Append lines to ``game.log``. The recording is normalised (LF endings, U+FFFD for bad UTF-8),
        not raw bytes; ``.ts`` holds the recorded size at the end of each poll's chunk."""
        if self._record_dir is None or not lines:
            return
        try:
            data = "".join(f"{ln}\n" for ln in lines).encode("utf-8")
            with (self._record_dir / "game.log").open("ab") as f:
                f.write(data)
            self._rec_size += len(data)
            with (self._record_dir / "game.log.ts").open("a", encoding="utf-8") as f:
                f.write(f"{self._rec_size} {time.monotonic_ns()}\n")
        except OSError as exc:
            _log.error("record failed: %s", exc)

    async def _record_save(self, path: Path) -> None:
        if self._record_dir is None:
            return
        try:
            dest = self._record_dir / "saves" / f"{self._rec_size:012d}-{path.name}"
            await asyncio.to_thread(shutil.copy2, path, dest)
        except OSError as exc:
            _log.error("save record failed: %s", exc)
