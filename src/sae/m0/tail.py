"""M0-4 timestamping tailer: stamps every ``SAE|`` line of game.log with the wall time it became visible.

Flush latency (A-07, UNVERIFIED) cannot be measured exactly without the game's tick time, so the automatic
verdict detects batching: a buffered log delivers several game months in one poll. The maintainer adds the
paused-on-the-1st observation with ``sae m0 record M0-4``.
"""

from __future__ import annotations

import itertools
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from sae.adapters.logtail import LogTailer
from sae.contracts.c1_log import HeartbeatRecord, LogLineError, parse_log_line
from sae.m0.evidence import Verdict

MIN_HB = 3
#: Batching only proves buffering when a game month takes at least this long in wall time.
MIN_MONTH_S = 1.0


@dataclass(frozen=True)
class Arrival:
    """One heartbeat as seen by the tailer."""

    t: float  # wall seconds since the tail started
    poll: int
    date: str


def analyze(arrivals: list[Arrival]) -> Verdict:
    """Judge batching from heartbeat arrivals; also report seconds per game month (M0-12 input)."""
    if len(arrivals) < MIN_HB:
        return Verdict("inconclusive", {"hb": len(arrivals)}, (f"only {len(arrivals)} HB seen; need {MIN_HB}+",))
    gaps = [b.t - a.t for a, b in itertools.pairwise(arrivals)]
    # Mean over the span, not the median gap: buffered flushes make most gaps 0.
    months = len({a.date for a in arrivals}) - 1
    s_month = (arrivals[-1].t - arrivals[0].t) / months if months > 0 else 0.0
    per_poll: dict[int, set[str]] = {}
    for a in arrivals:
        per_poll.setdefault(a.poll, set()).add(a.date)
    batched = sorted(p for p, dates in per_poll.items() if len(dates) > 1)
    evidence: dict[str, object] = {
        "hb": len(arrivals),
        "s_per_month": round(s_month, 3),
        "max_gap_s": round(max(gaps), 3),
        "batched_polls": len(batched),
    }
    if batched and s_month >= MIN_MONTH_S:
        return Verdict("fail", evidence, (f"{len(batched)} poll(s) delivered 2+ months at once: log is buffered",))
    notes = [f"no batching; median {s_month:.2f} s/month, max gap {max(gaps):.2f} s"]
    if s_month < MIN_MONTH_S:
        notes.append("months faster than 1 s: rerun at a slower speed for a meaningful batching test")
        return Verdict("inconclusive", evidence, tuple(notes))
    return Verdict("pass", evidence, tuple(notes))


def run_tail(
    game_log: Path,
    out: TextIO,
    seconds: float,
    *,
    poll_s: float = 0.1,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    echo: Callable[[str], None] = print,
    tailer: LogTailer | None = None,
) -> Verdict:
    """Tail ``game_log`` for ``seconds``; write one JSON line per SAE record to ``out``; return the M0-4 verdict.

    Lines already in the file when the tail starts are skipped (they would look like one big batch). Ctrl+C
    stops early and still returns the verdict for what was seen.
    """
    source = tailer or LogTailer(game_log)
    arrivals: list[Arrival] = []
    try:
        while source.poll():  # discard history (the tailer reads at most 1 MiB per poll)
            pass
        start = clock()
        _tail_loop(source, out, seconds, start, arrivals, poll_s=poll_s, clock=clock, sleep=sleep, echo=echo)
    except KeyboardInterrupt:
        echo("stopped by Ctrl+C")
    return analyze(arrivals)


def _tail_loop(
    source: LogTailer,
    out: TextIO,
    seconds: float,
    start: float,
    arrivals: list[Arrival],
    *,
    poll_s: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    echo: Callable[[str], None],
) -> None:
    poll = 0
    while (now := clock() - start) < seconds:
        poll += 1
        for line in source.poll():
            try:
                record = parse_log_line(line)
            except LogLineError as exc:
                echo(f"{now:8.2f}s  malformed ({exc.code}): {line[:120]}")
                continue
            if record is None:
                continue
            row = {"t": round(now, 3), "poll": poll, "type": record.type, "date": str(record.date)}
            out.write(json.dumps(row) + "\n")
            echo(f"{now:8.2f}s  {record.type:5} {record.date}")
            if isinstance(record, HeartbeatRecord):
                arrivals.append(Arrival(now, poll, str(record.date)))
        sleep(poll_s)
