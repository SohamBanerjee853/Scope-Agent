"""Explicit, bounded native-rule estimates, separate from permission receipts."""

from __future__ import annotations

import itertools
from pathlib import Path
import time
from typing import Any, Callable, Iterable

from . import execpolicy, transcript

MAX_COMMANDS = 1000
MAX_EVENTS = 100_000
MAX_SECONDS = 5.0
MEANING = (
    "Estimates only against explicitly supplied current Codex rules. Native human answers, "
    "historical rules, actual execution and commands outside the supplied evidence remain unknown. "
    "Candidate records from logs and transcripts may overlap; these are not permission counts."
)


def audit(events: Iterable[dict[str, Any]], *, transcript_path=None, rules=(),
          host: str = "codex", evaluator: Callable | None = None) -> dict[str, Any]:
    """Audit supplied records/files only; never discover configs or transcripts.

    An injected evaluator is a trusted test seam with the same timeout contract
    as execpolicy.check. No watcher or hook should call this routine automatically.
    """
    report = {"status": "unknown", "host": host, "commands": [],
              "counts": {"candidates": 0, "evaluated": 0, "rule_allow": 0,
                         "rule_prompt": 0, "rule_forbidden": 0, "unknown": 0},
              "limits": {"commands": MAX_COMMANDS, "transcript_bytes": transcript.MAX_BYTES,
                         "policy_seconds": MAX_SECONDS},
              "command_limit_reached": False, "event_limit_reached": False,
              "deadline_exhausted": False, "transcript": {"status": "not_requested"},
              "native_decisions": "unknown", "execution": "unknown", "meaning": MEANING}
    if host != "codex":
        report["reason"] = "native rule/transcript coverage is unsupported for this host"
        return report
    candidates = []
    ids = set()
    try:
        for number, event in enumerate(itertools.islice(events, MAX_EVENTS + 1), 1):
            if number > MAX_EVENTS:
                report["event_limit_reached"] = True
                break
            if not isinstance(event, dict) or event.get("event") != "permission_request":
                continue
            fields = event.get("fields")
            if not isinstance(fields, dict):
                continue
            request_id = fields.get("request_id")
            if isinstance(request_id, str) and request_id in ids:
                continue
            if isinstance(request_id, str):
                ids.add(request_id)
            if len(candidates) >= MAX_COMMANDS:
                report["command_limit_reached"] = True
                break
            argv = fields.get("argv")
            command = fields.get("command")
            candidates.append({"source": "permission_request", "record": number,
                               "request_id": request_id if isinstance(request_id, str) else None,
                               "argv": list(argv) if transcript.valid_argv(argv) else None,
                               "command": command if isinstance(command, str)
                               and len(command.encode("utf-8", errors="replace")) <= transcript.MAX_ARGV_BYTES else None,
                               "cwd": fields.get("cwd") if isinstance(fields.get("cwd"), str) else None})
    except (TypeError, ValueError):
        report["reason"] = "permission event evidence unavailable"
    if transcript_path is not None:
        evidence = transcript.read(transcript_path)
        report["transcript"] = {key: value for key, value in evidence.items() if key != "commands"}
        for item in evidence["commands"]:
            if len(candidates) >= MAX_COMMANDS:
                report["command_limit_reached"] = True
                break
            candidates.append(item)
        report["command_limit_reached"] |= evidence["command_limit_reached"]
    try:
        rule_paths = list(rules) if isinstance(rules, (list, tuple)) else []
        if len(rule_paths) > execpolicy.MAX_RULES:
            rule_paths = []
        rule_names = [str(Path(path)) for path in rule_paths]
    except (TypeError, ValueError):
        rule_paths, rule_names = [], []
    report["rule_files"] = rule_names
    evaluate = execpolicy.check if evaluator is None else evaluator
    deadline = time.monotonic() + MAX_SECONDS
    for candidate in candidates:
        item = {**candidate, "status": "unknown", "decision": None}
        remaining = deadline - time.monotonic()
        if not candidate.get("argv"):
            item["reason"] = candidate.get("reason", "command text does not establish native argv")
        elif not rule_paths:
            item["reason"] = "no explicit bounded native rule files supplied"
        elif remaining <= 0:
            report["deadline_exhausted"] = True
            item["reason"] = "total native policy deadline exhausted"
        else:
            try:
                result = evaluate(candidate["argv"], rule_paths, timeout=min(remaining, MAX_SECONDS),
                                  cwd=candidate.get("cwd"))
                if time.monotonic() > deadline:
                    report["deadline_exhausted"] = True
                    item["reason"] = "total native policy deadline exhausted"
                elif (isinstance(result, dict) and result.get("status") == "evaluated"
                      and result.get("decision") in {"allow", "prompt", "forbidden"}):
                    item.update(status="evaluated", decision=result["decision"],
                                reason="current explicit native-rule estimate")
                else:
                    item["reason"] = (result.get("reason", "native policy result unavailable")
                                      if isinstance(result, dict) else "native policy result unavailable")
            except Exception:
                item["reason"] = "native policy evaluation failed"
        report["commands"].append(item)
        report["counts"]["candidates"] += 1
        if item["status"] == "evaluated":
            report["counts"]["evaluated"] += 1
            report["counts"]["rule_" + item["decision"]] += 1
        else:
            report["counts"]["unknown"] += 1
    if report["counts"]["evaluated"]:
        report["status"] = "estimate"
    return report
