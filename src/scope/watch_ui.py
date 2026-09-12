"""Cancelable terminal questions. This module never executes a command.

Every prompt requires its displayed random reply tag. Late input from an older
prompt cannot answer a later one, even after cancellation and a new terminal read.
The watcher serializes all calls; no abandoned reader thread is created here.
"""

import json
import os
import secrets
import select
import sys
import time


def display_text(value: object) -> str:
    """Render untrusted control characters visibly, including terminal escapes."""
    return json.dumps(str(value), ensure_ascii=True)[1:-1]


class TerminalUI:
    def __init__(self, input_stream=None, output_stream=None):
        self.input = input_stream if input_stream is not None else sys.stdin
        self.output = output_stream if output_stream is not None else sys.stdout
        self._pending = bytearray()
        self._prompt_sequence = 0

    @property
    def interactive(self) -> bool:
        return bool(self.input.isatty() and self.output.isatty())

    def notice(self, message: str) -> None:
        self.output.write(display_text(message) + "\n")
        self.output.flush()

    def _discard(self) -> None:
        self._pending.clear()
        try:
            if os.name == "nt":
                import msvcrt
                while msvcrt.kbhit():
                    msvcrt.getwch()
            else:
                import termios
                termios.tcflush(self.input.fileno(), termios.TCIFLUSH)
        except (OSError, ValueError):
            pass

    def _line(self, context) -> str | None:
        if not self.interactive:
            return None
        if os.name == "nt":
            import msvcrt
            text = []
            while context.active():
                if not msvcrt.kbhit():
                    time.sleep(0.02)
                    continue
                char = msvcrt.getwch()
                if char in {"\x00", "\xe0"}:
                    msvcrt.getwch()  # Function/arrow key continuation.
                    continue
                if char in {"\r", "\n"}:
                    self.output.write("\n")
                    self.output.flush()
                    return "".join(text)
                if char in {"\x03", "\x04", "\x1a"}:
                    return None
                if char == "\b":
                    if text:
                        text.pop()
                        self.output.write("\b \b")
                elif char.isprintable() and len(text) < 16384:
                    text.append(char)
                    self.output.write(char)
                self.output.flush()
        else:
            descriptor = self.input.fileno()
            while context.active():
                if b"\n" in self._pending:
                    line, _, rest = self._pending.partition(b"\n")
                    self._pending[:] = rest
                    return line.decode("utf-8", errors="strict").rstrip("\r")
                ready, _, _ = select.select([descriptor], [], [], 0.05)
                if not ready:
                    continue
                chunk = os.read(descriptor, 4096)
                if not chunk:
                    return None
                self._pending.extend(chunk)
                if len(self._pending) > 16384:
                    self._discard()
                    return None
        self._discard()
        return None

    def _answer(self, instructions: str, context) -> str | None:
        if not self.interactive or not context.active():
            return None
        self._prompt_sequence += 1
        tag = f"{self._prompt_sequence}-{secrets.token_hex(4)}"
        self.notice(instructions)
        self.notice(f"Reply with {tag} followed by a space and your answer. A reply tag is valid for this prompt only.")
        while context.active():
            line = self._line(context)
            if line is None or not context.active():
                self._discard()
                return None
            if line == tag:
                return ""  # A deliberately empty human answer.
            if line.startswith(tag + " "):
                return line[len(tag) + 1:]
            self.notice(f"Ignored untagged or expired reply. Use {tag} for this prompt.")
        self._discard()
        return None

    def review(self, request, card, context) -> dict:
        self.notice(f"Permission review — session {request.session_id}, agent {request.agent_id or '(main)'}")
        self.notice(f"Directory: {request.cwd}; shell: {request.shell}")
        self.notice(f"Command (untrusted data): {request.command}")
        if request.description:
            self.notice(f"Description (untrusted data): {request.description}")
        if card is None:
            self.notice("No proposal card covers this context. You may approve this command once.")
        else:
            self.notice("Proposed card (not approved): " + json.dumps(card.to_dict(), ensure_ascii=True))
        answer = self._answer("Choose grant, edit, once, deny, abstain, or revoke. Grant requires a valid bounded card.", context)
        if answer == "edit":
            raw = self._answer("Enter the complete edited card as JSON: summary, commands, domains, budget.", context)
            if raw is None:
                return {"action": "abstain"}
            try:
                card_data = json.loads(raw)
            except (ValueError, TypeError):
                self.notice("Invalid card JSON; no approval recorded.")
                return {"action": "abstain"}
            return {"action": "grant", "card": card_data}
        return {"action": answer if answer in {"grant", "once", "deny", "abstain", "revoke"} else "abstain"}

    def question(self, repo: str, context_text: str, prompt: str, context) -> str | None:
        self.notice(f"Understanding question — repository: {repo}")
        self.notice(f"Context (untrusted data): {context_text}")
        self.notice(f"Question (untrusted data): {prompt}")
        return self._answer("Enter your own answer. This answer does not grant command permission.", context)
