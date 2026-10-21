"""Bus writer: stamps, validates and appends C5 events (``bus.jsonl`` and ``bus.god.jsonl``).

One unbuffered ``os.write`` per line on an ``O_APPEND`` descriptor. I/O failure truncates back to the last
good offset and is counted, never raised into the daemon. Semantics: ``contracts/C5_bus.md``, plan T6 §1/§6.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

from pydantic import TypeAdapter, ValidationError

import sae
from sae.contracts import CONTRACT_VERSIONS
from sae.contracts.c5_bus import (
    BUS_EVENT_ADAPTER,
    BUS_FILE,
    GOD_BUS_EVENT_ADAPTER,
    GOD_FILE,
    MAX_LINE_BYTES,
    BusEvent,
    GodBusEvent,
    HealthKind,
    HealthPayload,
    PolicyRef,
    RunStartedPayload,
)
from sae.contracts.c6_adapter import GodObservation
from sae.contracts.c7_governor import GameState, HealthLevel
from sae.contracts.c8_config import SaeConfig

if TYPE_CHECKING:  # runtime import avoided: keeps sae.bus free of sae.core
    from sae.core.state import EventDraft

log = logging.getLogger(__name__)

__all__ = ["BusValidationError", "BusWriter", "open_sink"]

_TAIL_LINES = 1024
_LOG_EVERY_NS = 60 * 1_000_000_000
_O_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)


class BusValidationError(ValueError):
    """A producer emitted an invalid or oversized event (raised only in the ``dev`` preset)."""


def _fow_table() -> dict[str, str]:
    """type -> fow, read from the event models' field defaults (never hand-written)."""
    table: dict[str, str] = {}
    for adapter_type in (BusEvent, GodBusEvent):
        union = get_args(adapter_type)[0]
        for cls in get_args(union):
            table[cls.model_fields["type"].default] = cls.model_fields["fow"].default
    return table


_FOW = _fow_table()


def config_sha256(cfg: SaeConfig) -> str:
    """SHA-256 of the resolved config JSON (replicates ``sae.daemon.runs.config_sha256``)."""
    return hashlib.sha256(cfg.model_dump_json().encode("utf-8")).hexdigest()


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


