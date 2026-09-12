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
    parser.add_argument("--session", default=os.environ.get("CODEX_THREAD_ID"))
    parser.add_argument("--agent")
    args = parser.parse_args(argv)
    if not args.session or not args.session.strip():
        parser.error("--session or CODEX_THREAD_ID is required")
    from .grants import validate_card
    from .ipc import exchange
    try:
        card = validate_card({"summary": args.summary, "commands": args.commands,
                              "domains": args.domains, "budget": args.budget})
    except (TypeError, ValueError):
        print("scope propose: invalid bounded card", file=sys.stderr)
        return 1
    reply = exchange({"kind": "proposal", "session_id": args.session, "agent_id": args.agent,
                      "cwd": str(Path.cwd()), "card": card.to_dict()})
    if not isinstance(reply, dict) or reply.get("accepted") is not True:
        print("scope propose: watcher unavailable or proposal rejected; no approval recorded", file=sys.stderr)
        return 1
    print("Proposal submitted for human review. It is not an approval.")
    return 0
