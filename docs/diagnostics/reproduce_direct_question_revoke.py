"""Verify shared revoke cancels watcher questions, using test fixtures.

Run with Python 3.11+ and Scope's dependencies installed:
    python docs/diagnostics/reproduce_direct_question_revoke.py --source CHECKOUT

CHECKOUT is an explicit checkout/archive containing src/scope. The original
diagnostic reproduced a direct-reader cancellation gap at b13cf4cea546346ab56762fe60c71b0a6f6619cf.
This regression diagnostic imports the requested source without installing or
editing it, uses temporary configuration homes, and exercises a real localhost
watcher. Only terminal input is simulated; the watcher's real TerminalUI generates
and validates its reply tag. A separate caller-side reader is forbidden even with
TTY-like caller streams. No human is consulted.

Exit 1 / bug_reproduced means a pending question returned the labeled fixture
answer after confirmed watcher revocation. Expected fixed behavior invalidates
that pending question and returns an explicit error without the late answer
(exit 0 / late_answer_rejected). Exit 2 / diagnostic_error means the diagnostic
could not establish that scenario or observed a forbidden local-reader attempt.
This checks pending-answer cancellation, not consent validity after a completed
question or the complete cancellation contract.
This is a standalone diagnostic, not a skipped or expected-failure pytest test.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib
import io
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import threading
from unittest.mock import patch
import uuid


class DiagnosticError(RuntimeError):
    """A fixed diagnostic code, safe to report without private source paths."""


def diagnose(source: Path) -> tuple[dict, int]:
    source = source.expanduser().resolve()
    package = source / "src" / "scope"
    modules = ("learning_cli", "log", "runner", "ui", "watch", "watch_ui")
    if not all((package / (name + ".py")).is_file() for name in modules):
        raise DiagnosticError("source_missing_required_modules")
    if any(name == "scope" or name.startswith("scope.") for name in sys.modules):
        raise DiagnosticError("scope_already_imported_run_in_fresh_python")

    with TemporaryDirectory(prefix="scope-direct-revoke-test-fixture-") as temporary:
        root = Path(temporary).resolve(strict=True)
        environment = {
            name: value for name, value in os.environ.items()
            if not name.startswith(("SCOPE_", "CODEX_", "CLAUDE_")) and name != "CLAUDECODE"
        }
        for name in ("SCOPE_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
            directory = root / name.lower()
            directory.mkdir(mode=0o700)
            environment[name] = str(directory)

        with patch.dict(os.environ, environment, clear=True), \
             patch.object(sys, "path", [str(source / "src"), *sys.path]), \
             patch.object(sys, "dont_write_bytecode", True):
            loaded = {name: importlib.import_module("scope." + name) for name in modules}
            if any(Path(module.__file__).resolve() != package / (name + ".py")
                   for name, module in loaded.items()):
                raise DiagnosticError("import_did_not_use_requested_source")
            return _exercise(loaded, root)


def _exercise(modules: dict, root: Path) -> tuple[dict, int]:
    ui, watch_ui = modules["ui"], modules["watch_ui"]
    entered, release = threading.Event(), threading.Event()
    answer = "test_fixture: answer supplied after confirmed watcher revoke"

    class TaggedTerminalFixture(watch_ui.TerminalUI):
        """Simulated reader; uses the real tagged question/answer implementation."""

        interactive = True

        def __init__(self):
            super().__init__(io.StringIO(), io.StringIO())
            self.context = None

        def _line(self, context):
            self.context = context
            tags = re.findall(r"Reply with (\d+-[0-9a-f]{8}) followed by",
                              self.output.getvalue())
            if not tags:
                raise DiagnosticError("tagged_prompt_not_observed")
            entered.set()
            if not release.wait(timeout=4):
                raise DiagnosticError("fixture_release_timed_out")
            return tags[-1] + " " + answer

        def _discard(self):
            self._pending.clear()

    class ForbiddenLocalTerminal:
        """TTY-like caller stream that cannot supply or display a local prompt."""

        def isatty(self):
            return True

        def read(self, *args, **kwargs):
            raise DiagnosticError("local_terminal_read_forbidden")

        readline = read

        def write(self, *args, **kwargs):
            raise DiagnosticError("local_terminal_prompt_forbidden")

        def flush(self):
            pass

    terminal = TaggedTerminalFixture()
    local_terminal = ForbiddenLocalTerminal()
    session = "test-fixture-direct-revoke-" + str(uuid.uuid4())
    with patch.object(modules["runner"], "run", side_effect=DiagnosticError("probe_forbidden")) as run, \
         patch("subprocess.Popen", side_effect=DiagnosticError("process_forbidden")) as popen, \
         modules["watch"].Watcher(ui=terminal) as watcher, \
         patch.object(ui, "TerminalUI", create=True,
                      side_effect=DiagnosticError("local_terminal_reader_forbidden")) as local_reader, \
         patch.object(sys, "stdin", local_terminal), \
         ThreadPoolExecutor(max_workers=1) as pool:
        before = watcher.store.generation
        future = pool.submit(ui.ask, "test_fixture: prediction question", repo=str(root), timeout=5,
                             input_stream=local_terminal, output_stream=local_terminal)
        try:
            if not entered.wait(timeout=2) or future.done():
                raise DiagnosticError("watcher_question_not_pending")
            revoked = modules["learning_cli"].revoke(session)
            after = watcher.store.generation
            active_after_revoke = terminal.context.active()
        finally:
            release.set()
        response = future.result(timeout=2)
        if run.called or popen.called:
            raise DiagnosticError("execution_guard_was_triggered")
        if local_reader.called:
            raise DiagnosticError("local_terminal_reader_was_attempted")

    if revoked.get("revoked") is not True or revoked.get("recorded") is not True or after <= before:
        raise DiagnosticError("real_watcher_revocation_not_confirmed")
    if active_after_revoke:
        raise DiagnosticError("watcher_question_still_active_after_revoke")
    records = modules["log"].read(session)
    if [record.get("event") for record in records] != ["scopes_revoked"]:
        raise DiagnosticError("unexpected_fixture_events")
    if not isinstance(response, dict):
        raise DiagnosticError("unexpected_question_result")

    late_answer = response.get("answer") == answer
    rejected = "answer" not in response and isinstance(response.get("error"), str)
    if not late_answer and not rejected:
        raise DiagnosticError("unexpected_question_result")
    return {
        "evidence": "test_fixture",
        "status": "bug_reproduced" if late_answer else "late_answer_rejected",
        "real_watcher_revocation_confirmed": True,
        "watcher_generation": [before, after],
        "watcher_context_active_after_revoke": active_after_revoke,
        "local_terminal_reader_opened": False,
        "tagged_watcher_prompt_observed": True,
        "late_fixture_answer_returned": late_answer,
        "ui_reported_route": response.get("provenance"),
        "probes_or_child_processes_executed": 0,
        "human_input_used": False,
    }, 1 if late_answer else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--source", type=Path, required=True, help="explicit Scope checkout/archive")
    args = parser.parse_args()
    try:
        report, status = diagnose(args.source)
    except Exception as error:
        report = {"evidence": "test_fixture", "status": "diagnostic_error",
                  "error": str(error) if isinstance(error, DiagnosticError) else type(error).__name__}
        status = 2
    print(json.dumps(report, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
