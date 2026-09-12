"""Submit a bounded proposal to the watcher; submission never approves it."""

import argparse
import os
from pathlib import Path
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scope propose", description=__doc__, allow_abbrev=False)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--command", action="append", required=True, dest="commands")
    parser.add_argument("--domain", action="append", default=[], dest="domains")
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--session")
    parser.add_argument("--agent")
    args = parser.parse_args(argv)
    from .grants import validate_card
    from .ipc import exchange
    try:
        if "SCOPE_LAUNCH_ID" in os.environ or "SCOPE_HOST" in os.environ:
            from . import host_session

            launch = host_session.current_launch()
            if launch is None or not launch["permissions"]:
                raise ValueError("permission proposals are disabled for this launch")
            session = host_session.require_session(args.session, cwd=Path.cwd())
        else:
            session = args.session if args.session is not None else os.environ.get("CODEX_THREAD_ID")
            if not session or not session.strip():
                parser.error("--session or CODEX_THREAD_ID is required outside a launch")
        card = validate_card({"summary": args.summary, "commands": args.commands,
                              "domains": args.domains, "budget": args.budget})
    except Exception:
        print("scope propose: invalid card or native launch identity unavailable", file=sys.stderr)
        return 1
    reply = exchange({"kind": "proposal", "session_id": session, "agent_id": args.agent,
                      "cwd": str(Path.cwd()), "card": card.to_dict()})
    if not isinstance(reply, dict) or reply.get("accepted") is not True:
        print("scope propose: watcher unavailable or proposal rejected; no approval recorded", file=sys.stderr)
        return 1
    print("Proposal submitted for human review. It is not an approval.")
    return 0
