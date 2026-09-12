"""Append local turn status and send a short best-effort watcher notice."""

import sys


def main(argv=None):
    try:
        if argv:
            return 0
        from .wire import read_event
        from . import log

        event = read_event(getattr(sys.stdin, "buffer", sys.stdin), "Stop")
        session_id = event.pop("session_id")
        log.append(session_id, "stop", status="turn_stopped", **event)
        from .ipc import exchange

        exchange({"kind": "stop", "session_id": session_id}, timeout=0.25)
    except BaseException:
        pass
    return 0
