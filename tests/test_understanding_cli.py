"""A3 CLI and human transport tests with labeled fixtures, no live agent hosts."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from scope import ipc, learning, learning_cli, log, storage, ui, watch_ui
from scope.watch import Watcher


@pytest.fixture(autouse=True)
def clean_identity(monkeypatch):
    for name in ("CODEX_THREAD_ID", "SCOPE_LAUNCH_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "project with spaces"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "app.py").write_text("def total():\n    return 2\n", encoding="utf-8")
    return root


class ScriptedTerminal(watch_ui.TerminalUI):
    """Explicitly labeled reader fixture; no terminal or human is impersonated."""
    interactive = True

    def __init__(self, lines):
        super().__init__(io.StringIO(), io.StringIO())
        self.lines = deque(lines)
        self.discarded = False
        self.discarded_event = threading.Event()

    def _line(self, context):
        line = self.lines.popleft() if self.lines else None
        return line(self) if callable(line) else line

    def tagged(self, answer):
        tags = re.findall(r"Reply with (\d+-[0-9a-f]{8}) followed by", self.output.getvalue())
        assert tags, "the real watcher must display a fresh reply tag"
        return tags[-1] + " " + answer

    def _discard(self):
        self.discarded = True
        self.discarded_event.set()


class ForbiddenCallerTerminal:
    """A TTY-like caller stream must never become a separate question reader."""

    def isatty(self):
        return True

    def read(self, *args, **kwargs):
        pytest.fail("caller-side terminal input is forbidden")

    readline = read
    fileno = read

    def write(self, *args, **kwargs):
        pytest.fail("caller-side question output is forbidden")

    def flush(self):
        pass


def test_start_checkpoint_knowledge_cli_uses_real_a1_evidence(repo, capsys):
    assert learning_cli.main(["start", "Repair", "理解", "-C", str(repo), "--session", "fixture-cli", "--json"]) == 0
    started = json.loads(capsys.readouterr().out)
    assert started["description"] == "Repair 理解"
    assert started["session_id"] == "fixture-cli"
    assert "app.py" in started["source"]["files"]
    assert [event["event"] for event in log.read("fixture-cli")] == ["task_start"]
    (repo / "app.py").write_text("def total():\n    return 1\n", encoding="utf-8")
    assert learning_cli.main(["checkpoint", started["task_id"], "-C", str(repo), "--note", "stable retry", "--json"]) == 0
    checkpoint = json.loads(capsys.readouterr().out)
    assert checkpoint["changes"]["modified"] == ["app.py"]
    assert learning_cli.main(["knowledge", "--task", started["task_id"], "-C", str(repo), "--json"]) == 0
    known = json.loads(capsys.readouterr().out)
    assert known["observations"] == []
    assert [event["event"] for event in log.read("fixture-cli")] == ["task_start", "task_checkpoint"]


def test_start_default_session_and_human_output_are_honest(repo, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_THREAD_ID", "fixture-host")
    assert learning_cli.main(["start", "Repair retry", "-C", str(repo)]) == 0
    output = capsys.readouterr().out
    assert "fixture-host" in output
    assert "No prediction, consent or probe has occurred" in output
    assert len(log.read("fixture-host")) == 1


@pytest.mark.parametrize("command", ["checkpoint", "knowledge"])
def test_task_cli_requires_exactly_one_task_identifier(command, repo):
    for args in (["-C", str(repo)], ["first", "--task", "second", "-C", str(repo)]):
        with pytest.raises(SystemExit) as error:
            learning_cli.main([command, *args])
        assert error.value.code == 2


@pytest.mark.parametrize("command", ["start", "checkpoint", "knowledge", "revoke", "skill"])
def test_a3_command_help_is_available_without_state(command, capsys):
    with pytest.raises(SystemExit) as error:
        learning_cli.main([command, "--help"])
    assert error.value.code == 0
    assert "scope " + command in capsys.readouterr().out


@pytest.mark.parametrize("command", ["check", "next"])
def test_a2_adapters_fail_explicitly_without_guessing_engine_api(command, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("pending A2 command must not execute or create evidence")

    monkeypatch.setattr(learning, "start", forbidden)
    monkeypatch.setattr(learning, "checkpoint", forbidden)
    assert learning_cli.main([command, "--task", "fixture", "--json"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "pending Arjun's A2" in output.err
    assert learning_cli.main([command, "--help"]) == 0
    assert "No engine API is guessed" in capsys.readouterr().out


def test_invalid_repo_task_and_launch_identity_fail_honestly(repo, tmp_path, monkeypatch, capsys):
    assert learning_cli.main(["start", "fixture", "-C", str(tmp_path)]) == 1
    assert "scope start:" in capsys.readouterr().err
    assert learning_cli.main(["knowledge", "missing-task", "-C", str(repo)]) == 1
    assert "unavailable" in capsys.readouterr().err
    monkeypatch.setenv("SCOPE_LAUNCH_ID", "unregistered-launch")
    assert learning_cli.main(["start", "fixture", "-C", str(repo)]) == 1
    assert "native launch identity" in capsys.readouterr().err


def test_checkpoint_and_knowledge_render_without_inventing_observations(repo, capsys):
    task = learning.start(repo, "fixture task", session_id="fixture-render")
    assert learning_cli.main(["checkpoint", task["task_id"], "-C", str(repo)]) == 0
    assert "Saved checkpoint" in capsys.readouterr().out
    assert learning_cli.main(["knowledge", task["task_id"], "-C", str(repo)]) == 0
    output = capsys.readouterr().out
    assert "0 saved observations" in output
    assert "not a mastery score or permission" in output


def test_unknown_and_missing_cli_commands_fail(capsys):
    assert learning_cli.main([]) == 1
    assert learning_cli.main(["unknown"]) == 1
    assert capsys.readouterr().err.count("scope understanding:") == 2


@pytest.mark.parametrize("reply,expected", [
    ({"revoked": True}, True), ({"revoked": False}, False), (None, None),
    ({"revoked": 1}, None), ({"revoked": True, "grant": "allow"}, None), ({"error": "no listener"}, None),
])
def test_revoke_records_only_confirmed_transport_outcome(reply, expected):
    messages = []

    def fixture(message):
        messages.append(message)
        return reply

    result = learning_cli.revoke("fixture-revoke", exchange=fixture)
    assert result["revoked"] is expected
    assert result["recorded"] is True
    assert messages == [{"kind": "revoke"}]
    fields = log.read("fixture-revoke")[0]["fields"]
    assert fields["session"] == "fixture-revoke"
    assert fields["revoked"] is expected
    assert fields["source"] == "understanding_cli"


def test_revoke_does_not_clear_saved_debugging_state(repo):
    task = learning.start(repo, "fixture task", session_id="fixture-revoke")
    before = storage.load(repo)
    result = learning_cli.revoke(task["session_id"], exchange=lambda message: {"revoked": True})
    assert result["revoked"] is True
    assert storage.load(repo) == before


def test_revoke_log_failure_reports_clearing_separately(monkeypatch):
    def broken_log(*args, **kwargs):
        raise OSError("labeled event failure")

    monkeypatch.setattr(log, "append", broken_log)
    result = learning_cli.revoke("fixture-revoke", exchange=lambda message: {"revoked": True})
    assert result["revoked"] is True
    assert result["recorded"] is False
    assert "could not be recorded" in result["error"]


def test_revoke_cli_failure_and_success_exit_status(monkeypatch, capsys):
    from scope import ipc

    monkeypatch.setattr(ipc, "exchange", lambda message: None)
    assert learning_cli.main(["revoke", "--session", "fixture-cli", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["revoked"] is None
    monkeypatch.setattr(ipc, "exchange", lambda message: {"revoked": True})
    assert learning_cli.main(["revoke", "--session", "fixture-cli"]) == 0
    assert "watcher confirmed revocation" in capsys.readouterr().out


def test_revoke_in_unregistered_launch_never_contacts_watcher(monkeypatch):
    monkeypatch.setenv("SCOPE_LAUNCH_ID", "fixture-unregistered")
    with pytest.raises(storage.StateError):
        learning_cli.revoke(exchange=lambda message: pytest.fail("must not borrow parent watcher"))


@pytest.mark.parametrize("shell,repo", [("posix", "/tmp/project with spaces"),
                                       ("powershell", "C:\\project with spaces")])
def test_headless_question_exact_protocol_and_actual_route(shell, repo):
    incoming = io.StringIO("must not read scripted stdin as a human answer")
    calls = []

    def fixture(message, **kwargs):
        calls.append((message, kwargs))
        return {"answer": "Labeled fixture: 理解"}

    result = ui.ask("Predict the total", repo=repo, context=shell + " fixture",
                    input_stream=incoming, output_stream=io.StringIO(), exchange=fixture)
    assert result == {"answer": "Labeled fixture: 理解", "provenance": "human_ipc"}
    assert incoming.tell() == 0
    assert calls == [({"kind": "question", "repo": repo, "context": shell + " fixture",
                       "prompt": "Predict the total"}, {"timeout": 105, "home": None})]


@pytest.mark.parametrize("response", [None, [], {}, {"error": "cancelled"}, {"answer": None},
                                    {"answer": "yes", "behavior": "allow"}, {"answer": ""},
                                    {"answer": " "}, {"answer": "x" * 16385}, {"answer": "\ud800"}])
def test_missing_invalid_or_empty_answers_never_become_human_answers(response):
    result = ui.ask("Predict", repo="fixture", input_stream=io.StringIO(), output_stream=io.StringIO(),
                    exchange=lambda *args, **kwargs: response)
    assert "error" in result
    assert "answer" not in result


@pytest.mark.parametrize("timeout", [0, -1, True, 106, float("inf"), float("nan"), "105"])
def test_question_invalid_deadline_never_reads_or_sends(timeout):
    result = ui.ask("Predict", repo="fixture", timeout=timeout,
                    exchange=lambda *args, **kwargs: pytest.fail("must not send"))
    assert "error" in result


@pytest.mark.parametrize("field,value", [("repo", ""), ("repo", "x" * 4097), ("context", None),
                                        ("context", "x" * 16385), ("prompt", ""), ("prompt", "\0")])
def test_question_invalid_text_never_sends(field, value):
    arguments = {"repo": "fixture", "context": "", "prompt": "Predict", field: value}
    result = ui.ask(**arguments, exchange=lambda *args, **kwargs: pytest.fail("must not send"))
    assert "error" in result


def test_interactive_caller_uses_only_tagged_watcher_reader(monkeypatch):
    terminal = ScriptedTerminal(["old-tag unrelated answer",
                                 lambda terminal: terminal.tagged("Labeled fixture: 2")])
    caller_terminal = ForbiddenCallerTerminal()
    monkeypatch.setattr(sys, "stdin", caller_terminal)
    with Watcher(ui=terminal) as watcher:
        result = ui.ask("Predict\x1b[2J", repo="fixture", context="source\x1b[31m",
                        input_stream=caller_terminal, output_stream=caller_terminal)
        assert watcher.store.sessions() == ()
    assert result == {"answer": "Labeled fixture: 2", "provenance": "human_ipc"}
    output = terminal.output.getvalue()
    assert "Ignored untagged or expired" in output
    assert "\x1b" not in output
    assert "does not grant command permission" in output


@pytest.mark.parametrize("control", ["revoke", "shutdown"])
def test_interactive_caller_rejects_late_tagged_answer_after_shared_cancellation(control, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    terminal = ScriptedTerminal([])
    caller_terminal = ForbiddenCallerTerminal()
    contexts = []

    def line(context):
        contexts.append(context)
        entered.set()
        assert release.wait(timeout=4), "test fixture was not released"
        return terminal.tagged("Labeled late fixture after shared cancellation")

    terminal._line = line
    monkeypatch.setattr(sys, "stdin", caller_terminal)
    with Watcher(ui=terminal) as watcher, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(ui.ask, "Predict", repo="fixture", timeout=5,
                             input_stream=caller_terminal, output_stream=caller_terminal)
        try:
            assert entered.wait(timeout=2)
            assert not future.done()
            if control == "revoke":
                assert learning_cli.revoke("fixture-tagged-revoke")["revoked"] is True
            else:
                assert ipc.exchange({"kind": "shutdown"}, timeout=2) == {"stopped": True}
            assert not contexts[0].active()
        finally:
            release.set()
        result = future.result(timeout=2)
        assert terminal.discarded_event.wait(timeout=2)
        assert "error" in result and "answer" not in result
        assert watcher.store.sessions() == ()


@pytest.mark.parametrize("watcher_present", [False, True], ids=["missing", "noninteractive"])
def test_interactive_caller_without_human_watcher_gets_actionable_error(watcher_present, monkeypatch):
    caller_terminal = ForbiddenCallerTerminal()
    monkeypatch.setattr(sys, "stdin", caller_terminal)
    reviewer = watch_ui.TerminalUI(io.StringIO(), io.StringIO())
    with Watcher(ui=reviewer) if watcher_present else nullcontext():
        result = ui.ask("Predict", repo="fixture", timeout=2,
                        input_stream=caller_terminal, output_stream=caller_terminal)
    assert "answer" not in result
    assert "start scope watch in an interactive terminal" in result["error"]
    assert reviewer.output.getvalue() == ""


def test_caller_cancellation_during_exchange_suppresses_returned_answer():
    cancelled = threading.Event()

    def cancelled_exchange(*args, **kwargs):
        cancelled.set()
        return {"answer": "Labeled fixture returned after caller cancellation"}

    result = ui.ask("Predict", repo="fixture", cancelled=cancelled, exchange=cancelled_exchange)
    assert "error" in result and "answer" not in result


def test_pre_cancelled_question_and_transport_exception_have_no_answer():
    cancelled = threading.Event()
    cancelled.set()
    assert "error" in ui.ask("Predict", repo="fixture", cancelled=cancelled,
                             exchange=lambda *args, **kwargs: pytest.fail("must not send"))

    def failure(*args, **kwargs):
        raise OSError("labeled transport failure")

    assert "answer" not in ui.ask("Predict", repo="fixture", input_stream=io.StringIO(),
                                   output_stream=io.StringIO(), exchange=failure)


def test_late_headless_response_cannot_answer_expired_question():
    def late_fixture(*args, **kwargs):
        time.sleep(0.02)
        return {"answer": "Labeled late fixture"}

    result = ui.ask("Predict", repo="fixture", input_stream=io.StringIO(), output_stream=io.StringIO(),
                    timeout=0.005, exchange=late_fixture)
    assert "answer" not in result
    assert "timed out" in result["error"]


def test_real_watcher_answer_has_ipc_provenance_without_permission_semantics():
    class ScriptedUI:
        interactive = True

        def question(self, repo, context_text, prompt, context):
            return "Labeled fixture answer"

    with Watcher(ui=ScriptedUI()) as watcher:
        result = ui.ask("Predict", repo="fixture", input_stream=io.StringIO(), output_stream=io.StringIO())
        assert result == {"answer": "Labeled fixture answer", "provenance": "human_ipc"}
        assert watcher.store.sessions() == ()


def test_real_question_transport_never_mutates_grants_and_revoke_cancels_it():
    entered = threading.Event()

    class ScriptedUI:
        interactive = True

        def question(self, repo, context_text, prompt, context):
            entered.set()
            while context.active():
                time.sleep(0.005)
            return "Labeled late fixture"

    with Watcher(ui=ScriptedUI()) as watcher, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(ui.ask, "Predict", repo="fixture", input_stream=io.StringIO(),
                             output_stream=io.StringIO())
        assert entered.wait(timeout=1)
        assert watcher.store.sessions() == ()
        revoked = learning_cli.revoke("fixture-understanding")
        assert revoked["revoked"] is True
        answer = future.result(timeout=2)
        assert "answer" not in answer
        assert watcher.store.sessions() == ()
    records = log.read("fixture-understanding")
    assert records[-1]["event"] == "scopes_revoked"


def test_show_escapes_untrusted_terminal_controls():
    output = io.StringIO()
    ui.show("Labeled context\x1b[2J\rforge", output_stream=output)
    assert "\x1b" not in output.getvalue()
    assert r"\u001b" in output.getvalue()


@pytest.mark.parametrize("project_name", ["posix project with spaces", "PowerShell project 'quoted'"])
def test_skill_installs_exact_packaged_utf8_once_and_dry_run_writes_nothing(tmp_path, project_name):
    project = tmp_path / project_name
    project.mkdir()
    report = learning_cli.install_skill(project, dry_run=True)
    assert report["changed"] is True
    assert not (project / ".agents").exists()
    report = learning_cli.install_skill(project)
    target = project / ".agents" / "skills" / "scope-understand" / "SKILL.md"
    assert report["target"] == str(target)
    # Compare exact installed/package bytes; read_text normalizes checkout CRLF.
    assert target.read_bytes() == learning_cli.skill_text().encode("utf-8")
    before = target.stat().st_mtime_ns
    assert learning_cli.install_skill(project)["changed"] is False
    assert target.stat().st_mtime_ns == before


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_skill_preserves_local_edits_and_does_not_offer_force(repo, line_ending):
    learning_cli.install_skill(repo)
    target = repo / ".agents" / "skills" / "scope-understand" / "SKILL.md"
    original = b"local edits" + line_ending
    target.write_bytes(original)
    for dry_run in (False, True):
        with pytest.raises(ValueError, match="local edits"):
            learning_cli.install_skill(repo, dry_run=dry_run)
        assert target.read_bytes() == original


@pytest.mark.parametrize("component", [".agents", ".agents/skills", ".agents/skills/scope-understand"])
def test_skill_rejects_symlinked_destination_chain_portably(repo, monkeypatch, component):
    suspect = repo / component
    suspect.mkdir(parents=True)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == suspect or original(path))
    with pytest.raises(ValueError, match="link"):
        learning_cli.install_skill(repo)
    assert not (suspect / "SKILL.md").exists()


def test_skill_rejects_windows_reparse_point_destination(repo, monkeypatch):
    directory = repo / ".agents"
    directory.mkdir()
    original = Path.lstat

    def reparse(path):
        info = original(path)
        return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400) if path == directory else info

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(ValueError, match="link"):
        learning_cli.install_skill(repo)


def test_skill_invalid_project_and_cli_modes(repo, tmp_path, capsys):
    with pytest.raises(ValueError, match="existing directory"):
        learning_cli.install_skill(tmp_path / "absent")
    assert learning_cli.main(["skill"]) == 0
    assert capsys.readouterr().out == learning_cli.skill_text()
    assert learning_cli.main(["skill", "--install", "--dry-run", "-C", str(repo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert not (repo / ".agents").exists()
    with pytest.raises(SystemExit) as error:
        learning_cli.main(["skill", "--dry-run", "-C", str(repo)])
    assert error.value.code == 2


def test_real_cli_dispatch_for_project_skill_dry_run(repo):
    result = subprocess.run([sys.executable, "-m", "scope", "skill", "--install", "--dry-run",
                             "-C", str(repo), "--json"], capture_output=True, text=True,
                            encoding="utf-8", timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["dry_run"] is True
    assert not (repo / ".agents").exists()
