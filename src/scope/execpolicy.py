"""Bounded read-only checks of explicitly supplied native Codex rule files.

Only ``codex execpolicy check`` runs. Command argv are arguments after ``--``;
the candidate command is never launched. Rules are evaluated by Codex's native
engine, never Python eval or a shell. Current rules cannot prove past decisions.

CLI syntax checked against codex-cli 0.154.0 and the official rules documentation:
https://learn.chatgpt.com/docs/agent-configuration/rules
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import threading
import time
from typing import Any

from .transcript import json_object, valid_argv

MAX_SECONDS = 5.0
MAX_OUTPUT = 64 * 1024
MAX_RULES = 64
MAX_RULE_BYTES = 1024 * 1024
MEANING = "Current explicit native-rule estimate only; historical native decisions and execution remain unknown."


def _unknown(reason: str, **fields: Any) -> dict[str, Any]:
    return {"status": "unknown", "decision": None, "matched_rules": [],
            "reason": reason, "meaning": MEANING, **fields}


def _terminate(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def _check(argv: list[str], rules: list[Path], *, timeout: float = MAX_SECONDS,
           cwd: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Evaluate only explicit rules with bounded output and a <=5-second deadline."""
    started = time.monotonic()
    if not valid_argv(argv):
        return _unknown("invalid structured argv")
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= MAX_SECONDS):
        return _unknown("invalid policy deadline")
    deadline = started + timeout
    if not isinstance(rules, (list, tuple)) or not 1 <= len(rules) <= MAX_RULES:
        return _unknown("explicit bounded rule files are required")
    paths = []
    try:
        total_size = 0
        for rule in rules:
            path = Path(rule).expanduser().absolute()
            info = path.lstat()
            total_size += info.st_size
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or total_size > MAX_RULE_BYTES:
                return _unknown("rule file is unavailable, linked or too large")
            paths.append(path)
        executable = shutil.which("codex")
        if executable is None:
            return _unknown("codex executable unavailable")
        # Batch/cmd wrappers may implicitly enter a shell on Windows. Require a
        # real executable there; do not broaden policy to repair helper discovery.
        if os.name == "nt" and Path(executable).suffix.lower() != ".exe":
            return _unknown("native codex.exe required for a shell-free policy check")
        command = [executable, "execpolicy", "check"]
        for path in paths:
            command.extend(["--rules", str(path)])
        command.extend(["--", *argv])
        if time.monotonic() >= deadline:
            return _unknown("policy deadline exhausted", timed_out=True)
        options = {"start_new_session": True} if os.name != "nt" else {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=cwd, shell=False, **options)
    except (OSError, TypeError, ValueError):
        return _unknown("policy checker unavailable or failed to start")

    output = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()
    read_error = threading.Event()

    def drain(name, stream):
        try:
            while True:
                chunk = os.read(stream.fileno(), 8192)
                if not chunk:
                    break
                remaining = MAX_OUTPUT - len(output[name])
                output[name].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
        except (OSError, ValueError):
            read_error.set()
        finally:
            stream.close()

    readers = []
    timed_out = False
    try:
        for name in output:
            reader = threading.Thread(target=drain, args=(name, getattr(process, name)), daemon=True)
            reader.start()
            readers.append(reader)
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate(process)
                break
            if overflow.is_set():
                _terminate(process)
                break
            try:
                process.wait(timeout=min(remaining, 0.02))
            except subprocess.TimeoutExpired:
                pass
        try:
            process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            _terminate(process)
        for reader in readers:
            reader.join(timeout=0.05)
        metadata = {"timed_out": timed_out, "truncated": overflow.is_set(),
                    "exit_code": process.poll(), "stdout_bytes": len(output["stdout"]),
                    "stderr_bytes": len(output["stderr"])}
        if timed_out:
            return _unknown("policy deadline exhausted", **metadata)
        if overflow.is_set() or read_error.is_set() or any(reader.is_alive() for reader in readers):
            return _unknown("policy output truncated or unavailable", **metadata)
        if process.returncode != 0:
            return _unknown("native policy checker failed", **metadata)
        try:
            result = json_object(bytes(output["stdout"]).decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            return _unknown("invalid native policy output", **metadata)
        matches = result.get("matchedRules")
        decision = result.get("decision")
        if (not isinstance(decision, str) or decision not in {"allow", "prompt", "forbidden"}
                or not isinstance(matches, list)
                or not matches or not all(isinstance(item, dict) for item in matches)):
            return _unknown("no confirmed matching native rule", **metadata)
        decisions = []
        for match in matches:
            prefix = match.get("prefixRuleMatch")
            if (not isinstance(prefix, dict) or not valid_argv(prefix.get("matchedPrefix"))
                    or not isinstance(prefix.get("decision"), str)
                    or prefix["decision"] not in {"allow", "prompt", "forbidden"}):
                return _unknown("unsupported native rule match evidence", **metadata)
            decisions.append(prefix["decision"])
        severity = {"allow": 0, "prompt": 1, "forbidden": 2}
        if max(decisions, key=severity.get) != decision:
            return _unknown("inconsistent native rule decision", **metadata)
        return {"status": "evaluated", "decision": decision, "matched_rules": matches,
                "meaning": MEANING, **metadata}
    finally:
        if process.poll() is None or any(reader.is_alive() for reader in readers):
            _terminate(process)
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        if not any(reader.is_alive() for reader in readers):
            process.stdout.close()
            process.stderr.close()


def check(argv: list[str], rules: list[Path], *, timeout: float = MAX_SECONDS,
          cwd: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Return unknown on checker failures; never turn an error into an allow."""
    try:
        return _check(argv, rules, timeout=timeout, cwd=cwd)
    except Exception:
        return _unknown("native policy evaluation failed")
