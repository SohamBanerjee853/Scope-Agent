"""Append-only SessionEnd hook. No network, receipt or audit work runs here."""

import sys


def main(argv=None):
    try:
        if argv:
            return 0
        from .wire import read_event
        from . import log

        event = read_event(getattr(sys.stdin, "buffer", sys.stdin), "SessionEnd")
        session_id = event.pop("session_id")
        log.append(session_id, "session_end", status="ended", **event)
    except BaseException:
        pass
    return 0
