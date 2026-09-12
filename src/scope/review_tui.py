"""A keyboard-first review desk owned by the watcher, never a command runner.

One persistent event loop reads the terminal. Each form and action belongs to an
internal ticket/view generation; cancellation discards the draft and queued keys.
Requests never become active automatically. F2 explicitly opens the next review;
ordinary typing and Enter in the inbox cannot approve or answer anything.
"""

from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
import json
import sys
import threading
from typing import Callable

from prompt_toolkit import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.input import create_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import DynamicContainer, HSplit, Layout, ScrollablePane, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.output import create_output
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Button, Label, TextArea

MAX_PENDING = 128
MAX_TEXT = 16384
MAX_ESSENTIAL = 128 * 1024
VALUE_TYPES = ("Number", "Text", "Boolean", "Null", "JSON")


def safe_text(value, *, maximum=MAX_TEXT):
    """Keep readable Unicode/newlines; neutralize terminal and bidi controls."""
    text = str(value)
    if len(text) > maximum:
        text = text[:maximum] + "… [details shortened]"
    return "".join(char if (char in "\n\t" or char.isprintable())
                   and char not in "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
                   else f"\\u{ord(char):04x}" for char in text)


@dataclass(eq=False)
class Ticket:
    kind: str
    context: object
    data: dict
    result: Future = field(default_factory=Future)