class _Stream:
    """One append-only file with its own gapless ``seq``."""

    def __init__(self, path: Path, adapter: TypeAdapter[Any], *, resume: bool, run_id: str) -> None:
        self.path = path
        self.adapter = adapter
        self.run_id = run_id
        self.next_seq = 0
        self.dropped_io = 0
        self.fresh = True
        self.prefix = b""
        self._last_month: str | None = None
        self._last_log_ns: int | None = None
        self._last_drop_ns: int | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 0:
            if not resume:
                raise FileExistsError(f"refusing to overwrite existing bus file: {path}")
            self._recover()
        self.fd: int | None = os.open(path, _O_FLAGS, 0o644)
        self.good_offset = path.stat().st_size

    def _recover(self) -> None:
        """Truncate a partial tail, check run_id, set ``next_seq`` from the last LF-terminated line."""
        first: bytes | None = None
        lines: deque[bytes] = deque(maxlen=_TAIL_LINES)  # only the tail is needed to find the last seq
        good = 0
        with self.path.open("rb") as fh:
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break
                good += len(raw)
                first = first if first is not None else raw
                lines.append(raw)
        if good != self.path.stat().st_size:
            os.truncate(self.path, good)
        if first is None:
            return  # no complete line: a fresh file
        try:
            first_run = json.loads(first)["run_id"]
        except (KeyError, TypeError, ValueError) as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
            raise ValueError(f"{self.path}: unreadable bus line, cannot resume") from exc
        if first_run != self.run_id:
            raise ValueError(f"{self.path}: first line belongs to another run")
        for raw in reversed(lines):  # garbage (e.g. torn write isolated by a LF) may trail the last good line
            try:
                seq = json.loads(raw)["seq"]
            except (KeyError, TypeError, ValueError):
                continue
            if isinstance(seq, int) and not isinstance(seq, bool):
                self.next_seq = seq + 1
                break
        else:
            raise ValueError(f"{self.path}: no line with an integer seq, cannot resume")
        self.fresh = False

    def warn(self, now_ns: int, msg: str, *args: object) -> None:
        if self._last_log_ns is None or now_ns - self._last_log_ns >= _LOG_EVERY_NS:
            self._last_log_ns = now_ns
            log.error(msg, *args)

    def warn_drop(self, now_ns: int, msg: str, *args: object) -> None:
        """Throttled log for validation drops, on its own timestamp (never starved by I/O-failure logs)."""
        if self._last_drop_ns is None or now_ns - self._last_drop_ns >= _LOG_EVERY_NS:
            self._last_drop_ns = now_ns
            log.error(msg, *args)

    def append(self, line: bytes, game_date: str | None, now_ns: int) -> bool:
        """Write one LF-terminated line; True on success (seq advances), False if dropped on I/O error."""
        if self.fd is None:
            return False
        data = self.prefix + line + b"\n"
        try:
            n = os.write(self.fd, data)
            if n != len(data):
                raise OSError(f"short write {n}/{len(data)}")
        except OSError as exc:
            self.dropped_io += 1
            self.warn(now_ns, "bus write failed on %s (%d dropped): %s", self.path, self.dropped_io, exc)
            try:
                os.ftruncate(self.fd, self.good_offset)
            except OSError as exc2:
                self.warn(now_ns, "bus truncate failed on %s: %s", self.path, exc2)
                try:
                    self.good_offset = os.fstat(self.fd).st_size
                except OSError:
                    pass
                self.prefix = b"\n"  # isolate any garbage to one invalid line
            return False
        self.prefix = b""
        self.good_offset += len(data)
        self.next_seq += 1
        month = game_date[:7] if game_date else None
        if month is not None and month != self._last_month:
            self._last_month = month
            self.sync(now_ns)
        return True

    def sync(self, now_ns: int) -> None:
        """fsync; failure is logged only."""
        if self.fd is not None:
            try:
                os.fsync(self.fd)
            except OSError as exc:
                self.warn(now_ns, "bus fsync failed on %s: %s", self.path, exc)

    def close(self, now_ns: int) -> None:
        """fsync and close; idempotent."""
        if self.fd is not None:
            self.sync(now_ns)
            try:
                os.close(self.fd)
            except OSError as exc:
                log.error("bus close failed on %s: %s", self.path, exc)
            self.fd = None


