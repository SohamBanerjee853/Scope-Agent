"""Bounded baseline PermissionRequest parsing and exact permission envelopes.

This module neither executes input nor makes permission decisions. Unknown fields
are retained as data for later correlation, never copied into an output envelope.
"""

from dataclasses import dataclass
import json
import math
from typing import BinaryIO, TextIO


MAX_INPUT_BYTES = 128 * 1024
MAX_MESSAGE_CHARS = 4096
_SHELLS = {
    "bash": "bash", "sh": "bash", "zsh": "bash", "posix": "bash",
    "powershell": "powershell", "pwsh": "powershell",
}


@dataclass(frozen=True)
class PermissionRequest:
    """Validated lexical inputs; ``original`` remains untrusted request data."""

    session_id: str
    cwd: str
    tool_name: str
    command: str
    shell: str
    description: str | None
    original: dict
    turn_id: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    transcript_path: str | None = None
    agent_id: str | None = None
    agent_type: str | None = None


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("nonfinite JSON number")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite JSON number")
    return number


def _text(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("invalid string field")
    # Escaped lone surrogates are accepted by json.loads, but are not UTF-8 text.
    value.encode("utf-8", errors="strict")
    if not optional and not value.strip():
        raise ValueError("empty string field")
    return value


def parse_request(payload: bytes | str) -> PermissionRequest:
    """Parse one strict UTF-8 JSON request, raising on unsupported/invalid input.

    The baseline permits an omitted ``hook_event_name`` because the command is
    itself the PermissionRequest entry point. An explicit other event is rejected.
    No Codex-only metadata is required. Input is limited to 128 KiB including
    whitespace, and duplicate keys/nonstandard JSON constants are rejected.
    """
    if isinstance(payload, str):
        data = payload.encode("utf-8", errors="strict")
    elif isinstance(payload, bytes):
        data = payload
    else:
        raise ValueError("expected UTF-8 JSON")
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("request is too large")
    value = json.loads(
        data.decode("utf-8", errors="strict"),
        object_pairs_hook=_object,
        parse_constant=_constant,
        parse_float=_float,
    )
    if not isinstance(value, dict):
        raise ValueError("request must be an object")
    if "hook_event_name" in value and value["hook_event_name"] != "PermissionRequest":
        raise ValueError("unsupported hook event")
    tool_name = value.get("tool_name")
    if tool_name not in ("Bash", "PowerShell"):
        raise ValueError("unsupported tool")
    tool_input = value.get("tool_input")
    if not isinstance(tool_input, dict):
        raise ValueError("tool_input must be an object")
    default_shell = "bash" if tool_name == "Bash" else "powershell"
    raw_shell = tool_input.get("shell", default_shell)
    if not isinstance(raw_shell, str):
        raise ValueError("invalid shell")
    normalized_shell = _SHELLS.get(raw_shell.lower().removesuffix(".exe"))
    if normalized_shell is None or normalized_shell != default_shell:
        raise ValueError("unsupported or conflicting shell")
    metadata = {
        name: _text(value.get(name), optional=True)
        for name in (
            "turn_id", "model", "permission_mode", "transcript_path",
            "agent_id", "agent_type",
        )
    }
    return PermissionRequest(
        session_id=_text(value.get("session_id")),
        cwd=_text(value.get("cwd")),
        tool_name=tool_name,
        command=_text(tool_input.get("command")),
        shell=normalized_shell,
        description=_text(tool_input.get("description"), optional=True),
        original=value,
        **metadata,
    )


def read_request(stream: BinaryIO | TextIO) -> PermissionRequest:
    """Read at most the limit plus one byte/character, never an unbounded stream."""
    return parse_request(stream.read(MAX_INPUT_BYTES + 1))


def decision_json(behavior: str | None, message: str | None = None) -> str:
    """Return exact compact allow/deny JSON, or the empty abstention string."""
    if behavior is None:
        if message is not None:
            raise ValueError("abstention has no message")
        return ""
    if behavior not in ("allow", "deny"):
        raise ValueError("unsupported decision")
    decision = {"behavior": behavior}
    if message is not None:
        if behavior != "deny" or not isinstance(message, str):
            raise ValueError("only deny may carry a message")
        if len(message) > MAX_MESSAGE_CHARS:
            raise ValueError("deny message is too large")
        message.encode("utf-8", errors="strict")
        decision["message"] = message
    return json.dumps(
        {"hookSpecificOutput": {
            "hookEventName": "PermissionRequest", "decision": decision,
        }},
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
