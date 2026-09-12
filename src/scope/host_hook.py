"""Invocation-local Codex/Claude adapters; all permission failures abstain.

Event fields/output checked against official hook references on September 12,
2026: https://learn.chatgpt.com/docs/hooks and
https://code.claude.com/docs/en/hooks . Claude agent_type alone can identify the
main custom agent; agent_id identifies subagent calls. No transcript guessing.
"""

import argparse
from contextlib import redirect_stdout
from importlib.resources import files
import io
import json
import sys

from . import host_session


def guidance(launch=None):
    """Load selected bundled guidance without reads of user-authored excerpts."""
    launch = host_session.current_launch() if launch is None else launch
    if not isinstance(launch, dict):
        raise host_session.HostSessionError("startup guidance requires a launch")
    parts = ["Scope is configured for this launch. Run scope ready before dependent work. "
             "If native startup or human review is unavailable, stop dependent work and report the error. "
             "Use the registered native session; never copy a parent or another session ID. "
             "Startup and review readiness do not prove any permission request was intercepted. "
             "Keep the host's existing sandbox, approvals, network limits and hook trust."]
    for enabled, name in ((launch["permissions"], "scope-permissions"),
                          (launch["understanding"], "scope-understand")):
        if enabled:
            with files("scope").joinpath("skills", name, "SKILL.md").open("rb") as stream:
                payload = stream.read(128 * 1024 + 1)
            if len(payload) > 128 * 1024:
                raise ValueError("bundled startup guidance is too large")
            parts.append(payload.decode("utf-8", errors="strict"))
    return "\n\n".join(parts)


def _permission(event):
    """Reuse the existing hook engine without a subprocess or a second policy."""
    from . import hook

    previous = sys.stdin
    output = io.StringIO()
    try:
        sys.stdin = io.StringIO(json.dumps(event, allow_nan=False))
        with redirect_stdout(output):
            hook.main([])
    finally:
        sys.stdin = previous
    return output.getvalue()


def process(event, *, host):
    """Process one already-decoded host event; caller owns the failure boundary."""
    from . import ipc, log, wire

    if not isinstance(event, dict) or host not in {"codex", "claude"}:
        raise ValueError("invalid host event")
    # Re-parse programmatic input through the same bounded strict JSON contract.
    raw = json.dumps(event, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(raw) > wire.MAX_INPUT_BYTES:
        raise ValueError("host event exceeds input bound")
    kind = event.get("hook_event_name")
    if kind not in {"SessionStart", "PermissionRequest", "Stop", "SessionEnd"}:
        return ""
    launch = host_session.current_launch()
    if launch is None or launch["host"] != host:
        raise host_session.HostSessionError("hook is not running in its owning host launch")
    native = wire._text(event.get("session_id"))
    cwd = wire._text(event.get("cwd"))
    agent_id = wire._text(event.get("agent_id"), optional=True)
    agent_type = wire._text(event.get("agent_type"), optional=True)
    if kind == "SessionStart":
        # Read/validate resources before recording startup, so failures remain
        # actionable without claiming the host received usable guidance.
        context = guidance(launch)
        host_session.register(native, host=host, cwd=cwd, source=event.get("source"),
                              agent_id=agent_id, agent_type=agent_type)
        return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                  "additionalContext": context}}, separators=(",", ":"))
    host_session.require_session(native, host=host, cwd=cwd)
    if kind == "PermissionRequest":
        if not launch["permissions"]:
            return ""
        request = wire.parse_request(raw)
        host_session.observe_permission(native, host=host, cwd=cwd)
        output = _permission(request.original)
        # A native close during a human wait invalidates even a prepared answer.
        host_session.require_session(native, host=host, cwd=cwd)
        return output
    if agent_id is not None:
        return ""  # Child lifecycle cannot stop or close the main launch.
    if kind == "Stop":
        log.append(native, "stop", host=host, launch_id=launch["launch_id"], status="turn_stopped")
        ipc.exchange({"kind": "stop", "session_id": native}, timeout=0.25)
    else:
        # Cancel grants/questions before recording native closure. No receipt or
        # audit runs inside the hook; launch-agent finalizes the owned receipt.
        ipc.exchange({"kind": "shutdown"}, timeout=0.5)
        host_session.close_session(native, host=host, cwd=cwd, inferred=False)
    return ""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid host-hook arguments")


def main(argv=None):
    try:
        parser = _Parser(prog="scope host-hook", description=__doc__, allow_abbrev=False)
        parser.add_argument("--host", choices=("codex", "claude"), required=True)
        args = parser.parse_args(argv)
        from . import ipc, wire
        raw = getattr(sys.stdin, "buffer", sys.stdin).read(wire.MAX_INPUT_BYTES + 1)
        raw = raw.encode("utf-8") if isinstance(raw, str) else raw
        if len(raw) > wire.MAX_INPUT_BYTES:
            raise ValueError("host event exceeds input bound")
        output = process(ipc._json_object(raw), host=args.host)
        if output:
            sys.stdout.write(output)
            sys.stdout.flush()
    except SystemExit:
        pass
    except BaseException:
        try:
            print("scope host-hook: unavailable or invalid native launch event; no permission decision", file=sys.stderr)
        except BaseException:
            pass
    return 0
