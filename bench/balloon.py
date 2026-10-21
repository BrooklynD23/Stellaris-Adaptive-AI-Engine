"""R-4 balloon: allocate RAM until available memory drops to a target, then hold until Ctrl-C.

Touches only its own memory. It never looks at, signals or launches any other process (not the game).
Usage: ``python3 bench/balloon.py --leave-gb 1.2 [--dry-run]``.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass

import psutil

GB = 1024**3
CHUNK = 64 * 1024 * 1024
PAGE = 4096
MIN_LEAVE_GB = 0.8


@dataclass(frozen=True)
class Plan:
    """What the balloon would do."""

    available_bytes: int
    leave_bytes: int
    chunks: int


def plan(available_bytes: int, leave_gb: float) -> Plan:
    """Pure planning: chunks of 64 MiB to allocate so ``available`` ends at or just below ``leave_gb``.

    Raises ValueError when ``leave_gb`` < 0.8 (would starve the machine).
    """
    if leave_gb < MIN_LEAVE_GB:
        raise ValueError(f"refusing: --leave-gb {leave_gb} is below {MIN_LEAVE_GB}")
    leave = int(leave_gb * GB)
    excess = max(0, available_bytes - leave)
    return Plan(available_bytes, leave, -(-excess // CHUNK))  # ceil


def keep_going(held: int, chunks: int, available: int, leave_bytes: int) -> bool:
    """Pure loop guard: more chunks planned, above the target, and never below the hard floor."""
    return held < chunks and available > leave_bytes and available >= int(MIN_LEAVE_GB * GB)


def main(argv: list[str] | None = None) -> int:
    """CLI entry."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--leave-gb", type=float, default=1.2)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        p = plan(psutil.virtual_memory().available, args.leave_gb)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"available {p.available_bytes / GB:.2f} GB, leave {p.leave_bytes / GB:.2f} GB, "
          f"allocate about {p.chunks} x 64 MiB")
    if args.dry_run:
        return 0
    held: list[bytearray] = []
    try:
        while keep_going(len(held), p.chunks, psutil.virtual_memory().available, p.leave_bytes):
            buf = bytearray(CHUNK)
            for i in range(0, CHUNK, PAGE):
                buf[i] = 1  # touch every page so it is really resident
            held.append(buf)
        free = psutil.virtual_memory().available / GB
        print(f"holding {len(held) * 64} MiB; available {free:.2f} GB. Ctrl-C to release.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("released")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
