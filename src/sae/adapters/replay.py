"""Replay adapter: re-feeds a recorded session dir to the engine (T2 U5, plan section 3).

Pull-driven: no pump task and no queues. ``observations()`` walks the recorded ``game.log`` lines;
acks produced by a pulse are appended to the ack box (and control is yielded once) before that pulse's
observation is yielded. Session dir layout (the ``LiveAdapter(record_dir=...)`` format)::

    game.log              normalised C1 lines
    game.log.ts           ``<byte_offset> <monotonic_ns>`` per recorded poll (offset = size at its end)
    saves/<offset:012d>-<name>.sav   parsed once the replayed byte offset reaches the prefix
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from sae.adapters import health as H
from sae.adapters._pull import AckBox, AckPlant, handoff, parse_ack_model, write_ini_sync
from sae.adapters.assembler import Assembler
from sae.adapters.health import HealthFn
from sae.adapters.keys import synth_key
from sae.adapters.logtail import parse_lines
from sae.adapters.simulator import ModSimulator, SimCountry
from sae.contracts.c1_log import AckRecord, HeartbeatRecord, LogRecord, PlayerRecord, SnapRecord
from sae.contracts.c3_intent import IntentBatch
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
from sae.contracts.common import normalize_game_date

_log = logging.getLogger("sae.adapters.replay")
_SAVE_NAME = re.compile(r"^(\d{12})-(.+)$")
_SPEED = re.compile(r"^(\d+(?:\.\d+)?)x$")
_AI_BASE, _HUMAN_BASE = 1000, 2000  # simulator country ids for mirrored sids


def parse_speed(text: str) -> float | None:
    """``max`` -> None (no pacing); ``realtime`` -> 1.0; ``<N>x`` -> N. Raises ``ValueError`` otherwise."""
    if text == "max":
        return None
    if text == "realtime":
        return 1.0
    m = _SPEED.match(text)
    if m is not None and float(m.group(1)) > 0:
        return float(m.group(1))
    raise ValueError(f"bad speed {text!r}: expected realtime|<N>x|max")


class ReplayAdapter:
    """GameAdapter over a recorded session. See the module docstring for the directory format."""

    def __init__(
        self,
        session_dir: Path,
        parser: SaveParser,
        god_sink: GodSink,
        *,
        health: HealthFn | None = None,
        scratch_dir: Path,
        speed: str = "max",
        from_: str | None = None,
        stop_at: str | None = None,
        ack_model: str = "echo",
        seed: int = 0,
        run_id: str | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._dir = session_dir
        self._parser = parser
        self._health = H.safe_health(health if health is not None else H.log_health)
        health = self._health
        self._scratch = scratch_dir
        self._factor = parse_speed(speed)
        self._from = normalize_game_date(from_) if from_ is not None else None
        self._stop = normalize_game_date(stop_at) if stop_at is not None else None
        self._model = parse_ack_model(ack_model)
        self._sleep = sleep
        self._mono = monotonic
        self._gate = H.new_gate()
        self._asm = Assembler(
            god_sink, health, self._gate, fixed_key=None if run_id is None else synth_key(run_id)
        )
        self._box = AckBox()
        self._sim = ModSimulator([])
        self._plant = None if self._model.kind == "from_recording" else AckPlant(self._sim, self._model, seed)
        self._sids: set[int] = set()  # mirrored managed AI sids
        self._humans: set[int] = set()
        self._post_hb: HeartbeatRecord | None = None
        self._pushed = False
        self._closed = False

    @property
    def mode(self) -> AdapterMode:
        """Always REPLAY."""
        return AdapterMode.REPLAY

    def clock(self) -> GameClock:
        """Current game time as last replayed."""
        return self._asm.clock()

    # -- iterators ----------------------------------------------------------------------------

    async def acks(self) -> AsyncIterator[IntentAck]:
        """Acks as they are produced; ends after the observations end or :meth:`close`."""
        async for ack in self._box.drain():
            yield ack

    async def observations(self) -> AsyncIterator[Observation]:
        """Replay the recording; ends at EOF, ``stop_at`` or :meth:`close`."""
        try:
            async for obs in self._walk():
                yield obs
        finally:
            self._box.finish()

    async def _walk(self) -> AsyncIterator[Observation]:
        try:
            lines = self._read_lines()
        except OSError as exc:
            self._health(HealthKind.INVALID_EVENT, H.truncate(f"replay: cannot read game.log: {exc}"))
            return
        saves = self._list_saves()
        stamps = self._read_stamps()
        consumed = 0
        for raw in lines:
            line = raw.decode("utf-8", errors="replace")  # per line: bad bytes must not shift offsets
            if self._closed:
                return
            await self._pace(stamps, consumed)
            pending = await self._apply_saves(saves, consumed)
            pending += self._on_line(line)
            if self._pushed:
                await asyncio.sleep(0)  # acks of this record are available before its observation
            for obs in pending:
                yield obs
                await handoff()
                if self._past_stop(obs):
                    return
            self._post_line()
            if self._pushed:
                await asyncio.sleep(0)  # the apply's acks reach the ack consumer before the next record
            consumed += len(raw) + 1
        pending = await self._apply_saves(saves, consumed) + self._asm.flush()
        for obs in pending:
            if self._visible(obs.game_date):
                yield obs
                await handoff()
            if self._past_stop(obs):
                return

    # -- recording access ---------------------------------------------------------------------

    def _read_lines(self) -> list[bytes]:
        lines = (self._dir / "game.log").read_bytes().split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        return lines

    def _list_saves(self) -> list[tuple[int, Path]]:
        found: list[tuple[int, Path]] = []
        try:
            for p in (self._dir / "saves").iterdir():
                m = _SAVE_NAME.match(p.name)
                if m is not None:
                    found.append((int(m.group(1)), p))
        except OSError:
            return []
        return sorted(found)

    def _read_stamps(self) -> list[tuple[int, int]] | None:
        """Sorted ``(offset, monotonic_ns)`` stamps, or None when pacing is off or the sidecar is unusable."""
        if self._factor is None:
            return None
        stamps: list[tuple[int, int]] = []
        try:
            for ln in (self._dir / "game.log.ts").read_text(encoding="utf-8").splitlines():
                off, ns = ln.split()
                stamps.append((int(off), int(ns)))
        except (OSError, ValueError):
            stamps = []
        if not stamps:
            self._health(HealthKind.INVALID_EVENT, "no_ts")
            self._factor = None
            return None
        return sorted(stamps)

    # -- pacing ---------------------------------------------------------------------------------

    async def _pace(self, stamps: list[tuple[int, int]] | None, consumed: int) -> None:
        """At a recorded poll boundary sleep until the next poll that added data (idle polls included)."""
        if stamps is None or self._factor is None:
            return
        offs = [o for o, _ in stamps]
        lo, hi = bisect.bisect_left(offs, consumed), bisect.bisect_right(offs, consumed)
        if lo == hi or hi >= len(stamps):
            return
        delay = (stamps[hi][1] - stamps[lo][1]) / 1e9 / self._factor
        if delay > 0:
            await self._sleep(delay)

    # -- saves ------------------------------------------------------------------------------------

    async def _apply_saves(self, saves: list[tuple[int, Path]], consumed: int) -> list[Observation]:
        out: list[Observation] = []
        while saves and saves[0][0] <= consumed:
            _, path = saves.pop(0)
            try:
                result = await self._parser.parse(str(path))
            except Exception as exc:
                _log.error("replay parse failed: %s", exc)
                self._health(HealthKind.PARSE_FAILED, H.truncate(f"{path.name}: {type(exc).__name__}: {exc}"))
                continue
            if result is None:
                continue
            parsed = self._asm.on_parse(result, self._asm.clock().session_epoch)
            out.extend(o for o in parsed if self._visible(o.game_date))
        return out



    def _push(self, ack: IntentAck) -> None:
        if self._visible(ack.game_date):
            self._box.push(ack)
            self._pushed = True

    def _visible(self, date: str) -> bool:
        return self._from is None or date >= self._from

    def _past_stop(self, obs: Observation) -> bool:
        return self._stop is not None and obs.game_date > self._stop

    def _on_line(self, line: str) -> list[Observation]:
        self._pushed = False
        clk = self._asm.clock()
        key = (clk.session_epoch, (clk.game_date or "")[:7])
        out: list[Observation] = []
        for rec in parse_lines([line], self._health, key, self._gate):
            if isinstance(rec, AckRecord) and self._plant is not None:
                continue  # recorded ACKs are ignored unless the model is from_recording
            self._mirror(rec)
            if isinstance(rec, HeartbeatRecord):
                self._post_hb = rec
            out.extend(self._route(self._asm.on_record(rec)))
        return [o for o in out if self._visible(o.game_date)]

    def _route(self, items: list[IntentAck | Observation]) -> list[Observation]:
        out: list[Observation] = []
        for it in items:
            if isinstance(it, IntentAck):
                self._push(it)
            else:
                out.append(it)
        return out

    # -- echo simulator ---------------------------------------------------------------------------

    def _mirror(self, rec: LogRecord) -> None:
        if self._plant is None:
            return
        if isinstance(rec, SnapRecord) and rec.sid not in self._sids:
            self._sids.add(rec.sid)
            self._sim.add_country(
                SimCountry(_AI_BASE + rec.sid, rec.name, sid=rec.sid, managed=True, contact_with_human=True)
            )
        elif isinstance(rec, PlayerRecord) and rec.sid not in self._humans:
            self._humans.add(rec.sid)
            name = rec.name or f"sid {rec.sid}"  # name is None in a recorded PLYR stub (C6 section 3)
            self._sim.add_country(SimCountry(_HUMAN_BASE + rec.sid, name, is_ai=False, sid=rec.sid))

    def _post_line(self) -> None:
        """After an HB's previous observation was handed out: stage due files and run the §6 apply."""
        hb, self._post_hb = self._post_hb, None
        self._pushed = False  # set again by _push when this apply produces acks
        if hb is None or self._plant is None:
            return
        try:
            st = self._sim.snapshot()
            st.anchor.m, st.anchor.initialised = hb.m, True
            self._sim.restore(st)
            self._plant.tick()
            for sid in sorted(self._sids):
                for line in self._sim.console_event_sae2(sid, hb.date):
                    clk = self._asm.clock()
                    for rec in parse_lines([line], self._health, (clk.session_epoch, hb.date[:7]), self._gate):
                        self._route(self._asm.on_record(rec))
        except Exception as exc:
            _log.exception("echo simulator failed")
            self._health(HealthKind.INVALID_EVENT, H.truncate(f"echo: {type(exc).__name__}: {exc}"))

    # -- emit / close -----------------------------------------------------------------------------

    async def emit(self, batch: IntentBatch) -> EmitReceipt:
        """Write ``scratch/sae_in.ini``; the ack model decides when (and whether) it runs. Never raises."""
        if self._closed:
            return EmitReceipt(seq=batch.seq, ok=False, error="closed")
        if self._asm.mp_blocked:
            return EmitReceipt(seq=batch.seq, ok=False, error="multiplayer")
        if self._asm.emit_blocked:
            return EmitReceipt(seq=batch.seq, ok=False, error="write_failed", detail="schema_skew")
        try:
            receipt, text = write_ini_sync(batch, self._scratch / "sae_in.ini", self._health)
            if text is not None and self._plant is not None:
                self._plant.submit(text)
            return receipt
        except Exception as exc:
            _log.exception("replay emit failed")
            return EmitReceipt(seq=batch.seq, ok=False, error="write_failed", detail=str(exc)[:200])

    async def close(self) -> None:
        """End both iterators. Idempotent."""
        self._closed = True
        self._box.finish()
