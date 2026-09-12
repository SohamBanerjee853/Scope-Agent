"""Lazy dispatch. Missing milestones fail clearly without pretending to work."""

import argparse
import importlib
import sys

from . import __version__

# Each module exposes main(argv) -> int | None. Multiplexed modules receive the
# command name as argv[0]; single-command modules receive only trailing args.
ROUTES = {
    "hook": ("hook", False),
    "stop": ("stop_hook", False),
    "session-end": ("session_end_hook", False),
    "install": ("install", False),
    "watch": ("watch", False),
    "propose": ("propose", False),
    "install-skill": ("install", True),
    "receipt": ("receipt", False),
    "smoke": ("smoke", False),
    **{name: ("learning_cli", True) for name in (
        "start", "checkpoint", "check", "knowledge", "next", "revoke", "skill"
    )},
    "demo": ("demo", False),
    "demo-adapter": ("demo_agent", False),
    **{name: ("launch", True) for name in ("codex", "claude", "ready", "launch-agent")},
    "host-hook": ("host_hook", False),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scope", description="Scope foundation: workflows are not implemented yet."
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("command", choices=ROUTES, nargs="?")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    module_name, include_command = ROUTES[args.command]
    qualified_name = f"scope.{module_name}"
    try:
        module = importlib.import_module(qualified_name)
    except ModuleNotFoundError as exc:
        if exc.name != qualified_name:
            raise  # An installed feature's broken dependency is not a missing milestone.
        print(f"scope {args.command}: not implemented in the F0 foundation", file=sys.stderr)
        return 1
    forwarded = [args.command, *args.args] if include_command else args.args
    result = module.main(forwarded)
    return 0 if result is None else result
