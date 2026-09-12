"""Real terminal key events exercise the review desk, without a live host."""

from concurrent.futures import Future
from contextlib import contextmanager
import io
import json
import threading
import time
from types import SimpleNamespace

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from scope import ipc, review_presentation
from scope.grants import validate_card
from scope.review_tui import ReviewUI, safe_text
from scope.watch import Watcher
from scope.wire import parse_request

F2 = "\x1bOQ"
F3 = "\x1bOR"
F4 = "\x1bOS"
PAGEUP = "\x1b[5~"
PAGEDOWN = "\x1b[6~"


def until(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        threading.Event().wait(0.005)
    assert condition(), "terminal state did not reach the expected condition"


def call_thread(callback):
    future = Future()

    def run():
        try:
            future.set_result(callback())
        except BaseException as error:
            future.set_exception(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return future


class FixtureContext:
    def __init__(self, presentation=None):
        self.cancelled = threading.Event()
        self.presentation = presentation

    def active(self):
        return not self.cancelled.is_set()


class TerminalOutput(DummyOutput):
    def __init__(self, columns, rows):
        self.columns, self.rows = columns, rows

    def get_size(self):
        return Size(rows=self.rows, columns=self.columns)


@contextmanager
def desk(*, columns=69, rows=39, watcher_factory=None, owner_tick=None):
    with create_pipe_input() as source:
        ui = ReviewUI(input=source, output=TerminalOutput(columns, rows))
        watcher = watcher_factory(ui) if watcher_factory else SimpleNamespace(closed=threading.Event())
        if watcher_factory:
            watcher.start()
        running = call_thread(lambda: ui.run(watcher, owner_tick=owner_tick,
                                             on_revoke=getattr(watcher, "_revoke", None)))
        assert ui._running.wait(5)
        try:
            yield ui, source, watcher
        finally:
            if not running.done():
                source.send_text("\x03")
            running.result(timeout=5)
            if watcher_factory:
                watcher.close()


def on_ui(ui, callback):
    future = Future()

    def apply():
        try:
            future.set_result(callback())
        except BaseException as error:
            future.set_exception(error)

    ui._loop.call_soon_threadsafe(apply)
    return future.result(timeout=5)


def screen(ui):
    def read():
        rendered = ui._app.renderer.last_rendered_screen
        if rendered is None:
            return ""
        return "\n".join("".join(rendered.data_buffer[row][col].char
                                for col in range(ui._output.columns))
                         for row in range(ui._output.rows))
    return on_ui(ui, read)


def open_form(ui, source, marker):
    until(lambda: bool(ui._pending))
    source.send_text(F2)
    until(lambda: marker in screen(ui))


def form(kind="prediction", repo="/fixture project"):
    request = {"kind": kind, "repo": repo, "task_id": "fixture-task",
               "spec": {"question": "How many results will this probe produce?", "field": "count",
                        "argv": ["python", "probe.py"], "shell": "posix", "timeout": 5}}
    if kind == "probe_approval":
        request.update(classification={"name": "T2", "reason": "Read-only fixture probe"},
                       prediction={"value": 2, "reason": "Two fixture entries", "assistance": ""})
    if kind == "next_task":
        request["options"] = {"smaller": "Inspect the retry key", "larger": "Review both callers", "defer": None}
    return review_presentation.from_request(request, repo)


def question(ui, kind="prediction", *, presentation=None):
    context = FixtureContext(presentation if presentation is not None else form(kind))
    pending = call_thread(lambda: ui.question("/fixture project", "Fixture context", "Fixture question", context))
    return pending, context


def permission(ui):
    request = parse_request(json.dumps({"session_id": "fixture-session", "cwd": "/fixture project",
                                        "tool_name": "Bash", "tool_input": {"command": "pytest tests/test_fixture.py"}}))
    card = validate_card({"summary": "Run focused fixture checks", "commands": ["pytest *"], "domains": [], "budget": 3})
    context = FixtureContext()
    pending = call_thread(lambda: ui.review(request, card, context))
    return pending, context


@pytest.mark.parametrize("tabs,action", [(0, "abstain"), (1, "once"), (2, "grant"), (4, "deny")])
def test_permission_requires_opening_review_and_explicit_focused_choice(tabs, action):
    with desk() as (ui, source, watcher):
        pending, _ = permission(ui)
        until(lambda: bool(ui._pending))
        source.send_text("grant\r")
        until(lambda: "Review next" in screen(ui))
        assert ui._active is None and not pending.done()
        open_form(ui, source, "Permission review")
        source.send_text("\t" * tabs + "\r")
        assert pending.result(5) == {"action": action}
        assert ui._active is None


@pytest.mark.parametrize("changes,value,expected", [(0, "2", 2), (1, "2", "2"), (2, "false", False),
                                                    (3, "", None), (4, '{"count":[2,false]}', {"count": [2, False]})])
def test_prediction_has_explicit_value_type_and_separate_reason(changes, value, expected):
    with desk() as (ui, source, watcher):
        pending, _ = question(ui)
        open_form(ui, source, "Make a prediction")
        for _ in range(changes):
            previous = ui._value_type
            source.send_text(F4)
            until(lambda: ui._value_type != previous)
        source.send_text(value + "\tTwo fixture entries\t\t\r")
        answer = json.loads(pending.result(5))
        assert answer == {"value": expected, "reason": "Two fixture entries", "assistance": ""}
        assert type(answer["value"]) is type(expected)


def test_prediction_invalid_value_and_missing_reason_stay_in_form_with_field_error():
    with desk() as (ui, source, watcher):
        pending, _ = question(ui)
        open_form(ui, source, "Make a prediction")
        source.send_text("NaN\t\t\t\r")
        until(lambda: "value" in ui._errors)
        assert not pending.done()
        assert on_ui(ui, lambda: ui._app.layout.has_focus(ui._fields["value"]))
        source.send_text("\x15" + "2\t\t\t\r")
        until(lambda: "reason" in ui._errors)
        assert not pending.done()
        assert on_ui(ui, lambda: ui._app.layout.has_focus(ui._fields["reason"]))
        source.send_text("Two entries\t\t\r")
        assert json.loads(pending.result(5))["value"] == 2


@pytest.mark.parametrize("keys,answer", [("\r", "false"), ("\t\r", "true")])
def test_consent_declines_by_default_and_has_a_distinct_run_once_choice(keys, answer):
    with desk() as (ui, source, watcher):
        pending, _ = question(ui, "probe_approval")
        open_form(ui, source, "Run this probe?")
        assert "probe.py" in screen(ui) and "/fixture project" in screen(ui)
        source.send_text(keys)
        assert pending.result(5) == answer


@pytest.mark.parametrize("tabs,answer", [(0, "defer"), (1, "smaller"), (2, "larger")])
def test_next_task_choice_only_returns_the_selected_offered_key(tabs, answer):
    with desk() as (ui, source, watcher):
        pending, _ = question(ui, "next_task")
        open_form(ui, source, "Choose your next step")
        assert "Inspect the retry key" in screen(ui)
        source.send_text("\t" * tabs + "\r")
        assert pending.result(5) == answer


def test_cancel_discards_draft_and_late_keys_and_old_action_cannot_answer_new_ticket():
    with desk() as (ui, source, watcher):
        old, old_context = question(ui)
        open_form(ui, source, "Make a prediction")
        source.send_text("99\tOld explanation")
        until(lambda: ui._fields["reason"].text == "Old explanation")
        stale_action = on_ui(ui, lambda: ui._actions["Save prediction"])
        old_context.cancelled.set()
        assert old.result(5) is None
        until(lambda: ui._active is None)
        fresh, _ = question(ui, "probe_approval")
        until(lambda: bool(ui._pending))
        source.send_text("99\r\t\r")
        until(lambda: "Review next" in screen(ui))
        assert ui._active is None and not fresh.done()
        open_form(ui, source, "Run this probe?")
        on_ui(ui, stale_action)
        assert not fresh.done()
        source.send_text("\r")
        assert fresh.result(5) == "false"


def test_same_input_batch_cannot_open_and_approve_request():
    with desk() as (ui, source, watcher):
        pending, _ = permission(ui)
        until(lambda: bool(ui._pending))
        source.send_text(F2 + "\t\t\r")
        until(lambda: "Permission review" in screen(ui))
        assert not pending.done()
        source.send_text("\r")
        assert pending.result(5) == {"action": "abstain"}


def test_details_preserve_editing_fields_focus_and_cursor():
    with desk() as (ui, source, watcher):
        pending, _ = permission(ui)
        open_form(ui, source, "Permission review")
        source.send_text("\t\t\t\r")
        until(lambda: "Edit permission scope" in screen(ui))
        source.send_text("\x15New fixture purpose\t")
        until(lambda: on_ui(ui, lambda: ui._app.layout.has_focus(ui._fields["commands"])))
        before = on_ui(ui, lambda: {key: (area.text, area.buffer.cursor_position) for key, area in ui._fields.items()})
        source.send_text(F3)
        until(lambda: ui._details)
        assert ui._editing
        assert before == on_ui(ui, lambda: {key: (area.text, area.buffer.cursor_position) for key, area in ui._fields.items()})
        assert on_ui(ui, lambda: ui._app.layout.has_focus(ui._fields["commands"]))
        source.send_text("\x1b")
        assert pending.result(5) == {"action": "abstain"}


@pytest.mark.parametrize("columns,rows", [(69, 39), (45, 24)])
def test_keyboard_scroll_keeps_prediction_submit_visible_at_supported_sizes(columns, rows):
    with desk(columns=columns, rows=rows) as (ui, source, watcher):
        pending, _ = question(ui)
        open_form(ui, source, "Make a prediction")
        source.send_text("2\tTwo fixture entries\t\t")
        until(lambda: "Save prediction" in screen(ui))
        assert "Ctrl-C Exit" in screen(ui)
        source.send_text("\r")
        assert json.loads(pending.result(5))["value"] == 2


def test_essential_command_is_not_truncated_and_control_arguments_cannot_forge_lines():
    ui = ReviewUI()
    command = "pytest " + "a" * 20000 + " --end-of-command"
    assert ui._text_block(command).content.text == command
    controls = ui._execution({"argv": ["python", "first\n2. counterfeit", "\x1b[2J"],
                              "cwd": "/fixture project", "shell": "posix", "timeout": 5})
    displayed = controls[1].content.text
    assert len(displayed.splitlines()) == 3
    assert '\\n2. counterfeit' in displayed and "\x1b" not in displayed
    assert safe_text("[bold] Ω \x1b[2J\u202e") == "[bold] Ω \\u001b[2J\\u202e"


def test_pages_reach_complete_command_and_details_without_approving_hidden_button():
    with desk(columns=45, rows=24) as (ui, source, watcher):
        request = parse_request(json.dumps({"session_id": "fixture-session", "cwd": "/fixture project",
                                            "tool_name": "Bash", "tool_input": {
                                                "command": "pytest FIRST_ARGUMENT " + "long_argument " * 80 + " LAST_ARGUMENT",
                                                "description": "Evidence row\n" * 60 + "FINAL_EVIDENCE_ROW"}}))
        context = FixtureContext()
        pending = call_thread(lambda: ui.review(request, None, context))
        until(lambda: bool(ui._pending))
        source.send_text(F2)
        until(lambda: ui._active is not None)
        source.send_text(F3)
        until(lambda: ui._details)
        source.send_text(PAGEUP * 10)
        until(lambda: "FIRST_ARGUMENT" in screen(ui))
        seen_last_argument = False
        for _ in range(12):
            previous_scroll = on_ui(ui, lambda: ui._pane.vertical_scroll)
            source.send_text(PAGEDOWN)
            until(lambda: ui._pane.vertical_scroll != previous_scroll)
            # Wait for this page's actual renderer rather than timing keystrokes.
            previous = on_ui(ui, lambda: (ui._app.invalidate(), ui._app.render_counter)[1])
            until(lambda: ui._app.render_counter > previous)
            visible = screen(ui)
            seen_last_argument |= "LAST_ARGUMENT" in "".join(visible.split())
            if "FINAL_EVIDENCE_ROW" in visible:
                break
        assert seen_last_argument and "FINAL_EVIDENCE_ROW" in screen(ui), screen(ui)
        assert not pending.done()
        source.send_text("\r\r")
        until(lambda: not ui._manual_scroll)
        until(lambda: "Pass to host" in screen(ui))
        assert not pending.done()
        source.send_text("\r")
        assert pending.result(5) == {"action": "abstain"}


def test_real_authenticated_watcher_revokes_active_form_then_accepts_fresh_question(tmp_path):
    with desk(watcher_factory=lambda ui: Watcher(ui, human_timeout=10)) as (ui, source, watcher):
        metadata = form(repo=str(tmp_path))
        message = {"kind": "question", "repo": str(tmp_path), "context": "Fixture context",
                   "prompt": "Fixture prediction", "presentation": metadata}
        pending = call_thread(lambda: ipc.exchange(message, timeout=10))
        open_form(ui, source, "Make a prediction")
        source.send_text("99\tOld draft")
        until(lambda: ui._fields["reason"].text == "Old draft")
        source.send_text("\x12")
        result = pending.result(5)
        assert result is None or "answer" not in result
        until(lambda: not ui._revoking and ui._active is None)
        fresh = call_thread(lambda: ipc.exchange(message, timeout=10))
        open_form(ui, source, "Make a prediction")
        assert ui._fields["value"].text == "" and ui._fields["reason"].text == ""
        source.send_text("2\tFresh fixture reason\t\t\r")
        assert json.loads(fresh.result(5)["answer"])["reason"] == "Fresh fixture reason"


def test_owner_monitor_failure_closes_ui_and_pending_questions():
    fail = threading.Event()

    def owner_tick():
        if fail.is_set():
            raise ValueError("Fixture launch identity became invalid")

    with desk(owner_tick=owner_tick) as (ui, source, watcher):
        pending, _ = question(ui)
        open_form(ui, source, "Make a prediction")
        fail.set()
        assert pending.result(5) is None
        assert watcher.closed.wait(5)


def test_noninteractive_ui_never_waits_for_or_fabricates_an_answer():
    ui = ReviewUI(io.StringIO(), io.StringIO())
    assert not ui.interactive
    assert ui.question("/fixture", "", "Answer?", FixtureContext()) is None
    with pytest.raises(ValueError, match="requires a terminal"):
        ui.run(SimpleNamespace(closed=threading.Event()))
