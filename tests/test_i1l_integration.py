"""Native identity boundaries exercised with temporary runs and fixture answers."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from scope import experience, host_session, ipc, learning, learning_cli, log, propose, receipt, storage, ui
from scope.watch import Watcher


@pytest.fixture
def project(tmp_path):
    root = (tmp_path / "project").resolve()
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "main.py").write_text("result = 1\n", encoding="utf-8")
    return root


def launch(monkeypatch, tmp_path, project, *, permissions=True, understanding=True, host="claude"):
    home = (tmp_path / "launch-home").resolve()
    config = host_session.create_launch(home, launch_id="fixture-run", host=host, cwd=project,
                                       permissions=permissions, understanding=understanding,
                                       parent_session_ids=("parent-session",))
    monkeypatch.setenv("SCOPE_HOME", str(home))
    monkeypatch.setenv("SCOPE_LAUNCH_ID", config["launch_id"])
    monkeypatch.setenv("SCOPE_HOST", host)
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-session")
    host_session.register("native-session", host=host, cwd=project)
    return home


class FixtureUI:
    interactive = True

    def __init__(self):
        self.questions = []
        self.callback = None

    def question(self, repo, context, prompt, lifetime):
        self.questions.append(prompt)
        return self.callback(lifetime) if self.callback else "fixture prediction"

    def review(self, request, card, lifetime):
        return {"action": "abstain"}

    def notice(self, text):
        pass


def test_existing_foreign_task_rejected_before_probe_or_question(project, tmp_path, monkeypatch):
    foreign = learning.start(project, "old task", session_id="other-session")
    before = (storage.project_dir(project) / "state.json").read_bytes()
    home = launch(monkeypatch, tmp_path, project)
    state_path = storage.project_dir(project) / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(before)
    calls = []
    spec = {"question": "Count?", "citations": ["main.py:1"], "field": "result", "argv": ["git", "status"]}
    with pytest.raises(storage.StateError, match="identity"):
        experience.check(project, foreign["task_id"], spec, ask=lambda value: calls.append(value))
    with pytest.raises(storage.StateError, match="identity"):
        learning.checkpoint(project, foreign["task_id"])
    assert not calls
    assert state_path.read_bytes() == before
    assert not (home / "sessions" / "session-parent-session.jsonl").exists()


def test_native_task_identity_project_and_closed_run_guards(project, tmp_path, monkeypatch):
    launch(monkeypatch, tmp_path, project)
    task = learning.start(project, "fixture task")
    assert task["session_id"] == "native-session"
    assert learning.read_task(project, task["task_id"])["session_id"] == "native-session"
    with pytest.raises(storage.StateError, match="identity"):
        learning.start(project, "wrong explicit identity", session_id="parent-session")
    other = tmp_path / "other-project"
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    with pytest.raises(storage.StateError, match="project"):
        learning.start(other, "wrong project")
    host_session.close_session(inferred=True)
    with pytest.raises(storage.StateError, match="identity"):
        learning.read_task(project, task["task_id"])


def test_permissions_only_revoke_works_but_understanding_is_disabled(project, tmp_path, monkeypatch):
    launch(monkeypatch, tmp_path, project, understanding=False)
    with Watcher(FixtureUI(), human_timeout=2):
        result = learning_cli.revoke()
        assert result["revoked"] is True
        assert result["session_id"] == "native-session"
        assert "error" in ui.ask("Predict", repo=str(project), timeout=2)
        with pytest.raises(storage.StateError, match="understanding"):
            learning.start(project, "disabled task")


def test_proposal_cli_uses_native_identity_and_refuses_parent(project, tmp_path, monkeypatch, capsys):
    launch(monkeypatch, tmp_path, project)
    monkeypatch.chdir(project)
    args = ["--summary", "Fixture proposal", "--command", "touch *", "--budget", "2"]
    with Watcher(FixtureUI(), human_timeout=2):
        assert propose.main(args) == 0
        assert propose.main([*args, "--session", "parent-session"]) == 1
    assert any(event["event"] == "proposal_created" for event in log.read("native-session"))
    assert not log.read("parent-session")
    capsys.readouterr()


def test_launched_watcher_refuses_foreign_and_unbound_questions(project, tmp_path, monkeypatch):
    launch(monkeypatch, tmp_path, project)
    terminal = FixtureUI()
    message = {"kind": "question", "repo": str(project), "context": "fixture", "prompt": "Predict"}
    with Watcher(terminal, human_timeout=2):
        assert "error" in ipc.exchange(message, timeout=2)
        assert "error" in ipc.exchange({**message, "session_id": "parent-session"}, timeout=2)
        assert ui.ask("Predict", repo=str(project), timeout=2)["answer"] == "fixture prediction"
    assert len(terminal.questions) == 1


def test_closing_native_session_cancels_pending_answer(project, tmp_path, monkeypatch):
    launch(monkeypatch, tmp_path, project)
    entered, release = threading.Event(), threading.Event()
    terminal = FixtureUI()
    terminal.callback = lambda lifetime: (entered.set(), release.wait(2), "late fixture answer")[-1]
    with Watcher(terminal, human_timeout=2), ThreadPoolExecutor(1) as pool:
        result = pool.submit(ui.ask, "Predict", repo=str(project), timeout=3)
        try:
            assert entered.wait(1)
            host_session.close_session(inferred=True)
        finally:
            release.set()
        assert "error" in result.result(3)


def test_receipt_native_host_and_explicit_closed_run_replay(project, tmp_path, monkeypatch, capsys):
    home = launch(monkeypatch, tmp_path, project)
    assert receipt.main(["--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["session_id"], result["host"]) == ("native-session", "claude")
    assert result["launch"]["events"][0]["event"] == "host_connected"
    host_session.close_session(inferred=True)
    assert receipt.main(["--home", str(home), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["session_id"], result["host"]) == ("native-session", "claude")
    assert any(event["event"] == "agent_process" for event in result["launch"]["events"])
    assert not any(event["event"] == "session_end" for event in result["launch"]["events"])
    assert receipt.main(["--home", str(home), "--host", "codex"]) == 1


def test_orphan_host_marker_never_falls_back_to_parent_identity(project, monkeypatch):
    monkeypatch.setenv("SCOPE_HOST", "codex")
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-session")
    with pytest.raises(storage.StateError, match="identity"):
        learning.resolve_session()
    with pytest.raises(host_session.HostSessionError):
        Watcher(FixtureUI())
    assert "error" in ui.ask("Predict", repo=str(project), exchange=lambda *a, **k: {"answer": "unsafe"})


def test_closure_during_task_mutation_preserves_saved_state(project, tmp_path, monkeypatch):
    launch(monkeypatch, tmp_path, project)
    task = learning.start(project, "fixture task")
    state_path = storage.project_dir(project) / "state.json"
    before = state_path.read_bytes()

    def change(record):
        record["description"] = "must not be stored after closure"
        host_session.close_session(inferred=True)

    with pytest.raises(storage.StateError, match="identity"):
        learning.update_task(project, task["task_id"], change)
    assert state_path.read_bytes() == before


def test_watcher_observes_actual_owner_process_loss_and_finalizes(project, tmp_path, monkeypatch):
    from scope import watch

    home = launch(monkeypatch, tmp_path, project)
    monkeypatch.setattr(watch, "TerminalUI", FixtureUI)
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    owner = subprocess.Popen([sys.executable, "-c",
                              "from scope.launch import owner_lock; import sys,time; "
                              "\nwith owner_lock(sys.argv[1]):\n print('owned',flush=True); time.sleep(30)",
                              str(home)], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert owner.stdout.readline().strip() == "owned"
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(watch.main, ["--owner-home", str(home)])
            deadline = time.monotonic() + 3
            while ipc.exchange({"kind": "ping"}, timeout=0.1) != {"ready": True}:
                assert time.monotonic() < deadline
                time.sleep(0.02)
            owner.terminate()
            owner.wait(timeout=3)
            assert pending.result(timeout=5) == 0
        state = host_session.status()
        assert state["status"] == "closed"
        assert state["closure_source"] == "launcher"
        result = receipt.load(receipt.receipt_path("native-session", home=home))
        assert result["session_id"] == "native-session"
        assert result["host"] == "claude"
        assert ipc.exchange({"kind": "ping"}, timeout=0.1) is None
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=3)


def test_concurrent_close_waits_for_endpoint_cleanup(project, tmp_path, monkeypatch):
    home = launch(monkeypatch, tmp_path, project)
    watcher = Watcher(FixtureUI(), human_timeout=2).start()
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
    real_close = watcher.server.close

    def delayed_close():
        entered.set()
        assert release.wait(3)
        real_close()

    def second_close():
        second_started.set()
        watcher.close()

    monkeypatch.setattr(watcher.server, "close", delayed_close)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(watcher.close)
        try:
            assert entered.wait(1)
            second = pool.submit(second_close)
            assert second_started.wait(1)
            with pytest.raises(TimeoutError):
                second.result(timeout=0.05)
        finally:
            release.set()
        first.result(timeout=3)
        second.result(timeout=3)
    assert not (home / ipc.ENDPOINT_NAME).exists()
