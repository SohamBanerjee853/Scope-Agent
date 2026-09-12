"""Human understanding answers through a terminal or the existing watcher.

Answers carry their actual input route. They are neither execution consent nor
permission grants. This module does not execute probes or parse source as code.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from typing import Callable

from .watch_ui import TerminalUI, display_text

MAX_TEXT = 16384
_terminal_lock = threading.Lock()


class _QuestionContext:
    def __init__(self, deadline: float, cancelled: threading.Event | None):
        self.deadline = deadline
        self.cancelled = cancelled

    def active(self) -> bool:
        return (time.monotonic() < self.deadline
                and (self.cancelled is None or not self.cancelled.is_set()))


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

    Interactive prompts reuse the watcher's tagged terminal reader, with at most
    95 seconds for a human. Headless callers send one authenticated IPC question;
    no response is retried. ``cancelled`` also supports caller-owned cancellation
    of direct terminal prompts; watcher revocation governs transported questions.
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
        question_context = _QuestionContext(started + min(timeout, 95), cancelled)
        if not question_context.active():
            return {"error": "understanding question cancelled"}
        terminal = TerminalUI(input_stream if input_stream is not None else sys.stdin,
                              output_stream if output_stream is not None else sys.stderr)
        if terminal.interactive:
            provenance = "human_terminal"
            while question_context.active():
                if not _terminal_lock.acquire(timeout=0.025):
                    continue
                try:
                    if not question_context.active():
                        return {"error": "understanding question cancelled"}
                    answer = terminal.question(repo, context, prompt, question_context)
                finally:
                    _terminal_lock.release()
                break
            else:
                return {"error": "understanding question timed out or cancelled"}
            if not question_context.active():
                return {"error": "understanding question timed out or cancelled"}
        else:
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
                return {"error": "human reviewer unavailable or question cancelled"}
            answer = response["answer"]
        if answer is None:
            return {"error": "human answer unavailable or skipped"}
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
