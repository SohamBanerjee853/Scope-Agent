"""Terminal reply-tag and cancellation tests with explicitly labeled fixtures."""

from collections import deque
import io
import json
import sys
import time
from types import SimpleNamespace

import pytest

from scope import watch_ui
from scope.grants import validate_card
from scope.wire import parse_request


class Context:
    def __init__(self):
        self.live = True
        self.deadline = time.monotonic() + 1

    def active(self):
        return self.live and time.monotonic() < self.deadline


class ScriptedTerminal(watch_ui.TerminalUI):
    interactive = True

    def __init__(self, lines):
        super().__init__(io.StringIO(), io.StringIO())
        self.lines = deque(lines)
        self.discarded = False

    def _line(self, context):
        return self.lines.popleft() if self.lines else None

    def _discard(self):
        self.discarded = True


def fixture_request():
    return parse_request(json.dumps({"session_id": "scripted-terminal", "cwd": "/project", "tool_name": "Bash",
                                     "tool_input": {"command": "touch note.txt", "description": "[bold]untrusted\u001b[2J"}}))


@pytest.fixture
def fixed_tags(monkeypatch):
    tags = iter(["prompt01", "prompt02", "prompt03", "prompt04"])
    monkeypatch.setattr(watch_ui.secrets, "token_hex", lambda n: next(tags))


def test_late_and_untagged_answers_cannot_approve_new_prompt(fixed_tags):
    ui = ScriptedTerminal(["once", "expired once", "1-prompt01 deny", "1-prompt01 once", "2-prompt02 abstain"])
    assert ui.review(fixture_request(), None, Context()) == {"action": "deny"}
    assert ui.review(fixture_request(), None, Context()) == {"action": "abstain"}
    assert ui.output.getvalue().count("Ignored untagged or expired") == 3


def test_cancelled_prompt_cannot_consume_late_answer(fixed_tags):
    ui = ScriptedTerminal([])
    assert ui.question("/project", "fixture", "predict", Context()) is None
    assert ui.discarded
    ui.lines.extend(["1-prompt01 old answer", "2-prompt02 new explicit answer"])
    assert ui.question("/project", "fixture", "predict", Context()) == "new explicit answer"


def test_deliberately_empty_question_answer_remains_empty(fixed_tags):
    ui = ScriptedTerminal(["1-prompt01"])
    assert ui.question("/project", "fixture", "predict", Context()) == ""


def test_edited_card_requires_second_fresh_reply_tag(fixed_tags):
    edited = {"summary": "One local note", "commands": ["touch *"], "domains": [], "budget": 1}
    ui = ScriptedTerminal(["1-prompt01 edit", "1-prompt01 " + json.dumps(edited), "2-prompt02 " + json.dumps(edited)])
    assert ui.review(fixture_request(), validate_card(edited), Context()) == {"action": "grant", "card": edited}
    output = ui.output.getvalue()
    assert "One local note" in output and "budget" in output and "touch *" in output
    assert "\x1b" not in output and r"\u001b" in output


def test_noninteractive_terminal_never_reads(monkeypatch):
    ui = watch_ui.TerminalUI(io.StringIO(), io.StringIO())
    monkeypatch.setattr(ui, "_line", lambda context: pytest.fail("must not read redirected stdin"))
    assert ui.question("repo", "context", "prompt", Context()) is None
    assert ui.review(fixture_request(), None, Context()) == {"action": "abstain"}


def test_expired_context_never_reads(fixed_tags):
    ui = ScriptedTerminal(["1-prompt01 once"])
    context = Context()
    context.live = False
    assert ui.review(fixture_request(), None, context) == {"action": "abstain"}
    assert list(ui.lines) == ["1-prompt01 once"]


def test_windows_key_reader_with_injected_console_api(monkeypatch):
    keys = deque("fixture\bX\r")
    fake_console = SimpleNamespace(kbhit=lambda: bool(keys), getwch=lambda: keys.popleft())
    monkeypatch.setitem(sys.modules, "msvcrt", fake_console)
    monkeypatch.setattr(watch_ui, "os", SimpleNamespace(name="nt"))
    ui = ScriptedTerminal([])
    assert watch_ui.TerminalUI._line(ui, Context()) == "fixturX"


def test_posix_reader_with_injected_descriptor(monkeypatch):
    # Exercise the POSIX branch on either OS using a descriptor fixture.
    chunks = deque([b"scripted first\nscripted second\n"])
    monkeypatch.setattr(watch_ui, "os", SimpleNamespace(name="posix", read=lambda fd, count: chunks.popleft()))
    monkeypatch.setattr(watch_ui.select, "select", lambda *args: ([17], [], []))
    ui = ScriptedTerminal([])
    ui.input = SimpleNamespace(fileno=lambda: 17)
    assert watch_ui.TerminalUI._line(ui, Context()) == "scripted first"
    assert watch_ui.TerminalUI._line(ui, Context()) == "scripted second"
