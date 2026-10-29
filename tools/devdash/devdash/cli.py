"""Command-line interface for the dev dashboard. Stdlib-only, so hooks can run it under a bare ``python3``.

Implemented now: ``emit``, ``enable`` and ``disable``. Later tasks add ``task``, ``serve``, ``ensure``, ``stop`` and
``hook`` by registering more subparsers in :func:`_add_commands`. Each subcommand sets a ``handler`` default.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from devdash import dev_dir, events

Handler = Callable[[argparse.Namespace], int]


def _nonempty(text: str) -> str:
    """argparse type: reject empty or blank text."""
    if not text.strip():
        raise argparse.ArgumentTypeError("must not be empty")
    return text


def _percent(text: str) -> int:
    """argparse type: an integer in 0..100."""
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an integer in 0..100, got {text!r}") from exc
    if not 0 <= value <= 100:
        raise argparse.ArgumentTypeError(f"expected a value in 0..100, got {value}")
    return value


def _cmd_emit(args: argparse.Namespace) -> int:
    """Append one event. Exit 0 when written or when the log is disabled (a no-op); 1 when the write fails."""
    dev = dev_dir()
    if not events.is_enabled(dev):
        print("devdash: not enabled (run 'dd.py enable'); nothing written", file=sys.stderr)
        return 0
    if not events.emit(args.source, args.kind, args.msg, ref=args.ref, pct=args.pct, dev=dev):
        print("devdash: event not written (see the warning above)", file=sys.stderr)
        return 1
    return 0


def _cmd_enable(_args: argparse.Namespace) -> int:
    """Switch the dev log on by creating the enabled flag."""
    dev = dev_dir()
    try:
        events.enable(dev)
    except OSError as exc:
        print(f"devdash: cannot enable {dev}: {exc}", file=sys.stderr)
        return 1
    print(f"devdash: enabled ({dev / events.ENABLED_FLAG})")
    return 0


def _cmd_disable(_args: argparse.Namespace) -> int:
    """Switch the dev log off by removing the enabled flag."""
    dev = dev_dir()
    try:
        events.disable(dev)
    except OSError as exc:
        print(f"devdash: cannot disable {dev}: {exc}", file=sys.stderr)
        return 1
    print("devdash: disabled")
    return 0


def _add_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register every subcommand on the parser's subparsers action."""
    emit = commands.add_parser("emit", help="append one event to the dev event log")
    emit.add_argument("--source", default="orchestrator", type=_nonempty, help="writer name (default: orchestrator)")
    emit.add_argument("--kind", default="info", choices=events.KINDS, help="event kind (default: info)")
    emit.add_argument("--ref", default=None, help="task or gate id the event belongs to")
    emit.add_argument("--pct", default=None, type=_percent, help="progress 0..100")
    emit.add_argument("msg", help="one line of text (redacted and cut to 200 characters)")
    emit.set_defaults(handler=_cmd_emit)

    enable = commands.add_parser("enable", help="switch the dev event log on (creates .sae-dev/enabled)")
    enable.set_defaults(handler=_cmd_enable)

    disable = commands.add_parser("disable", help="switch the dev event log off (removes .sae-dev/enabled)")
    disable.set_defaults(handler=_cmd_disable)


def build_parser() -> argparse.ArgumentParser:
    """Return the top-level parser with every subcommand registered."""
    parser = argparse.ArgumentParser(
        prog="dd.py",
        description="SAE dev dashboard: event log and state. Dev tooling only; never imported by src/sae.",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    _add_commands(commands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI on ``argv`` (default: ``sys.argv[1:]``) and return the exit code.

    argparse usage errors (2) and ``--help`` (0) come back as return codes instead of ``SystemExit``.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(None if argv is None else list(argv))
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    handler: Handler = args.handler
    return handler(args)
