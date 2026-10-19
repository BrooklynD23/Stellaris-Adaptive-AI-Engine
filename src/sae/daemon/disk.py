"""Runs-dir size warning (Wave 3 finding N4). Read-only: nothing is ever deleted."""

from __future__ import annotations

import os
from pathlib import Path

# ponytail: module constant, no C8 key fits; add `paths.runs_dir_warn_bytes` by proposal (or a retention
# policy with explicit user opt-in) if users need to tune it.
RUNS_DIR_WARN_BYTES = 5 * 1024**3


def dir_bytes(root: Path) -> int:
    """Total size of regular files under ``root`` (iterative scandir; unreadable entries count as 0)."""
    total, stack = 0, [root]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total
