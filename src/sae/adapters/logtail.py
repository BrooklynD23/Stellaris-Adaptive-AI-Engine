"""Incremental ``game.log`` reader and C1 line parsing (T2 U1)."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from sae.adapters import health as H
from sae.adapters.health import HealthFn, HealthGate
from sae.contracts.c1_log import LogLineError, LogRecord, parse_log_line

HEAD_BYTES = 512
TAIL_BYTES = 64
MAX_PARTIAL = 8192
MAX_READ = 1 << 20  # per poll; the remainder is read on the next poll

Opener = Callable[[Path, str], BinaryIO]


def _open(path: Path, mode: str) -> BinaryIO:
    return open(path, mode)  # type: ignore[return-value]


class LogTailer:
    """Returns complete lines appended to a file since the last :meth:`poll`.

    Opens, reads and closes on every poll. Never raises.
    """

    def __init__(
        self,
        path: Path,
        *,
        opener: Opener = _open,
         health: HealthFn = H.log_health,
        gate: HealthGate | None = None,
    ) -> None:
        self._gate = H.new_gate() if gate is None else gate
        self._path = path
        self._open = opener
        self._health = health
        self._offset = 0
        self._head = b""
        self._tail = b""  # last TAIL_BYTES before _offset: catches rotation with an identical head
        self._buf = b""
        self._skip = False  # discarding the rest of an over-long line

    def _reset(self, why: str) -> None:
        self._offset, self._head, self._tail, self._buf, self._skip = 0, b"", b"", b"", False
        H.reconnect(self._health, self._gate, why)

    def poll(self) -> list[str]:
        """Complete decoded lines since the last poll (A-16 UNVERIFIED: UTF-8, else replacement chars)."""
        try:
            data = self._read()
        except OSError as exc:
            cause = "missing" if isinstance(exc, FileNotFoundError) else type(exc).__name__
            H.reconnect(self._health, self._gate, cause, f"{cause}: {exc}" if cause != "missing" else cause)
            return []
        H.recovered(self._gate)
        return self._split(data)

    def _read(self) -> bytes:
        with self._open(self._path, "rb") as f:
            head = f.read(HEAD_BYTES)
            size = f.seek(0, 2)
            n = len(self._head)
            # A-15 UNVERIFIED: rotation/truncation shows as a shrunk file or a changed head; M0 rotates a log.
            if size < self._offset or head[:n] != self._head:
                self._reset("truncated_or_replaced")
            elif self._tail:
                f.seek(self._offset - len(self._tail))
                if f.read(len(self._tail)) != self._tail:
                    self._reset("truncated_or_replaced")
            if len(self._head) < HEAD_BYTES:
                self._head = head
            f.seek(self._offset)
            data = f.read(min(max(size - self._offset, 0), MAX_READ))
        self._offset += len(data)
        self._tail = (self._tail + data)[-TAIL_BYTES:]
        return data

    def _split(self, data: bytes) -> list[str]:
        if self._skip:
            _, nl, data = data.partition(b"\n")
            if not nl:
                return []
            self._skip = False
        parts = (self._buf + data).split(b"\n")
        self._buf = parts.pop()
        if len(self._buf) > MAX_PARTIAL:
            self._buf, self._skip = b"", True
            H.parse_error(self._health, self._gate, None, "too_long", f"partial line over {MAX_PARTIAL} bytes dropped")
        return [(p[:-1] if p.endswith(b"\r") else p).decode("utf-8", errors="replace") for p in parts]


_DATE_EXACT = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")


def _redact(line: str) -> str:
    """Log-safe excerpt (C1 section 5, proposal 012).

    If the line from its ``SAE|`` marker contains ``PLYR`` anywhere, only ``SAE|v1|PLYR`` is kept, plus
    ``|<date>`` when the fourth ``|``-field is exactly ``YYYY.MM.DD``; no other byte of the line.
    """
    start = line.find("SAE|")
    text = line[start:] if start >= 0 else line
    if "PLYR" in text:
        fields = text.split("|", 4)
        date = fields[3] if len(fields) >= 4 else ""
        return "SAE|v1|PLYR" + (f"|{date}" if _DATE_EXACT.match(date) else "")
    return text[:200]


def parse_lines(
    lines: list[str], health: HealthFn, month_key: object, gate: HealthGate
) -> list[LogRecord]:
    """Parse C1 lines; noise is skipped and errors go to ``health``. Never raises."""
    out: list[LogRecord] = []
    for line in lines:
        try:
            rec = parse_log_line(line)
        except LogLineError as exc:
            if exc.code == "version_skew":
                H.version_skew(health, gate, "version != v1" if "PLYR" in line else exc.detail)
            else:
                H.parse_error(health, gate, month_key, exc.code, _redact(line))
            continue
        if rec is not None:
            out.append(rec)
    return out
