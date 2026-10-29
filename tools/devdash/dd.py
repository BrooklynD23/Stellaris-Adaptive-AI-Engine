"""Entry point for the SAE dev dashboard: ``python3 tools/devdash/dd.py <command> ...``.

Stdlib-only, so hooks can run it under a bare ``python3``. This file's own directory goes to the front of
``sys.path`` so that the ``devdash`` package resolves here and nowhere else. The exit code is the return value of
``devdash.cli.main``.

Fast path: ``emit`` and ``hook`` are no-ops while ``<dev>/enabled`` is missing, so they exit before any ``devdash``
import (package lookups on drvfs are slow). The dev directory is ``$SAE_DEVDASH_DIR`` or ``<repo>/.sae-dev``.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_FAST_COMMANDS = ("emit", "hook")


def _disabled_noop(argv: list[str]) -> bool:
    """Return True when ``argv`` is an ``emit``/``hook`` call and the dev log is switched off.

    Intentional: while disabled, bad arguments and ``--help`` for these commands also exit 0 silently. Hooks must
    never fail, and argument parsing would need the ``devdash`` import this path exists to avoid.
    """
    if not argv or argv[0] not in _FAST_COMMANDS:
        return False
    dev = os.environ.get("SAE_DEVDASH_DIR") or os.path.join(_HERE, os.pardir, os.pardir, ".sae-dev")
    return not os.path.isfile(os.path.join(dev, "enabled"))


def run(argv: list[str] | None = None) -> int:
    """Put this directory first on ``sys.path``, then run ``devdash.cli.main`` with ``argv``."""
    args = sys.argv[1:] if argv is None else argv
    if _disabled_noop(args):
        return 0
    if sys.path[:1] != [_HERE]:
        sys.path.insert(0, _HERE)
    from devdash.cli import main

    return main(args)


if __name__ == "__main__":
    sys.exit(run())
