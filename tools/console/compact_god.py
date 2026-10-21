"""CLI: ``python tools/console/compact_god.py RUN_DIR [--keep-every-months N]`` (offline; refuses a live run)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sae.bus import CompactRefused, compact_god


def main(argv: list[str] | None = None) -> int:
    """Compact ``RUN_DIR/bus.god.jsonl``; exit 0 ok, 1 refused or failed."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--keep-every-months", type=int, default=12)
    args = ap.parse_args(argv)
    try:
        n = compact_god(args.run_dir, keep_every_months=args.keep_every_months)
    except (CompactRefused, OSError, ValueError) as exc:
        print(f"compact_god: {exc}", file=sys.stderr)
        return 1
    print(f"kept {n} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
