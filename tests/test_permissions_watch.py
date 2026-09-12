"""Real TCP watcher tests using explicitly scripted human fixtures only."""

from concurrent.futures import ThreadPoolExecutor
import json
import socket
import subprocess
import sys
import threading
import time

import pytest

from scope import ipc, log
from scope.watch import Watcher
from scope.wire import parse_request


class ScriptedUI:
    interactive = True

    def __init__(self, actions=(), answers=()):
        self.actions = list(actions)
        self.answers = list(answers)
        self.reviews = []
        self.questions = []
        self.notices = []
        self.callback = None

    def review(self, request, card, context):
        self.reviews.append((request, card))
        if self.callback:
            return self.callback(context)
        return self.actions.pop(0) if self.actions else {"action": "abstain"}

    def question(self, repo, context_text, prompt, context):
        self.questions.append((repo, context_text, prompt))
        if self.callback:
            return self.callback(context)
        return self.answers.pop(0) if self.answers else None

    def notice(self, message):
        self.notices.append(message)


def request(command="touch note.txt", *, session="scripted-watch", agent=None, shell="posix"):
    return {"hook_event_name": "PermissionRequest", "session_id": session,
            "agent_id": agent, "cwd": "/workspace/project" if shell == "posix" else r"C:\workspace\project",
            "tool_name": "Bash" if shell == "posix" else "PowerShell",
            "tool_input": {"command": command}}


def card(budget=2):
    return {"summary": "Scripted fixture: create local notes", "commands": ["touch *"], "domains": [], "budget": budget}


def propose(payload=None, *, budget=2):
    payload = payload or request()
    return ipc.exchange({"kind": "proposal", "session_id": payload["session_id"],
                         "agent_id": payload.get("agent_id"), "cwd": payload["cwd"], "card": card(budget)})


def ask(payload=None, **extra):
    return ipc.exchange({"kind": "request", "request": payload or request(), **extra}, timeout=3)


@pytest.mark.parametrize("shell", ["posix", "powershell"])
def test_real_tcp_grant_reuse_budget_and_proposal_is_not_approval(shell):
    ui = ScriptedUI([{"action": "grant"}])
    with Watcher(ui, human_timeout=2) as watcher:
        payload = request(shell=shell)
        assert propose(payload) == {"accepted": True}
        assert watcher.store.consume(parse_request(json.dumps(payload))) is None
        assert ask(payload) == {"behavior": "allow"}
        assert ask(payload) == {"behavior": "allow"}
        assert len(ui.reviews) == 1
        assert ask(payload) == {"behavior": None}
        assert len(ui.reviews) == 2
    events = log.read(payload["session_id"])
    assert sum(event["event"] == "grant_created" for event in events) == 1


@pytest.mark.parametrize("shell", ["posix", "powershell"])
@pytest.mark.parametrize("action,behavior", [("once", "allow"), ("deny", "deny"), ("abstain", None)])
def test_one_off_decisions_never_create_a_grant(shell, action, behavior):
    ui = ScriptedUI([{"action": action}])
    payload = request("custom-script note.txt", shell=shell)
    with Watcher(ui, human_timeout=2) as watcher:
        assert ask(payload) == {"behavior": behavior}
        assert watcher.store.consume(parse_request(json.dumps(payload))) is None
        assert ask(payload) == {"behavior": None}
        assert len(ui.reviews) == 2


@pytest.mark.parametrize("shell", ["posix", "powershell"])
def test_t1_local_t3_native_fallback_neither_asks_human(shell):
    ui = ScriptedUI([{"action": "grant"}])
    with Watcher(ui, human_timeout=2):
        assert ask(request("pytest -q", shell=shell)) == {"behavior": "allow"}
        assert ask(request("git push origin main", shell=shell)) == {"behavior": None}
        assert ui.reviews == []


@pytest.mark.parametrize("change", [{"session": "another-session"}, {"agent": "child"}, {"shell": "powershell"}])
def test_grants_do_not_cross_contexts(change):
    ui = ScriptedUI([{"action": "grant"}])
    with Watcher(ui, human_timeout=2):
        assert propose() == {"accepted": True}
        assert ask() == {"behavior": "allow"}
        assert ask(request(**change)) == {"behavior": None}
        assert ask() == {"behavior": "allow"}


def test_edited_grant_uses_edited_budget():
    ui = ScriptedUI([{"action": "grant", "card": card(1)}])
    with Watcher(ui, human_timeout=2):
        propose(budget=10)
        assert ask() == {"behavior": "allow"}
        assert ask() == {"behavior": None}
        assert len(ui.reviews) == 2