class ReviewUI:
    """Synchronous watcher facade over one main-thread prompt_toolkit app."""

    def __init__(self, input_stream=None, output_stream=None, *, input=None, output=None):
        self.input_stream = sys.stdin if input_stream is None else input_stream
        self.output_stream = sys.stdout if output_stream is None else output_stream
        self._input = input
        self._output = output
        self._injected = input is not None and output is not None
        self._loop = None
        self._running = threading.Event()
        self._ended = threading.Event()
        self._pending = deque()
        self._active = None
        self._epoch = 0
        self._status = "Ready when you are. Your agent's requests appear here."
        self._notice = ""
        self._details = False
        self._editing = False
        self._manual_scroll = False
        self._pane = None
        self._errors = {}
        self._fields = {}
        self._actions = {}
        self._value_type = "Number"
        self._watcher = None
        self._on_revoke = None
        self._revoking = False
        self._inbox_control = FormattedTextControl(self._inbox_text, focusable=True)
        self._inbox = Window(self._inbox_control, wrap_lines=True)
        self._body = self._inbox
        self._app = None

    @property
    def interactive(self):
        return self._injected or bool(self.input_stream.isatty() and self.output_stream.isatty())

    def notice(self, message):
        message = safe_text(message, maximum=2000)
        if self._loop is not None and self._running.is_set():
            self._loop.call_soon_threadsafe(self._set_notice, message)
        else:
            self._notice = message

    def _set_notice(self, message):
        self._notice = message
        self._redraw()

    def review(self, request, card, context):
        data = {"request": request, "card": card.to_dict() if card is not None else None}
        return self._wait(Ticket("permission", context, data)) or {"action": "abstain"}

    def question(self, repo, context_text, prompt, context):
        presentation = getattr(context, "presentation", None)
        if presentation is not None:
            from .review_presentation import validate

            presentation = validate(presentation, repo)
            kind = presentation["kind"]
            data = dict(presentation)
        else:
            kind = "question"
            data = {"repo": repo, "prompt": prompt, "details": context_text}
        return self._wait(Ticket(kind, context, data))

    def _wait(self, ticket):
        if not self.interactive or self._ended.is_set() or not ticket.context.active():
            return None
        while not self._running.wait(0.025):
            if self._ended.is_set() or not ticket.context.active():
                return None
        self._loop.call_soon_threadsafe(self._enqueue, ticket)
        while ticket.context.active() and not self._ended.is_set():
            try:
                answer = ticket.result.result(timeout=0.025)
                return answer if ticket.context.active() else None
            except FutureTimeout:
                continue
        if self._running.is_set():
            self._loop.call_soon_threadsafe(self._cancel, ticket, "Request cancelled or expired. Nothing was approved.")
        return None

    def _enqueue(self, ticket):
        if ticket.result.done() or not ticket.context.active() or len(self._pending) >= MAX_PENDING:
            if not ticket.result.done():
                ticket.result.set_result(None)
            return
        self._pending.append(ticket)
        self._redraw()

    def _inbox_text(self):
        count = sum(not ticket.result.done() for ticket in self._pending)
        title = f"{count} request waiting" if count == 1 else f"{count} requests waiting" if count else "All caught up"
        return [("class:title", "\n  " + title + "\n\n"),
                ("", "  Review a command, make a prediction,\n  or choose your next step.\n\n"),
                ("class:focus", "  F2  Review next  " if count else "  Waiting for your agent  "),
                ("class:muted", "\n\n  Each request opens only when you choose.\n  Typing here cannot approve a command.\n\n"),
                ("class:status", "  " + self._status + "\n"),
                ("class:muted", "\n  " + self._notice if self._notice else "")]

    def _redraw(self):
        if self._app is not None:
            self._app.invalidate()

    def _drain_keys(self):
        if self._app is None:
            return
        # flush_keys returns pending parsed escapes; never feed those keys into
        # the next form. This is performed only on the terminal's event loop.
        self._app.key_processor.empty_queue()
        self._app.input.read_keys()
        self._app.input.flush_keys()
        self._app.key_processor.empty_queue()

    def _focus(self, target):
        self._manual_scroll = False
        if self._app is not None:
            self._app.layout.focus(target)

    def _open_next(self):
        if self._active is not None or self._revoking:
            return
        while self._pending:
            ticket = self._pending.popleft()
            if not ticket.result.done() and ticket.context.active():
                self._active = ticket
                self._details = False
                self._value_type = "Number"
                self._form(ticket)
                return
            if not ticket.result.done():
                ticket.result.set_result(None)
        self._redraw()

    def _current(self, ticket, epoch):
        return (self._active is ticket and self._epoch == epoch
                and not ticket.result.done() and ticket.context.active())

    def _resolve(self, ticket, value, epoch, message):
        if not self._current(ticket, epoch):
            self._cancel(ticket, "Request cancelled or expired. Nothing was approved.")
            return
        ticket.result.set_result(value)
        self._back_to_inbox(message)

    def _back_to_inbox(self, message):
        self._epoch += 1
        self._active = None
        self._fields = {}
        self._actions = {}
        self._errors = {}
        self._body = self._inbox
        self._status = message
        self._focus(self._inbox_control)
        self._drain_keys()
        self._redraw()

    def _cancel(self, ticket, message):
        if not ticket.result.done():
            ticket.result.set_result(None)
        if self._active is ticket:
            self._back_to_inbox(message)
        else:
            try:
                self._pending.remove(ticket)
            except ValueError:
                pass
            self._redraw()

    def _label(self, text, style=""):
        return Label(safe_text(text), style=style)

    def _text_block(self, text, *, maximum=MAX_ESSENTIAL):
        return Window(FormattedTextControl(safe_text(text, maximum=maximum)), wrap_lines=True,
                      height=Dimension(min=1), dont_extend_height=True)

    def _field(self, key, label, *, text="", height=1, hint=""):
        area = TextArea(text=text, height=height, multiline=height > 1, wrap_lines=True,
                        style="class:field", name=key, complete_while_typing=False,
                        accept_handler=lambda buffer: False)
        def restore_focus(buffer):
            self._manual_scroll = False
        area.buffer.on_text_changed += restore_focus
        area.buffer.on_cursor_position_changed += restore_focus
        self._fields[key] = area
        return [self._label(label, "class:label"), area,
                Label(lambda k=key, h=hint: [("class:error" if self._errors.get(k) else "class:muted",
                                             safe_text(self._errors.get(k) or h))])]

    def _button(self, title, ticket, epoch, callback):
        def action():
            if self._current(ticket, epoch):
                if self._manual_scroll:
                    # A scroll may have moved the focused action off-screen.
                    # First restore its visibility; require a fresh activation.
                    self._manual_scroll = False
                    self._drain_keys()
                    self._redraw()
                    return
                callback()
        self._actions[title] = action
        return Button(title, handler=action, width=min(36, max(18, len(title) + 4)))

    def _details_block(self, ticket):
        if not self._details:
            return []
        details = ticket.data.get("details", "")
        if ticket.kind == "permission":
            request = ticket.data["request"]
            details = (f"Session: {request.session_id}\nAgent: {request.agent_id or 'main'}\n"
                       f"Description: {request.description or 'None supplied'}")
        controls = []
        if ticket.kind == "prediction":
            controls += self._execution(ticket.data["execution"])
        if ticket.kind == "probe_approval":
            prediction = ticket.data["saved_prediction"]
            controls += [self._label("Your saved prediction", "class:label"),
                         self._text_block(json.dumps(prediction["value"], ensure_ascii=False, allow_nan=False)),
                         self._text_block("Why: " + prediction["reason"])]
            if prediction["assistance"]:
                controls += [self._text_block("Help used: " + prediction["assistance"])]
        return controls + [self._label("Details · untrusted supplied evidence", "class:label"),
                           self._text_block(details, maximum=MAX_TEXT)]

    def _execution(self, execution):
        argv = execution["argv"]
        # One argument per line preserves exact argument boundaries without an
        # escaped JSON document or an executable shell reconstruction.
        command = "\n".join(f"{index + 1}. " + (json.dumps(argument, ensure_ascii=False)
                            if not argument or any(char.isspace() for char in argument)
                            else argument) for index, argument in enumerate(argv))
        return [self._label("Command arguments", "class:label"), self._text_block(command),
                self._label("Directory", "class:label"), self._text_block(execution["cwd"]),
                self._label(f"{execution['shell']} · up to {execution['timeout']} seconds", "class:muted")]

    def _form(self, ticket, *, edit=False):
        self._editing = edit
        self._epoch += 1
        epoch = self._epoch
        self._fields, self._actions, self._errors = {}, {}, {}
        controls = []
        data = ticket.data
        first = None
        if edit:
            controls += [self._label("Edit permission scope", "class:title"),
                         self._label("Review every field, then explicitly approve.", "class:muted")]
            card = data["card"] or {"summary": "", "commands": [], "domains": [], "budget": 1}
            for key, label, height in (("summary", "Purpose", 1), ("commands", "Command patterns · one per line", 3),
                                       ("domains", "Domains · optional, one per line", 2), ("budget", "Number of commands · 1–100", 1)):
                text = "\n".join(card[key]) if isinstance(card[key], list) else str(card[key])
                controls += self._field(key, label, text=text, height=height)
            approve = self._button("Approve edited scope", ticket, epoch, lambda: self._edited(ticket, epoch))
            cancel = self._button("Cancel editing", ticket, epoch, lambda: self._form(ticket))
            controls += [approve, cancel]
            first = self._fields["summary"]
        elif ticket.kind == "permission":
            request, card = data["request"], data["card"]
            controls += [self._label("Permission review", "class:title"),
                         self._label("Your agent wants to run a command.", "class:muted"),
                         self._label("Command · supplied by your agent", "class:label"), self._text_block(request.command),
                         self._label("Directory", "class:label"), self._text_block(request.cwd),
                         self._label(f"Shell: {request.shell}", "class:muted")]
            if card:
                controls += [self._label("Proposed scope · not yet approved", "class:label"),
                             self._text_block(card["summary"]),
                             self._text_block("Patterns: " + ", ".join(card["commands"])),
                             self._label(f"Budget: {card['budget']} commands · expires in 15 minutes", "class:muted")]
                if card["domains"]:
                    controls += [self._text_block("Domains: " + ", ".join(card["domains"]))]
            else:
                controls += [self._label("No reusable scope was proposed.", "class:muted")]
            choices = [("Pass to host", "abstain"), ("Allow once", "once")]
            if card:
                choices += [("Approve scope", "grant"), ("Edit scope", "edit")]
            choices += [("Deny", "deny")]
            for label, action in choices:
                callback = (lambda: self._form(ticket, edit=True)) if action == "edit" else (
                    lambda a=action, label=label: self._resolve(ticket, {"action": a}, epoch, f"Sent your choice: {label}."))
                button = self._button(label, ticket, epoch, callback)
                first = first or button
                controls.append(button)
        elif ticket.kind == "prediction":
            controls += [self._label("Make a prediction", "class:title"), self._text_block(data["question"]),
                         self._label("Predict first. F3 shows the planned probe and context.", "class:muted"),
                         Label(lambda: "Value type: " + self._value_type + "   [F4 change]", style="class:label")]
            controls += self._field("value", f"Predicted value · {data['field']}")
            controls += self._field("reason", "Why do you expect that?", height=3)
            controls += self._field("assistance", "Help used · optional", height=2)
            controls += [self._button("Save prediction", ticket, epoch, lambda: self._prediction(ticket, epoch)),
                         self._button("Skip", ticket, epoch, lambda: self._resolve(ticket, None, epoch, "Prediction skipped."))]
            first = self._fields["value"]
        elif ticket.kind == "probe_approval":
            controls += [self._label("Run this probe?", "class:title"),
                         self._label("A separate choice. Your prediction is already saved.", "class:muted"),
                         *self._execution(data["execution"]),
                         self._label(f"Review tier: {data['classification']['name']}", "class:label"),
                         self._text_block(data["classification"]["reason"]),
                         self._label("Run once only · no reusable permission grant", "class:muted")]
            first = self._button("Decline", ticket, epoch, lambda: self._resolve(ticket, "false", epoch, "Probe declined. Nothing will run."))
            controls += [first, self._button("Run once", ticket, epoch,
                                             lambda: self._resolve(ticket, "true", epoch, "Sent your consent for this one probe."))]
        elif ticket.kind == "next_task":
            controls += [self._label("Choose your next step", "class:title"),
                         self._label("Select an instruction or leave it for later.", "class:muted")]
            for key, label in (("smaller", "Smaller step"), ("larger", "Larger step")):
                if key in data["options"]:
                    controls += [self._label(label, "class:label"), self._text_block(data["options"][key])]
            for key, label in (("defer", "Defer"), ("smaller", "Smaller step"), ("larger", "Larger step")):
                if key in data["options"]:
                    button = self._button(label, ticket, epoch,
                                          lambda k=key: self._resolve(ticket, k, epoch, "Next-step choice sent. No execution was authorized."))
                    first = first or button
                    controls.append(button)
        else:
            controls += [self._label("A question for you", "class:title"), self._text_block(data["prompt"]),
                         self._label("Your answer does not grant permission.", "class:muted")]
            controls += self._field("answer", "Your answer", height=4)
            controls += [self._button("Send answer", ticket, epoch, lambda: self._answer(ticket, epoch)),
                         self._button("Skip", ticket, epoch, lambda: self._resolve(ticket, None, epoch, "Question skipped."))]
            first = self._fields["answer"]
        controls += self._details_block(ticket)
        self._body = HSplit(controls, padding=0)
        self._focus(first)
        self._drain_keys()
        self._redraw()

    def _error(self, field, message):
        self._errors[field] = message
        self._focus(self._fields[field])
        self._redraw()

    def _prediction(self, ticket, epoch):
        from .ui import parse_json

        raw = self._fields["value"].text
        try:
            if len(raw.encode("utf-8")) > MAX_TEXT or "\0" in raw:
                raise ValueError("Keep the value below 16 KiB.")
            if self._value_type == "Text":
                value = raw
            elif self._value_type == "Null":
                value = None
            else:
                value = parse_json(raw)
                if self._value_type == "Number" and type(value) not in (int, float):
                    raise ValueError("Enter a number, such as 2 or 2.5.")
                if self._value_type == "Boolean" and type(value) is not bool:
                    raise ValueError("Enter true or false.")
                if self._value_type == "JSON" and type(value) not in (dict, list):
                    raise ValueError("Enter a JSON object or list, or change the value type.")
        except (ValueError, UnicodeError):
            self._error("value", {"Number": "Enter a number, such as 2 or 2.5.", "Boolean": "Enter true or false.",
                                  "JSON": "Enter a valid JSON object/list with unique keys and finite values."}.get(self._value_type, "Enter a valid value below 16 KiB."))
            return
        reason = self._fields["reason"].text
        assistance = self._fields["assistance"].text
        if not reason.strip() or len(reason) > 2000 or "\0" in reason:
            self._error("reason", "Add your reason (1–2,000 characters).")
            return
        if len(assistance) > 2000 or "\0" in assistance:
            self._error("assistance", "Keep help notes within 2,000 characters.")
            return
        answer = json.dumps({"value": value, "reason": reason, "assistance": assistance}, ensure_ascii=False, allow_nan=False)
        try:
            parse_json(answer)
        except (ValueError, UnicodeError):
            self._error("value", "Keep the complete prediction below 16 KiB.")
            return
        self._resolve(ticket, answer, epoch, "Prediction sent. Running still needs a separate choice.")

    def _edited(self, ticket, epoch):
        from .grants import validate_card

        try:
            budget = self._fields["budget"].text.strip()
            if not budget.isascii() or not budget.isdecimal():
                raise ValueError("Budget must be an integer from 1 to 100.")
            card = {"summary": self._fields["summary"].text,
                    "commands": [line.strip() for line in self._fields["commands"].text.splitlines() if line.strip()],
                    "domains": [line.strip() for line in self._fields["domains"].text.splitlines() if line.strip()],
                    "budget": int(budget)}
            valid = validate_card(card)
        except (ValueError, OverflowError) as error:
            self._error("summary", "Scope needs correction: " + safe_text(error, maximum=160))
            return
        self._resolve(ticket, {"action": "grant", "card": valid.to_dict()}, epoch, "Sent your edited scope for approval.")

    def _answer(self, ticket, epoch):
        answer = self._fields["answer"].text
        if len(answer) > MAX_TEXT or "\0" in answer:
            self._error("answer", "Keep the answer below 16,384 characters.")
            return
        self._resolve(ticket, answer, epoch, "Your answer was sent. It grants no command permission.")

    async def _refresh(self):
        while True:
            for ticket in (*self._pending, *((self._active,) if self._active else ())):
                if not ticket.context.active():
                    self._cancel(ticket, "Request cancelled or expired. Nothing was approved.")
            if self._watcher.closed.is_set():
                self._app.exit()
                return
            await asyncio.sleep(0.05)

    async def _tick(self, callback):
        while True:
            try:
                await asyncio.to_thread(callback)
            except Exception:
                self._watcher.closed.set()
                return
            await asyncio.sleep(0.1)

    async def _revoke(self):
        if self._revoking or self._on_revoke is None:
            return
        self._revoking = True
        for ticket in (*self._pending, *((self._active,) if self._active else ())):
            self._cancel(ticket, "Cancelling pending requests…")
        try:
            await asyncio.to_thread(self._on_revoke)
            self._status = "Permission scopes revoked. Pending answers were cancelled."
        except Exception:
            self._status = "Revocation could not be confirmed. Stop dependent work."
        finally:
            self._revoking = False
            self._redraw()

    def run(self, watcher, owner_tick: Callable | None = None, on_revoke: Callable | None = None):
        """Own the terminal until watcher exit; callbacks perform no host launch."""
        if not self.interactive:
            raise ValueError("interactive review requires a terminal")
        self._watcher, self._on_revoke = watcher, on_revoke
        bindings = KeyBindings()

        @bindings.add("f2", eager=True)
        def open_next(event):
            self._open_next()

        @bindings.add("tab", eager=True)
        def next_field(event):
            if self._active is not None:
                self._manual_scroll = False
                event.app.layout.focus_next()

        @bindings.add("s-tab", eager=True)
        def previous_field(event):
            if self._active is not None:
                self._manual_scroll = False
                event.app.layout.focus_previous()

        @bindings.add("pageup", eager=True)
        @bindings.add("pagedown", eager=True)
        def scroll(event):
            from prompt_toolkit.keys import Keys

            self._manual_scroll = True
            distance = max(1, event.app.output.get_size().rows - 8)
            direction = -1 if event.key_sequence[-1].key == Keys.PageUp else 1
            self._pane.vertical_scroll = max(0, self._pane.vertical_scroll + direction * distance)
            self._redraw()

        @bindings.add("enter", filter=Condition(lambda: self._active is None), eager=True)
        def inactive_enter(event):
            pass

        @bindings.add("escape", eager=True)
        def cancel(event):
            if self._active is not None:
                self._cancel(self._active, "Request skipped. Nothing was approved.")

        @bindings.add("f3", eager=True)
        def details(event):
            if self._active is not None:
                self._details = not self._details
                ticket = self._active
                # Details must not discard typed drafts or reset the chosen type.
                drafts = {key: (area.text, area.buffer.cursor_position) for key, area in self._fields.items()}
                focused = next((key for key, area in self._fields.items()
                                if event.app.layout.has_focus(area)), None)
                errors = self._errors.copy()
                self._form(ticket, edit=self._editing)
                self._errors.update(errors)
                for key, (text, cursor) in drafts.items():
                    if key in self._fields:
                        self._fields[key].text = text
                        self._fields[key].buffer.cursor_position = cursor
                if focused is not None:
                    self._focus(self._fields[focused])

        @bindings.add("f4", eager=True)
        def value_type(event):
            if self._active is not None and self._active.kind == "prediction":
                self._value_type = VALUE_TYPES[(VALUE_TYPES.index(self._value_type) + 1) % len(VALUE_TYPES)]
                self._errors.pop("value", None)
                self._redraw()

        @bindings.add("c-r", eager=True)
        def revoke(event):
            event.app.create_background_task(self._revoke())

        @bindings.add("c-c", eager=True)
        def close(event):
            watcher.closed.set()
            event.app.exit()

        header = Window(FormattedTextControl([("class:brand", " SCOPE "), ("class:muted", "  Your review desk")]), height=1)
        footer = Window(FormattedTextControl(lambda: [("class:muted", " F2 Review  F3 Details  F4 Type\n Tab Move  Enter Choose  Esc Skip\n PgUp/Dn Scroll  Ctrl-R Revoke  Ctrl-C Exit")]), height=3, wrap_lines=True)
        self._pane = ScrollablePane(DynamicContainer(lambda: self._body), show_scrollbar=True, display_arrows=True,
                                    keep_cursor_visible=Condition(lambda: not self._manual_scroll),
                                    keep_focused_window_visible=Condition(lambda: not self._manual_scroll))
        root = HSplit([header, Window(height=1, char="─", style="class:border"), self._pane, Window(height=1), footer])
        style = Style.from_dict({"": "bg:#101820 #edf2f7", "brand": "bold #8ce2db", "title": "bold #edf2f7",
                                 "label": "bold #b8d8e8", "muted": "#b7c3cf", "status": "#8ce2db",
                                 "border": "#536779", "field": "bg:#203242 #ffffff",
                                 "field focused": "bg:#29465b #ffffff", "button": "bg:#263747 #edf2f7",
                                 "button.focused": "bg:#9ee8dd #102028 bold", "focus": "bg:#9ee8dd #102028 bold",
                                 "error": "#ffb7aa bold", "scrollbar.background": "bg:#203242",
                                 "scrollbar.button": "bg:#9bb8ca"})
        self._app = Application(layout=Layout(root, focused_element=self._inbox_control), key_bindings=bindings,
                                style=style, full_screen=True, mouse_support=False,
                                input=self._input or create_input(self.input_stream),
                                output=self._output or create_output(self.output_stream),
                                terminal_size_polling_interval=0.2)

        def ready():
            self._loop = asyncio.get_running_loop()
            self._running.set()
            self._app.create_background_task(self._refresh())
            if owner_tick is not None:
                self._app.create_background_task(self._tick(owner_tick))

        try:
            self._app.run(pre_run=ready, set_exception_handler=False)
        finally:
            self._ended.set()
            self._running.clear()
            for ticket in (*self._pending, *((self._active,) if self._active else ())):
                if not ticket.result.done():
                    ticket.result.set_result(None)
