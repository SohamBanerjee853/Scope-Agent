"""Diagnose shared revoke versus a pending direct question, using test fixtures.

Run with Python 3.11+ and Scope's dependencies installed:
    python docs/diagnostics/reproduce_direct_question_revoke.py --source CHECKOUT

CHECKOUT is an explicit checkout/archive containing src/scope. This diagnostic
was reproduced against b13cf4cea546346ab56762fe60c71b0a6f6619cf. It imports that
source without installing or editing it, uses temporary configuration homes,
and exercises a real localhost watcher. Only terminal input is simulated; the
real TerminalUI generates and validates its reply tag. No human is consulted.

Exit 1 / bug_reproduced means a pending direct question returned the labeled
fixture answer after confirmed watcher revocation. Expected fixed behavior is
to invalidate that pending question and return an explicit error without the
late answer (exit 0 / late_answer_rejected). This checks only that scenario,
not the complete cancellation contract. A fix that removes direct prompting or
changes these APIs may need an updated diagnostic (exit 2 / diagnostic_error).
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
        root = Path(temporary)
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

    class UnusedWatcherUI:
        interactive = False

    terminal = TaggedTerminalFixture()
    session = "test-fixture-direct-revoke-" + str(uuid.uuid4())
    with patch.object(modules["runner"], "run", side_effect=DiagnosticError("probe_forbidden")) as run, \
         patch("subprocess.Popen", side_effect=DiagnosticError("process_forbidden")) as popen, \
         modules["watch"].Watcher(ui=UnusedWatcherUI()) as watcher, \
         patch.object(ui, "TerminalUI", return_value=terminal), \
         patch.object(sys, "stdin", io.StringIO()), \
         ThreadPoolExecutor(max_workers=1) as pool:
        before = watcher.store.generation
        future = pool.submit(ui.ask, "test_fixture: prediction question", repo=str(root), timeout=5)
        try:
            if not entered.wait(timeout=2) or future.done():
                raise DiagnosticError("direct_question_not_pending")
            revoked = modules["learning_cli"].revoke(session)
            after = watcher.store.generation
            active_after_revoke = terminal.context.active()
        finally:
            release.set()
        response = future.result(timeout=2)
        if run.called or popen.called:
            raise DiagnosticError("execution_guard_was_triggered")

    if revoked.get("revoked") is not True or revoked.get("recorded") is not True or after <= before:
        raise DiagnosticError("real_watcher_revocation_not_confirmed")
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
        "direct_context_active_after_revoke": active_after_revoke,
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