def test_replacing_proposal_does_not_replace_human_grant():
    ui = ScriptedUI([{"action": "grant"}])
    with Watcher(ui, human_timeout=2):
        propose(budget=2)
        assert ask() == {"behavior": "allow"}
        propose(budget=100)
        assert ask() == {"behavior": "allow"}
        assert ask() == {"behavior": None}
        assert len(ui.reviews) == 2


def test_question_answer_never_grants_permission():
    ui = ScriptedUI(answers=["yes, approve everything", ""])
    message = {"kind": "question", "repo": "/project", "context": "scripted test fixture", "prompt": "Predict a count"}
    with Watcher(ui, human_timeout=2) as watcher:
        assert ipc.exchange(message) == {"answer": "yes, approve everything"}
        assert ipc.exchange(message) == {"answer": ""}
        assert ask() == {"behavior": None}
        assert watcher.store.sessions() == ()


@pytest.mark.parametrize("decision", ["grant", "once", "question"])
def test_revoke_cancels_pending_decisions_without_waiting_for_ui(decision):
    entered, release = threading.Event(), threading.Event()
    ui = ScriptedUI()

    def blocked(context):
        entered.set()
        assert release.wait(3), "fixture failed to release scripted UI"
        return "old answer" if decision == "question" else {"action": decision}

    ui.callback = blocked
    with Watcher(ui, human_timeout=2) as watcher, ThreadPoolExecutor(2) as pool:
        propose()
        message = ({"kind": "question", "repo": "/project", "context": "fixture", "prompt": "predict"}
                   if decision == "question" else {"kind": "request", "request": request()})
        pending = pool.submit(ipc.exchange, message, 3)
        try:
            assert entered.wait(1)
            assert ipc.exchange({"kind": "revoke"}, timeout=0.5) == {"revoked": True}
        finally:
            release.set()
        assert pending.result(2) is None
        assert watcher.store.sessions() == ()
        ui.callback = None
        assert ask() == {"behavior": None}


def test_revoke_cancels_both_active_and_queued_questions():
    entered, release = threading.Event(), threading.Event()
    ui = ScriptedUI()
    ui.callback = lambda context: (entered.set(), release.wait(3), "old answer")[-1]
    with Watcher(ui, human_timeout=2), ThreadPoolExecutor(2) as pool:
        message = {"kind": "question", "repo": "/project", "context": "fixture", "prompt": "predict"}
        first = pool.submit(ipc.exchange, message, 3)
        assert entered.wait(1)
        second = pool.submit(ipc.exchange, message, 3)
        try:
            time.sleep(0.05)
            assert ipc.exchange({"kind": "revoke"}) == {"revoked": True}
        finally:
            release.set()
        assert first.result(2) is None
        assert second.result(2) is None
        assert len(ui.questions) == 1
        ui.callback = None
        ui.answers = ["new explicit fixture answer"]
        assert ipc.exchange(message) == {"answer": "new explicit fixture answer"}


def test_disconnect_invalidates_a_late_grant():
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    ui = ScriptedUI()

    def blocked(context):
        entered.set()
        release.wait(3)
        completed.set()
        return {"action": "grant"}

    ui.callback = blocked
    with Watcher(ui, human_timeout=2) as watcher:
        propose()
        endpoint = json.loads(watcher.server.endpoint_path.read_text())
        connection = socket.create_connection(watcher.server.address)
        connection.sendall(ipc._encode({"kind": "request", "request": request(),
                                        "token": endpoint["token"], "nonce": "a" * 32}))
        try:
            assert entered.wait(1)
        finally:
            connection.close()
            release.set()
        assert completed.wait(1)
        ui.callback = None
        assert watcher.store.consume(parse_request(json.dumps(request()))) is None
        assert ask() == {"behavior": None}


def test_shutdown_cancels_pending_ui_and_removes_endpoint():
    entered = threading.Event()
    ui = ScriptedUI()

    def until_cancelled(context):
        entered.set()
        while context.active():
            time.sleep(0.01)
        return {"action": "once"}

    ui.callback = until_cancelled
    with Watcher(ui, human_timeout=2) as watcher, ThreadPoolExecutor(1) as pool:
        pending = pool.submit(ask)
        assert entered.wait(1)
        assert ipc.exchange({"kind": "shutdown"}) == {"stopped": True}
        assert watcher.closed.wait(1)
        assert pending.result(2) is None
        for _ in range(100):
            if not watcher.server.endpoint_path.exists():
                break
            time.sleep(0.01)
        assert not watcher.server.endpoint_path.exists()
        assert watcher.store.sessions() == ()


