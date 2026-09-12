"""Human understanding answers through the authenticated watcher.

Answers carry their actual input route. They are neither execution consent nor
permission grants. This module does not execute probes or parse source as code.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from typing import Callable

from .watch_ui import display_text

MAX_TEXT = 16384


def _text(value, *, maximum=MAX_TEXT, nonempty=True):
    if (not isinstance(value, str) or len(value) > maximum or "\0" in value
            or nonempty and not value.strip()):
        raise ValueError("invalid understanding question text")
    value.encode("utf-8")
    return value


def ask(prompt: str, *, repo: str, context: str = "", home=None,
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
        response = transport({"kind": "question", "repo": repo, "context": context,
                              "prompt": prompt}, timeout=timeout, home=home)
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
