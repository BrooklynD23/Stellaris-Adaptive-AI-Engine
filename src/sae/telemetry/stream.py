"""Bus -> sink streaming. The only module that imports ``rerun`` (lazily)."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

import psutil

from sae.bus.reader import BusTail
from sae.contracts.c5_bus import BUS_FILE, GOD_FILE
from sae.contracts.c7_governor import TelemetryTier
from sae.telemetry.mapping import Mapper, Record

log = logging.getLogger(__name__)

__all__ = ["ListSink", "RerunSink", "Sink", "limit_process_memory", "rss_bytes", "run_stream"]

POLL_S = 2.0
DISCONNECT_TIMEOUT_S = 3.0
RSS_LIMIT = 150 * 1024 * 1024  # C8 section 2: telemetry process cap 150 MiB (resource proposal)

# Read by numpy/OpenBLAS, tokio, mimalloc (rerun_bindings) and Arrow when they load, so they must be set before
# ``import rerun``. Without them each library sizes thread pools / arenas to the CPU count. setdefault: a user
# value wins.
LOW_MEMORY_ENV = {
    "OPENBLAS_NUM_THREADS": "1",  # numpy is imported by rerun; telemetry does no BLAS
    "MIMALLOC_PURGE_DELAY": "0",  # return freed pages to the OS at once
    "MIMALLOC_ARENA_EAGER_COMMIT": "0",
    "ARROW_DEFAULT_MEMORY_POOL": "system",  # no jemalloc arenas + background thread in pyarrow
}
# Bus bytes parsed per poll. Parsed events and their records cost several times their JSON, so peak RSS on a
# long offline bus tracks this (3 MiB bus, ext4 venv: 8 MiB -> 142, 2 MiB -> 139, 256 KiB -> 124 MiB peak).
# BusTail still reads one whole C5 line (<= 1 MiB) when it is longer than this.
TAIL_BYTES = 256 * 1024
_PR_SET_THP_DISABLE = 41  # linux/prctl.h
_M_ARENA_MAX = -8  # malloc.h; same 2-arena policy T5 uses for workers (MALLOC_ARENA_MAX=2)


class Sink(Protocol):
    """Where records go."""

    def start(self) -> None:
        """Send the blueprint (once)."""

    def log_batch(self, recs: Sequence[Record]) -> None:
        """Log one poll's records (seq order)."""

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

    def log_batch(self, recs: Sequence[Record]) -> None:
        """Append."""
        self.records.extend(recs)

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

    def log_batch(self, recs: Sequence[Record]) -> None:
        """One ``send_columns`` per (entity, kind, has-month) group, not one ``log`` per record.

        Per-record ``log`` grows RSS with the record count (3k records 119 MiB, 30k 157 MiB, 100k 204 MiB peak,
        rerun 0.39); columns stay flat (~115 MiB). Records without a month get no ``game_month`` index, as the
        per-record path did with ``disable_timeline``.
        """
        groups: dict[tuple[str, str, bool], list[Record]] = {}
        for rec in recs:
            groups.setdefault((rec.entity, rec.kind, rec.game_month is not None), []).append(rec)
        rr = self._rr
        for (entity, kind, has_month), rows in groups.items():
            indexes = [rr.TimeColumn("seq", sequence=[r.seq for r in rows]),
                       rr.TimeColumn("t_wall", timestamp=[r.t_wall for r in rows])]
            if has_month:
                indexes.append(rr.TimeColumn("game_month", sequence=[r.game_month for r in rows]))
            if kind == "scalar":
                columns = rr.Scalars.columns(scalars=[r.value for r in rows])
            else:
                columns = rr.TextLog.columns(text=[r.text for r in rows], level=[r.level for r in rows])
            self._rec.send_columns(entity, indexes=indexes, columns=columns)

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


def limit_process_memory() -> None:
    """Shrink the telemetry process's native footprint before rerun loads (C8 section 2 cap).

    Linux also turns transparent huge pages off for this process: under THP ``always`` each touched thread stack
    and allocator arena can be backed by 2 MiB pages. Suspected (not measured) cause of the 162-172 MiB seen on
    the GitHub ubuntu-24.04 runner for a run that peaks at ~130 MiB on WSL2; the cap message's anon/file split
    is there to confirm or refute it.
    """
    for key, value in LOW_MEMORY_ENV.items():
        os.environ.setdefault(key, value)
    if sys.platform != "linux":
        return
    try:
        libc = ctypes.CDLL(None)
        libc.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
        if libc.prctl(_PR_SET_THP_DISABLE, 1, 0, 0, 0) != 0:
            log.warning("telemetry: prctl(PR_SET_THP_DISABLE) failed")
        if libc.mallopt(_M_ARENA_MAX, 2) != 1:  # musl: no-op stub
            log.warning("telemetry: mallopt(M_ARENA_MAX) failed")
    except (OSError, AttributeError) as exc:  # non-glibc libc: the env knobs still apply
        log.warning("telemetry memory limits not applied: %s", exc)


def rss_bytes() -> int:
    """This process's resident set size."""
    return int(psutil.Process().memory_info().rss)


def _rss_detail() -> str:
    """Anon/file split of RSS on Linux (shared library pages count as file), for the cap message."""
    try:
        status = Path("/proc/self/status").read_text(encoding="ascii").splitlines()
    except OSError:
        return ""
    kib = {k: v.split()[0] for k, _, v in (ln.partition(":") for ln in status) if k in ("RssAnon", "RssFile")}
    return f" (anon {int(kib['RssAnon']) >> 10} MiB, file {int(kib['RssFile']) >> 10} MiB)" if len(kib) == 2 else ""


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
    tails = [BusTail(run_dir / BUS_FILE, batch_bytes=TAIL_BYTES)]
    if tier is TelemetryTier.T2:
        tails.append(BusTail(run_dir / GOD_FILE, god=True, batch_bytes=TAIL_BYTES))
    try:
        sink.start()
        while True:
            alive = is_live() if live else False
            moved = False
            for tail in tails:
                before = tail.offset
                recs = [rec for ev in tail.poll() for rec in mapper.map(ev)]
                if recs:
                    sink.log_batch(recs)
                moved = moved or tail.offset != before or tail.restarted
                tail.restarted = False
            used = rss()
            if used > RSS_LIMIT:
                log.error("telemetry RSS %d MiB%s exceeds limit; exiting", used >> 20, _rss_detail())
                return 1
            if moved:
                continue  # drain: more may be waiting beyond one batch
            if not alive:
                return 0
            sleep(poll_s)
    finally:
        sink.close()