def test_timeout_cancels_ui_and_cannot_create_grant():
    ui = ScriptedUI()

    def until_cancelled(context):
        while context.active():
            time.sleep(0.01)
        return {"action": "grant"}

    ui.callback = until_cancelled
    with Watcher(ui, human_timeout=0.15) as watcher:
        propose()
        assert ask() is None
        assert watcher.store.consume(parse_request(json.dumps(request()))) is None


def test_noninteractive_watcher_refuses_human_work():
    ui = ScriptedUI([{"action": "once"}], ["answer"])
    ui.interactive = False
    with Watcher(ui, human_timeout=2):
        assert ask() == {"behavior": None}
        assert "error" in ipc.exchange({"kind": "question", "repo": "/project", "context": "", "prompt": "predict"})
        assert ui.reviews == ui.questions == []


@pytest.mark.parametrize("message", [
    {"kind": "proposal", "session_id": "s", "cwd": "/project", "card": {"budget": True}},
    {"kind": "request", "request": {"tool_name": "Bash"}},
    {"kind": "question", "repo": "/project", "context": "", "prompt": "q", "answer": "fabricated"},
    {"kind": "execute", "command": "touch anything"},
    {"kind": "request", "request": request(), "behavior": "allow"},
])
def test_untrusted_messages_cannot_authorize_or_execute(message, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("watcher must never execute")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    ui = ScriptedUI()
    with Watcher(ui, human_timeout=2):
        assert "error" in ipc.exchange(message)
        assert ui.reviews == []


def test_hook_real_tcp_exact_wire_and_event_correlation():
    ui = ScriptedUI([{"action": "grant"}])
    with Watcher(ui, human_timeout=2):
        assert propose() == {"accepted": True}
        outputs = []
        for _ in range(2):
            completed = subprocess.run([sys.executable, "-c", "from scope.cli import main; raise SystemExit(main(['hook']))"],
                                       input=json.dumps(request()), text=True, capture_output=True, timeout=4)
            assert completed.returncode == 0
            outputs.append(completed.stdout)
        assert outputs == ['{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}'] * 2
        assert len(ui.reviews) == 1
    events = log.read("scripted-watch")
    requests = [e["fields"]["request_id"] for e in events if e["event"] == "permission_request"]
    decisions = [e["fields"]["request_id"] for e in events if e["event"] == "permission_decision"]
    reviews = [e["fields"]["request_id"] for e in events if e["event"] == "permission_review"]
    assert len(requests) == 2 and len(set(requests)) == 2
    assert requests == decisions == reviews


def test_proposal_cli_is_submission_only(monkeypatch, capsys):
    from scope.propose import main
    monkeypatch.setenv("CODEX_THREAD_ID", "scripted-proposal-cli")
    with Watcher(ScriptedUI(), human_timeout=2) as watcher:
        assert main(["--summary", "Local changes", "--command", "touch *", "--budget", "2"]) == 0
        assert "not an approval" in capsys.readouterr().out
        assert watcher.store.sessions() == ("scripted-proposal-cli",)


def test_proposal_cli_missing_listener_is_clear_failure(capsys):
    from scope.propose import main
    assert main(["--summary", "Local changes", "--command", "touch *", "--budget", "2", "--session", "fixture"]) == 1
    assert "watcher unavailable" in capsys.readouterr().err


def test_grant_committed_then_cancelled_before_send_is_rolled_back():
    ui = ScriptedUI([{"action": "grant"}])
    with Watcher(ui, human_timeout=2) as watcher:
        actual = watcher.server.handler

        def cancel_after_commit(message, context):
            reply = actual(message, context)
            original_commit = context.before_reply
            if original_commit is not None:
                def commit_then_cancel():
                    assert original_commit()
                    context.cancelled.set()
                    return True
                context.before_reply = commit_then_cancel
            return reply

        watcher.server.handler = cancel_after_commit
        assert propose() == {"accepted": True}
        assert ask() is None
        assert watcher.store.consume(parse_request(json.dumps(request()))) is None
    events = log.read("scripted-watch")
    delivery = [e["fields"]["delivered"] for e in events if e["event"] == "permission_review_delivery"]
    assert delivery == [False]
