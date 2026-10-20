"""SAE command line entry point (owner: architect). Dispatches to per-team CLI modules."""

from __future__ import annotations

import argparse
import importlib
import sys

from sae import __version__

# subcommand -> (module, owner team)
_COMMANDS: dict[str, tuple[str, str]] = {
    "daemon": ("sae.daemon.cli", "T4"),
    "replay": ("sae.adapters.cli", "T2"),
    "synth": ("sae.adapters.cli", "T2"),
    "ctl": ("sae.control.cli", "T6"),
    "telemetry": ("sae.telemetry.cli", "T6"),
    "bench": ("sae.bench.cli", "T7"),
    "m0": ("sae.m0.cli", "T8"),
    "profile": ("sae.profile.cli", "T9"),
    "play": ("sae.profile.launcher", "T9"),
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sae", description="Stellaris Adaptive Engine")
    parser.add_argument("--version", action="version", version=f"sae {__version__}")
    parser.add_argument("command", nargs="?", choices=sorted(_COMMANDS), help="subcommand to run")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the ``sae`` CLI and return a process exit code.

    Args:
        argv: Arguments excluding the program name; defaults to ``sys.argv[1:]``.

    Returns:
        The subcommand's exit code, or 2 if no/unavailable subcommand.
    """
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in _COMMANDS:
        parser = _build_parser()
        parser.parse_args(args)  # handles --version / --help / bad choice (exits)
        parser.print_help()
        return 2
    cmd, rest = args[0], args[1:]
    module_name, team = _COMMANDS[cmd]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name is None or not module_name.startswith(exc.name):
            raise
        print(f"sae {cmd}: not available yet (owner {team})", file=sys.stderr)
        return 2
    entry = getattr(module, "main", None)
    if entry is None:
        print(f"sae {cmd}: not available yet (owner {team})", file=sys.stderr)
        return 2
    return int(entry(cmd, rest))


if __name__ == "__main__":
    raise SystemExit(main())
