"""Bus -> sink streaming. The only module that imports ``rerun`` (lazily)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import psutil

from sae.bus.reader import BusTail
from sae.contracts.c5_bus import BUS_FILE, GOD_FILE
from sae.contracts.c7_governor import TelemetryTier
from sae.telemetry.mapping import Mapper, Record

log = logging.getLogger(__name__)

__all__ = ["ListSink", "RerunSink", "Sink", "rss_bytes", "run_stream"]

POLL_S = 2.0
DISCONNECT_TIMEOUT_S = 3.0
RSS_LIMIT = 150 * 1024 * 1024  # C8 section 2: telemetry process cap 150 MiB (resource proposal)


class Sink(Protocol):
    """Where records go."""

    def start(self) -> None:
        """Send the blueprint (once)."""

    def log(self, rec: Record) -> None:
        """Log one record."""

    def close(self) -> None:
        """Flush and release."""


class ListSink:
    """Collects records in memory (tests)."""

    def __init__(self) -> None:
        self.records: list[Record] = []
        self.started = 0
        self.closed = False

    def start(self) -> None:
        """Count blueprint sends."""
        self.started += 1

    def log(self, rec: Record) -> None:
        """Append."""
        self.records.append(rec)

    def close(self) -> None:
        """Mark closed."""
        self.closed = True


class RerunSink:
    """A Rerun RecordingStream writing ``.rrd`` (path) or connecting over gRPC (``rerun+http://...``)."""

    def __init__(self, run_id: str, target: str | Path, *, url: bool) -> None:
        import rerun as rr  # lazy: optional extra

        self._rr: Any = rr
        self._rec: Any = rr.RecordingStream("sae_telemetry", recording_id=run_id)
        if url:
            self._rec.connect_grpc(str(target))
        else:
            self._rec.save(Path(target))

    def start(self) -> None:
        """Send the Engine Console blueprint."""
        from sae.telemetry.blueprint import engine_console

        self._rec.send_blueprint(engine_console())

    def log(self, rec: Record) -> None:
        """Set the three timelines then log a Scalars or TextLog row."""
        r = self._rec
        r.set_time("seq", sequence=rec.seq)
        r.set_time("t_wall", timestamp=rec.t_wall)
        if rec.game_month is not None:
            r.set_time("game_month", sequence=rec.game_month)
        else:
            r.disable_timeline("game_month")  # do not inherit the previous record's month
        if rec.kind == "scalar":
            r.log(rec.entity, self._rr.Scalars(rec.value))
        else:
            r.log(rec.entity, self._rr.TextLog(rec.text, level=rec.level))

    def close(self) -> None:
        """Flush and disconnect."""
        try:
            self._rec.flush()
        except RuntimeError as exc:  # rerun 0.39: "gRPC has been unable to connect for 6s"
            log.warning("rerun flush failed: %s", exc)
        # rerun 0.39 disconnect() can block indefinitely retrying an unreachable viewer: bound it.
        def _disconnect() -> None:
            try:
                self._rec.disconnect()
            except RuntimeError as exc:
                log.warning("rerun disconnect failed: %s", exc)

        t = threading.Thread(target=_disconnect, name="rerun-disconnect", daemon=True)
        t.start()
        t.join(DISCONNECT_TIMEOUT_S)
        if t.is_alive():
            log.warning("rerun disconnect still blocked after %.0fs; abandoning it", DISCONNECT_TIMEOUT_S)


def rss_bytes() -> int:
    """This process's resident set size."""
    return int(psutil.Process().memory_info().rss)


def run_stream(
    run_dir: Path,
    sink: Sink,
    tier: TelemetryTier,
    *,
    is_live: Callable[[], bool],
    sleep: Callable[[float], None] = time.sleep,
    rss: Callable[[], int] = rss_bytes,
    poll_s: float = POLL_S,
) -> int:
    """Stream ``bus.jsonl`` (+ god at T2) into ``sink``; 0 on completion, 1 if RSS exceeds RSS_LIMIT (150 MiB).

    Follows while ``is_live()``; otherwise one pass to EOF (offline: no level cap).
    """
    live = is_live()
    mapper = Mapper(tier, live=live)
    tails = [BusTail(run_dir / BUS_FILE)]
    if tier is TelemetryTier.T2:
        tails.append(BusTail(run_dir / GOD_FILE, god=True))
    try:
        sink.start()
        while True:
            alive = is_live() if live else False
            moved = False
            for tail in tails:
                before = tail.offset
                for ev in tail.poll():
                    for rec in mapper.map(ev):
                        sink.log(rec)
                moved = moved or tail.offset != before or tail.restarted
                tail.restarted = False
            used = rss()
            if used > RSS_LIMIT:
                log.error("telemetry RSS %d MiB exceeds limit; exiting", used >> 20)
                return 1
            if moved:
                continue  # drain: more may be waiting beyond one batch
            if not alive:
                return 0
            sleep(poll_s)
    finally:
        sink.close()
