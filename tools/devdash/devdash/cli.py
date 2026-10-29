"""Command-line interface for the dev dashboard. Stdlib-only, so hooks can run it under a bare ``python3``.

Implemented now: ``emit``, ``enable``, ``disable``, ``task``, ``serve``, ``ensure`` and ``stop``. A later task adds
``hook`` by registering more subparsers in :func:`_add_commands`. Each subcommand sets a ``handler`` default.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from devdash import REPO, dev_dir, ensure, events, tasks

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


def _cmd_task(args: argparse.Namespace) -> int:
    """Create or update a task in the board file, then emit an event (a no-op when the dashboard is disabled)."""
    path = Path(args.file) if args.file else tasks.tasks_file(REPO)
    try:
        task = tasks.set_task(
            path,
            args.id,
            status=args.status,
            title=args.title,
            note=args.note,
            tag=args.tag,
            link=args.link,
            blocked_on=args.blocked_on,
        )
    except (ValueError, OSError) as exc:
        print(f"devdash: {exc}", file=sys.stderr)
        return 1
    events.emit("orchestrator", "info", f"task {args.id} \u2192 {task['status']}", ref=args.id)
    return 0


def _port(text: str) -> int:
    """argparse type: a TCP port in 0..65535 (0 = ephemeral, for tests)."""
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected a port number, got {text!r}") from exc
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError(f"expected a port in 0..65535, got {value}")
    return value


def _real_port(text: str) -> int:
    """argparse type: a TCP port in 1..65535."""
    value = _port(text)
    if value == 0:
        raise argparse.ArgumentTypeError("expected a port in 1..65535")
    return value


def _cmd_serve(args: argparse.Namespace) -> int:
    """Run the dashboard server in the foreground. Imported lazily: it needs the repo venv, hooks never load it."""
    game_tools = str(REPO / "tools" / "game")  # the server imports the gate registry (tools/game/gates.py)
    if game_tools not in sys.path:
        sys.path.append(game_tools)
    from devdash import server

    port: int = ensure.default_port() if args.port is None else args.port
    return server.run(REPO, dev_dir(), port)


def _cmd_ensure(args: argparse.Namespace) -> int:
    """Start the server if it is not running and print its URL. Exit 1 when the port is taken or startup fails."""
    port: int = ensure.default_port() if args.port is None else args.port
    try:
        print(ensure.ensure_running(REPO, dev_dir(), port))
    except RuntimeError as exc:
        print(f"devdash: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_stop(_args: argparse.Namespace) -> int:
    """Stop the running dashboard server. Exit 0 either way; the message says what happened."""
    print("devdash: stopped" if ensure.stop(dev_dir()) else "devdash: no running server")
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

    task = commands.add_parser("task", help="create or update a task on the board (docs/dev/tasks.json)")
    task.add_argument("id", type=_nonempty, help="task id (lowercase-hyphen)")
    task.add_argument("--status", default=None, choices=tasks.STATUSES, help="task status (required to create)")
    task.add_argument("--title", default=None, type=_nonempty, help="task title (required to create)")
    task.add_argument("--note", default=None, help="free-text note")
    task.add_argument("--tag", default=None, help="grouping tag")
    task.add_argument("--link", default=None, help="URL, for example a pull request")
    task.add_argument("--blocked-on", default=None, dest="blocked_on", help="what the task waits for")
    task.add_argument("--file", default=None, help="board file (default: docs/dev/tasks.json; for tests)")
    task.set_defaults(handler=_cmd_task)

    serve = commands.add_parser("serve", help="run the dashboard server in the foreground")
    serve.add_argument("--port", type=_port, default=None, help="TCP port (default: $SAE_DEVDASH_PORT or 8765)")
    serve.set_defaults(handler=_cmd_serve)

    ens = commands.add_parser("ensure", help="start the dashboard server if needed and print its URL")
    ens.add_argument("--port", type=_real_port, default=None, help="TCP port (default: $SAE_DEVDASH_PORT or 8765)")
    ens.set_defaults(handler=_cmd_ensure)

    stop = commands.add_parser("stop", help="stop the running dashboard server")
    stop.set_defaults(handler=_cmd_stop)

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
