"""Lazy command dispatch. Missing milestones fail explicitly."""

import argparse
import importlib
import sys

from . import __version__

LEARNING_COMMANDS = {"start", "checkpoint", "check", "knowledge", "next", "revoke", "skill"}
ROUTES = {
    "hook": "hook", "stop": "stop_hook", "session-end": "session_end_hook",
    "install": "install", "watch": "watch", "propose": "propose",
    "install-skill": "install", "receipt": "receipt", "smoke": "smoke",
    "demo": "demo", "demo-adapter": "demo_agent",
    "codex": "launch", "claude": "launch", "ready": "launch",
    "launch-agent": "launch", "host-hook": "host_hook",
    **dict.fromkeys(LEARNING_COMMANDS, "learning_cli"),
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="scope", description="Local permission and understanding workflows")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("command", choices=sorted(ROUTES), nargs="?")
    if not args or args[0].startswith("-"):
        parser.parse_args(args)
        parser.print_help()
        return 0
    parsed = parser.parse_args(args[:1])
    module_name = f"scope.{ROUTES[parsed.command]}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
        print(f"scope {parsed.command}: not implemented in this milestone", file=sys.stderr)
        return 2
    command_args = args[1:]
    if parsed.command in LEARNING_COMMANDS or ROUTES[parsed.command] == "launch" or parsed.command == "install-skill":
        command_args = [parsed.command, *command_args]
    return module.main(command_args)