class BusWriter:
    """Implements ``sae.daemon.ports.BusSink`` over ``bus.jsonl`` and ``bus.god.jsonl``."""

    def __init__(self, run_dir: Path, run_id: str, config: SaeConfig, *, resume: bool = False,
                 wall: Callable[[], datetime] | None = None, mono: Callable[[], int] | None = None,
                 policy: PolicyRef | None = None) -> None:
        self._run_id = run_id
        self._dev = config.preset == "dev"
        self._wall = wall or (lambda: datetime.now(UTC))
        self._mono = mono or time.monotonic_ns
        self._lock = threading.RLock()
        self._closed = False
        self._last_invalid_ns: int | None = None
        self._suppressed_invalid = 0
        self._health: tuple[GameState, HealthLevel] = (GameState.PLAYING, HealthLevel.H0)
        self._started = RunStartedPayload(
            engine_version=sae.__version__, contracts=dict(CONTRACT_VERSIONS), adapter_mode=config.adapter,
            preset=config.preset, config_sha256=config_sha256(config),
            platform=f"{sys.platform}-{platform.machine()}", python=platform.python_version(), policy=policy)
        self._bus = _Stream(run_dir / BUS_FILE, BUS_EVENT_ADAPTER, resume=resume, run_id=run_id)
        try:
            self._god = _Stream(run_dir / GOD_FILE, GOD_BUS_EVENT_ADAPTER, resume=resume, run_id=run_id)
        except Exception:
            self._bus.close(0)
            raise
        for stream in (self._bus, self._god):
            if stream.fresh:
                self._write(stream, "run_started", self._started.model_dump(mode="json"), None, None)

    @property
    def dropped_io(self) -> int:
        """Lines lost to I/O errors (both files)."""
        return self._bus.dropped_io + self._god.dropped_io

    def _write(self, stream: _Stream, type_: str, payload: dict[str, Any], game_date: str | None,
               decision_id: str | None) -> BaseException | None:
        """Stamp, validate, serialize, append. Returns the validation error (None on success or I/O drop)."""
        now_ns = self._mono()
        if stream.next_seq == 0 and type_ != "run_started":
            self._write(stream, "run_started", self._started.model_dump(mode="json"), None, None)
            if stream.next_seq == 0:
                return None  # run_started write failed (counted); never lead with another type
        try:
            env = {"v": 1, "run_id": self._run_id, "seq": stream.next_seq, "t_wall": _iso(self._wall()),
                   "t_mono_ns": now_ns, "game_date": game_date, "decision_id": decision_id, "type": type_,
                   "fow": _FOW[type_], "payload": payload}
            event = stream.adapter.validate_python(env)
            line: bytes = stream.adapter.dump_json(event)
            if len(line) > MAX_LINE_BYTES:
                raise ValueError(f"line of {len(line)} bytes exceeds {MAX_LINE_BYTES}")
        except (ValueError, KeyError) as exc:
            return exc
        if stream.append(line, game_date, now_ns) and type_ == "health":
            self._health = (event.payload.state, event.payload.level)
        return None

    def emit(self, draft: EventDraft) -> None:
        """Stamp, validate and append one draft to ``bus.jsonl``."""
        with self._lock:
            if self._closed:
                if self._dev:
                    raise RuntimeError("emit after close")
                return
            err: BaseException | None
            if draft.type == "observation_god":
                err = ValueError("observation_god may only be written through write_god")
            else:
                err = self._write(self._bus, draft.type, draft.payload.model_dump(mode="json"),
                                  draft.game_date, draft.decision_id)
            if err is None:
                return
            msg = f"{draft.type}: {_first_error(err)}"
            if self._dev:
                raise BusValidationError(msg) from err
            self._report_invalid(msg)

    def _report_invalid(self, msg: str) -> None:
        now_ns = self._mono()
        if self._last_invalid_ns is not None and now_ns - self._last_invalid_ns < _LOG_EVERY_NS:
            self._suppressed_invalid += 1
            return
        self._last_invalid_ns = now_ns
        if self._suppressed_invalid:
            msg = f"{msg} (+{self._suppressed_invalid} suppressed)"
            self._suppressed_invalid = 0
        state, level = self._health
        payload = HealthPayload(kind=HealthKind.INVALID_EVENT, component="bus", state=state, level=level,
                                detail=msg[:500])
        err = self._write(self._bus, "health", payload.model_dump(mode="json"), None, None)
        if err is not None:
            log.error("invalid_event health failed (%s); dropped: %s", err, msg)
        else:
            log.warning("dropped invalid bus event: %s", msg)

    def write_god(self, obs: GodObservation) -> None:
        """Append a god observation to ``bus.god.jsonl``. Never raises."""
        with self._lock:
            if self._closed:
                return
            try:
                err = self._write(self._god, "observation_god", obs.model_dump(mode="json"), obs.game_date, None)
            except Exception:
                log.exception("write_god failed")
                return
            if err is not None:
                self._god.warn_drop(self._mono(), "god event dropped: %s", _first_error(err))

    def flush(self) -> None:
        """No-op: every line is written unbuffered."""

    def close(self) -> None:
        """fsync and close both files. Idempotent."""
        with self._lock:
            if not self._closed:
                self._closed = True
                if self._suppressed_invalid:
                    log.warning("%d invalid_event health reports suppressed by throttle", self._suppressed_invalid)
                self._bus.close(self._mono())
                self._god.close(self._mono())


def _first_error(exc: BaseException) -> str:
    if isinstance(exc, ValidationError):
        e = exc.errors()[0]
        return f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"[:300]
    return str(exc)[:300]


def open_sink(run_dir: Path, run_id: str, config: SaeConfig, *, resume: bool = False,
              wall: Callable[[], datetime] | None = None, mono: Callable[[], int] | None = None,
              policy: PolicyRef | None = None) -> BusWriter:
    """Open (or resume) the run's bus files and return the writer.

    ``policy`` (C8 section 5.1 v1.2 / C5 v1.3) is the PolicyVersion the daemon resolved; it goes in
    ``run_started.payload.policy``. ``None`` keeps the pre-v1.3 shape (built-in 0.1.0 implied).
    """
    return BusWriter(run_dir, run_id, config, resume=resume, wall=wall, mono=mono, policy=policy)
