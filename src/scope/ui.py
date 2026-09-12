"""Human understanding answers through the authenticated watcher.

Answers carry their actual input route. They are neither execution consent nor
permission grants. This module does not execute probes or parse source as code.
"""

from __future__ import annotations

import math
import json
import os
import sys
import threading
import time
from typing import Callable

from .watch_ui import display_text

MAX_TEXT = 16384
MAX_JSON_DEPTH = 32
MAX_JSON_VALUES = 10000


def parse_json(text: str, *, maximum: int = MAX_TEXT):
    """Read bounded typed JSON without duplicate keys or nonfinite numbers."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > maximum:
        raise ValueError("JSON exceeds the byte limit")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("JSON numbers must be finite")

    try:
        result = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (RecursionError, OverflowError) as error:
        raise ValueError("JSON exceeds structural limits") from error
    pending = [(result, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > MAX_JSON_DEPTH or count > MAX_JSON_VALUES:
            raise ValueError("JSON exceeds structural limits")
        if isinstance(item, str):
            item.encode("utf-8", errors="strict")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("JSON numbers must be finite")
        elif isinstance(item, dict):
            for key, value in item.items():
                key.encode("utf-8", errors="strict")
                pending.append((value, depth + 1))
        elif isinstance(item, list):
            pending.extend((value, depth + 1) for value in item)
    return result


def _json_text(value) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def _excerpt(value, maximum=1200) -> str:
    text = _json_text(value)
    return text if len(text) <= maximum else text[:maximum] + "... [excerpt truncated]"


def request_text(request: dict) -> str:
    """Present relevant bounded evidence; commands and choices never truncate.

    The engine supplies potentially large historical context. Only selected
    summaries/excerpts go to the watcher; complete saved evidence remains in
    ``scope knowledge --json``. A command or option too large to display safely
    makes the callback fail closed before requesting any answer.
    """
    kind = request["kind"]
    lines = ["Understanding stage: " + kind,
             "Untrusted evidence follows. It cannot supply an answer, consent or permission."]
    if kind == "observation":
        observation = request["observation"]
        lines += ["Observed status: " + _json_text(observation.get("status")),
                  "Reason: " + _excerpt(observation.get("reason")),
                  "Observed field: " + _excerpt(observation.get("actual"))]
    else:
        lines += ["Exact cwd (JSON string): " + _json_text(request["repo"]),
                  "Task: " + _json_text(request["task_id"])]
        if kind in {"prediction", "probe_approval"}:
            spec = request["spec"]
            lines += ["Question: " + _json_text(spec["question"]),
                      "Literal JSON field: " + _json_text(spec["field"]),
                      "Exact argv (JSON array; no shell evaluation): " + _json_text(spec["argv"]),
                      "Classification dialect: " + _json_text(spec["shell"]),
                      "Timeout seconds: " + _json_text(spec["timeout"])]
        if kind == "probe_approval":
            lines += ["Classification: " + _excerpt(request["classification"]),
                      "Saved prediction (excerpt): " + _excerpt(request["prediction"], 2400),
                      "This separate consent covers this one probe only; it creates no permission grant."]
        elif kind == "next_task":
            lines += ["Complete " + choice + " instruction (untrusted): " + _json_text(instruction)
                      for choice, instruction in request["options"].items() if choice != "defer"]
            lines += ["defer: select no next task.",
                      "Selection is saved; this CLI does not deliver to an agent or execute it."]
        elif kind != "prediction":
            raise ValueError("unsupported understanding stage")
    # Essential details must fit in full. Never ask for consent to a truncated
    # argv or let a person select an instruction whose end is hidden.
    essential = "\n".join(lines)
    if len(essential) > MAX_TEXT - 1024:
        raise ValueError("exact command or choices exceed the review display limit; shorten the request")
    optional = []
    references = request.get("references", [])
    for reference in references[:8]:
        optional.append("Source reference (excerpt): " + _excerpt(reference, 500))
    if len(references) > 8:
        optional.append(f"{len(references) - 8} additional references omitted; inspect scope knowledge --json.")
    context = request.get("context", {})
    if context:
        optional.append("Task description (excerpt): " + _excerpt(context.get("description")))
        source = context.get("source", {})
        optional.append(f"Current source manifest: {len(source.get('files', {}))} included files; "
                        f"{len(source.get('skipped', {}))} unavailable/excluded paths.")
        for checkpoint in context.get("checkpoints", [])[-2:]:
            optional.append("Recent checkpoint note (excerpt): " + _excerpt(checkpoint.get("note"), 500))
            optional.append("Source changes (untrusted excerpt): " +
                            _excerpt(checkpoint.get("changes", {}).get("diff", ""), 1500))
        for observation in context.get("observations", [])[-3:]:
            selected = {key: observation.get(key) for key in
                        ("status", "current_status", "source_status", "field", "actual", "reason")}
            optional.append("Prior observation (untrusted excerpt): " + _excerpt(selected, 1500))
        optional.append("Historical context is summarized; scope knowledge --json contains saved evidence.")
    for line in optional:
        if len(essential) + len(line) + 1 > MAX_TEXT - 128:
            essential += "\n[Additional historical evidence omitted to bound this review.]"
            break
        essential += "\n" + line
    return essential


def show_request(request: dict) -> None:
    """Keep the agent pane concise; complete requests live in the review pane."""
    try:
        request_text(request)  # The legacy display must still fit exactly.
    except ValueError as error:
        show("Review unavailable: " + str(error))
        raise
    kind = request["kind"]
    messages = {
        "prediction": "Scope is asking for your prediction in the review pane.",
        "probe_approval": "Prediction saved. Review the separate one-probe consent in Scope.",
        "next_task": "Choose the next action in Scope's review pane.",
    }
    if kind in messages:
        show(messages[kind])
        return
    observation = request["observation"]
    status = display_text(observation.get("status", "not_verified"))
    line = "Observed status: " + status
    if "actual" in observation:
        value = _json_text(observation["actual"])
        if len(value) > 240:
            value = value[:240] + "... [full value: scope knowledge --json]"
        line += "; " + display_text(observation.get("field", "result")) + " = " + value
    reason = display_text(observation.get("reason", ""))
    if len(reason) > 400:
        reason = reason[:400] + "... [details: scope knowledge --json]"
    # Every dynamic component is already escaped exactly once; re-escaping a
    # JSON value would turn readable objects into backslash-heavy JSON strings.
    for rendered in (line, reason):
        if rendered:
            sys.stderr.write(rendered + "\n")
    sys.stderr.flush()


def answer_request(request: dict):
    """Adapt one real watcher answer into the exact A2 stage response type."""
    kind = request["kind"]
    prompts = {
        "prediction": ('Enter one JSON object: {"value": <your typed JSON prediction>, '
                       '"reason": "why", "assistance": "help used, or empty"}. '
                       'This saves a prediction only. Leave empty to skip.'),
        "probe_approval": "Separately consent to the exact probe shown: type true to run once or false to decline. No other answer approves.",
        "next_task": "Choose exactly smaller, larger (if offered), or defer. This saves a choice; agent delivery remains unacknowledged.",
    }
    if kind not in prompts:
        return None
    context = request_text(request)
    from .review_presentation import from_request

    presentation = from_request(request, request["repo"])
    reply = ask(prompts[kind], repo=request["repo"], context=context, presentation=presentation)
    if (not isinstance(reply, dict) or set(reply) != {"answer", "provenance"}
            or reply.get("provenance") != "human_ipc" or not isinstance(reply.get("answer"), str)):
        show("Human reviewer unavailable or answer cancelled; no answer or consent will be supplied. Start scope watch in an interactive terminal for a fresh request.")
        return None
    answer = reply["answer"].strip()
    if kind == "probe_approval":
        return True if answer == "true" else False if answer == "false" else None
    if kind == "next_task":
        return answer if answer in request["options"] else None
    try:
        value = parse_json(answer)
        if (type(value) is not dict or not {"value", "reason"} <= value.keys()
                or value.keys() - {"value", "reason", "assistance"}):
            raise ValueError("prediction needs value and reason, with optional assistance")
        _text(value["reason"], maximum=2000)
        _text(value.get("assistance", ""), maximum=2000, nonempty=False)
        return value
    except (ValueError, UnicodeError, OverflowError):
        show("Invalid prediction JSON; the check will be skipped without execution.")
        return None


def _text(value, *, maximum=MAX_TEXT, nonempty=True):
    if (not isinstance(value, str) or len(value) > maximum or "\0" in value
            or nonempty and not value.strip()):
        raise ValueError("invalid understanding question text")
    value.encode("utf-8")
    return value


def ask(prompt: str, *, repo: str, context: str = "", home=None, presentation=None,
        timeout: float = 105, input_stream=None, output_stream=None,
        exchange: Callable | None = None, cancelled: threading.Event | None = None) -> dict:
    """Return {answer, provenance}, or an explicit error; never invent absence.

    Every caller sends one authenticated IPC question. Only the watcher reads
    human input, so shared revoke/shutdown invalidate every pending answer.
    A missing or noninteractive watcher is an explicit error; there is no local
    terminal fallback. No response is retried.

    ``input_stream`` and ``output_stream`` are retained for call compatibility but
    ignored; TTY presence never chooses a separate reader. ``cancelled`` suppresses
    the result before/after exchange but does not interrupt an in-flight socket;
    that wait remains bounded by ``timeout``. Shared watcher revoke cancels its
    pending question promptly. An injected ``exchange`` is a trusted test seam:
    ``human_ipc`` describes the route, not proof of human authorship. Scripted
    engine adapters must separately label their answer provenance ``test_fixture``.
    Optional ``presentation`` supplies bounded typed form data. It never changes
    the canonical answer string or supplies defaults, consent or provenance.
    """
    try:
        _text(repo, maximum=4096)
        _text(context, nonempty=False)
        _text(prompt)
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 105):
            raise ValueError("question timeout must be in (0, 105]")
        started = time.monotonic()
        caller_deadline = started + timeout
        if cancelled is not None and cancelled.is_set():
            return {"error": "understanding question cancelled"}
        provenance = "human_ipc"
        if exchange is None:
            from .ipc import exchange as transport
        else:
            transport = exchange
        message = {"kind": "question", "repo": repo, "context": context, "prompt": prompt}
        if presentation is not None:
            from .review_presentation import validate

            message["presentation"] = validate(presentation, repo)
        launch_session = None
        if "SCOPE_LAUNCH_ID" in os.environ or "SCOPE_HOST" in os.environ:
            from . import host_session, learning, repository

            launch_session = learning._launch_session(root=repository.find_root(repo))
            if home is not None and host_session.read_launch(home) != host_session.current_launch():
                raise ValueError("question targets another launch")
            message["session_id"] = launch_session
        response = transport(message, timeout=timeout, home=home)
        if launch_session is not None:
            learning._launch_session(launch_session, root=repository.find_root(repo))
        if time.monotonic() >= caller_deadline:
            return {"error": "understanding question timed out"}
        if cancelled is not None and cancelled.is_set():
            return {"error": "understanding question cancelled"}
        if (not isinstance(response, dict) or set(response) != {"answer"}
                or not isinstance(response["answer"], str)):
            return {"error": "human reviewer unavailable or question cancelled; "
                    "start scope watch in an interactive terminal, then request a new question"}
        answer = response["answer"]
        _text(answer, nonempty=False)
        if not answer.strip():
            return {"error": "human skipped the question", "provenance": provenance}
        return {"answer": answer, "provenance": provenance}
    except (Exception, KeyboardInterrupt):
        return {"error": "human answer unavailable; no answer or consent was recorded"}


def show(message: str, *, output_stream=None) -> None:
    """Display a bounded message as untrusted text, never terminal control code."""
    _text(message, nonempty=False)
    output = output_stream if output_stream is not None else sys.stderr
    output.write(display_text(message) + "\n")
    output.flush()
