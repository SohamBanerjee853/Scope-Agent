"""Bounded, explicit transcript reads for a conservative coverage estimate.

Only structured shell argv are eligible for native rule checks. Tool calls are
requests, not proof of execution. Code-mode JavaScript and tool output are never
parsed for embedded commands or treated as instructions.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import stat
from typing import Any

MAX_BYTES = 16 * 1024 * 1024
MAX_COMMANDS = 1000
MAX_LINE = 128 * 1024
MAX_ARGV_BYTES = 32 * 1024
MAX_ARGUMENTS = 256
SHELL_TOOLS = frozenset({"shell", "exec_command", "shell_command",
                         "functions.shell", "functions.exec_command", "functions.shell_command"})


def valid_argv(value: Any) -> bool:
    """An argv list is data for execpolicy; it is never itself executed."""
    if (not isinstance(value, list) or not 1 <= len(value) <= MAX_ARGUMENTS
            or not all(isinstance(item, str) and "\0" not in item for item in value)
            or not value[0] or any(char in value[0] for char in "\r\n")):
        return False
    try:
        return sum(len(item.encode("utf-8")) + 1 for item in value) <= MAX_ARGV_BYTES
    except UnicodeError:
        return False


def json_object(data: str | bytes) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("nonfinite JSON value")

    result = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(result, dict):
        raise ValueError("JSON object required")
    return result


def _candidate(payload: dict[str, Any], number: int) -> dict[str, Any]:
    tool = payload.get("name")
    item = {"source": "transcript", "record": number,
            "tool": tool[:100] if isinstance(tool, str) else None,
            "argv": None, "command": None, "status": "unknown",
            "reason": "unsupported tool call; embedded code is not shell argv"}
    if payload.get("type") != "function_call" or not isinstance(tool, str) or tool not in SHELL_TOOLS:
        return item
    arguments = payload.get("arguments")
    try:
        if isinstance(arguments, str):
            if len(arguments.encode("utf-8")) > MAX_LINE:
                raise ValueError("arguments too large")
            arguments = json_object(arguments)
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be a JSON object")
    except (ValueError, UnicodeError, RecursionError):
        item["reason"] = "invalid structured tool arguments"
        return item
    # The shell tool's command list is actual argv. An exec_command cmd string
    # omits the final native shell invocation; do not reconstruct it with shlex.
    command = arguments.get("command", arguments.get("cmd"))
    if tool in {"shell", "functions.shell"} and valid_argv(command):
        item.update(argv=list(command), status="candidate", reason="structured argv request")
    elif isinstance(command, str) and len(command.encode("utf-8", errors="replace")) <= MAX_ARGV_BYTES:
        item.update(command=command, reason="command text does not establish native argv")
    else:
        item["reason"] = "missing or invalid command argv"
    return item


def read(path: str | os.PathLike[str], limit: int = MAX_BYTES) -> dict[str, Any]:
    """Read at most 16 MiB of one named regular JSONL file, without discovery."""
    result = {"status": "unavailable", "commands": [], "bytes_read": 0,
              "records_read": 0, "malformed_records": 0, "ignored_records": 0,
              "truncated": False, "command_limit_reached": False,
              "sha256_prefix": None,
              "meaning": "Transcript tool requests are not permission decisions or proof of execution."}
    if type(limit) is not int or not 1 <= limit <= MAX_BYTES:
        result["reason"] = "invalid transcript byte limit"
        return result
    try:
        target = Path(path)
        result["path"] = str(target)
        named = target.lstat()
        if not stat.S_ISREG(named.st_mode) or stat.S_ISLNK(named.st_mode):
            raise OSError("regular transcript file required")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_BINARY", 0)
        fd = os.open(target, flags)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
                raise OSError("transcript changed during open")
            data = stream.read(limit + 1)
    except (OSError, TypeError, ValueError):
        result["reason"] = "transcript unavailable or not a regular file"
        return result
    result["truncated"] = len(data) > limit
    data = data[:limit]
    result["bytes_read"] = len(data)
    result["sha256_prefix"] = hashlib.sha256(data).hexdigest()
    result["status"] = "truncated" if result["truncated"] else "complete"
    complete = data.rfind(b"\n") + 1
    if complete != len(data):
        # A partial last record cannot support any finding.
        result["truncated"] = True
        result["status"] = "truncated"
    for number, line in enumerate(io.BytesIO(data[:complete]), 1):
        if len(result["commands"]) >= MAX_COMMANDS:
            result["command_limit_reached"] = True
            break
        result["records_read"] += 1
        try:
            if len(line) > MAX_LINE:
                raise ValueError("transcript record too large")
            record = json_object(line.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            result["malformed_records"] += 1
            continue
        payload = record.get("payload")
        if (record.get("type") == "response_item" and isinstance(payload, dict)
                and isinstance(payload.get("type"), str)
                and payload.get("type") in {"function_call", "custom_tool_call"}):
            result["commands"].append(_candidate(payload, number))
        else:
            result["ignored_records"] += 1
    if result["malformed_records"] and result["status"] == "complete":
        result["status"] = "partial"
    return result
