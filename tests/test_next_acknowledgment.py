"""Explicit native caller receipt claims; fixture identities, no real coding host."""

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from scope import experience, host_session, learning, learning_cli, log


@pytest.fixture
def native_task(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    project = (tmp_path / "fixture project").resolve()
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / "app.py").write_text("result = 2\n", encoding="utf-8")
    home = (tmp_path / "fixture run").resolve()
    launch = host_session.create_launch(home, launch_id="fixture-receiver", host="codex", cwd=project)
    monkeypatch.setenv("SCOPE_HOME", str(home))
    monkeypatch.setenv("SCOPE_LAUNCH_ID", launch["launch_id"])
    monkeypatch.setenv("SCOPE_HOST", "codex")
    host_session.register("fixture-native-receiver", host="codex", cwd=project)
    task = learning.start(project, "Labeled fixture understanding task")
    handoff = experience.choose_next(project, task["task_id"], smaller="Inspect 理解 and the exact retry key.\nKeep scope narrow.",
                                     ask=lambda request: "smaller", provenance="test_fixture")
    return project, launch, task, handoff


def digest(handoff):
    return hashlib.sha256(handoff["instruction"].encode("utf-8")).hexdigest()


def arguments(native_task, *, supplied_digest=None):
    project, launch, task, handoff = native_task
    return ["next", task["task_id"], "-C", str(project), "--acknowledge", handoff["handoff_id"],
            "--received-sha256", digest(handoff) if supplied_digest is None else supplied_digest, "--json"]


def test_native_receiver_acknowledges_exact_selected_bytes_once(native_task, capsys):
    project, launch, task, handoff = native_task
    before = log.read(task["session_id"])
    assert learning_cli.main(arguments(native_task)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "dispatched"
    assert result["delivery_status"] == "caller_acknowledged"
    assert result["delivery_provenance"] == "caller_reported"
    assert "self-reported" in result["delivery_reason"]
    assert "does not prove automatic delivery" in result["delivery_reason"]
    assert result["instruction_sha256"] == digest(handoff)
    events = log.read(task["session_id"])[len(before):]
    assert [event["event"] for event in events] == ["host_handoff_acknowledged", "dispatch"]
    claim = events[0]["fields"]
    assert claim["host"] == "codex" and claim["session"] == task["session_id"]
    assert claim["launch_id"] == launch["launch_id"]
    assert claim["provenance"] == "caller_reported"
    assert claim["instruction_sha256"] == digest(handoff)
    assert "execution" not in [event["event"] for event in log.read(task["session_id"])]
    assert learning.read_task(project, task["task_id"])["handoffs"][handoff["handoff_id"]]["status"] == "dispatched"
    assert learning_cli.main(arguments(native_task)) == 1
    assert capsys.readouterr().out == ""
    assert len(log.read(task["session_id"])) == len(before) + 2


@pytest.mark.parametrize("supplied", ["a" * 64, "A" * 64, "g" * 64, "0" * 63, "0" * 65, " " + "0" * 64])
def test_digest_mismatch_or_malformed_claim_preserves_selected_handoff(native_task, capsys, supplied):
    project, launch, task, handoff = native_task
    before = log.read(task["session_id"])
    assert learning_cli.main(arguments(native_task, supplied_digest=supplied)) == 1
    assert capsys.readouterr().out == ""
    assert learning.read_task(project, task["task_id"])["handoffs"][handoff["handoff_id"]] == handoff
    assert log.read(task["session_id"]) == before


def test_acknowledgment_rejected_outside_native_launch(native_task, monkeypatch, capsys):
    project, launch, task, handoff = native_task
    monkeypatch.delenv("SCOPE_LAUNCH_ID")
    monkeypatch.delenv("SCOPE_HOST")
    before = log.read(task["session_id"])
    assert learning_cli.main(arguments(native_task)) == 1
    assert "requires a registered open native" in capsys.readouterr().err
    assert log.read(task["session_id"]) == before


@pytest.mark.parametrize("change", ["closed", "parent_thread", "foreign_launch"])
def test_closed_or_foreign_native_identity_cannot_acknowledge(native_task, monkeypatch, capsys, change):
    project, launch, task, handoff = native_task
    if change == "closed":
        host_session.close_session(task["session_id"], host="codex", cwd=project)
    elif change == "parent_thread":
        monkeypatch.setenv("CODEX_THREAD_ID", "unrelated-parent-thread")
    else:
        monkeypatch.setenv("SCOPE_LAUNCH_ID", "foreign-launch")
    before = log.read(task["session_id"])
    assert learning_cli.main(arguments(native_task)) == 1
    assert capsys.readouterr().out == ""
    assert log.read(task["session_id"]) == before


def test_changed_selected_instruction_rejects_stale_digest(native_task, capsys):
    project, launch, task, handoff = native_task
    learning.update_task(project, task["task_id"], lambda state: state["handoffs"][handoff["handoff_id"]].update(instruction="Different instruction"))
    before = log.read(task["session_id"])
    assert learning_cli.main(arguments(native_task)) == 1
    assert "differs from the selected handoff" in capsys.readouterr().err
    assert log.read(task["session_id"]) == before


@pytest.mark.parametrize("failure", ["changed_callback_payload", "audit_write"])
def test_failed_receiver_claim_does_not_record_dispatched_or_allow_retry(native_task, monkeypatch, capsys, failure):
    project, launch, task, handoff = native_task
    if failure == "changed_callback_payload":
        original = experience.dispatch

        def changed(path, task_id, handoff_id, *, deliver):
            return original(path, task_id, handoff_id,
                            deliver=lambda offered: deliver({**offered, "instruction": "Tampered callback fixture"}))

        monkeypatch.setattr(experience, "dispatch", changed)
    else:
        original = log.append

        def unavailable(session_id, event, **fields):
            if event == "host_handoff_acknowledged":
                raise OSError("fixture audit write failed")
            return original(session_id, event, **fields)

        monkeypatch.setattr(log, "append", unavailable)
    assert learning_cli.main(arguments(native_task)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed" and result["delivery_status"] == "not_acknowledged"
    records = log.read(task["session_id"])
    assert "host_handoff_acknowledged" not in [event["event"] for event in records]
    assert records[-1]["event"] == "dispatch" and records[-1]["fields"]["status"] == "failed"
    assert learning_cli.main(arguments(native_task)) == 1
    capsys.readouterr()
    assert log.read(task["session_id"]) == records


def test_deferred_selection_has_no_handoff_to_acknowledge(native_task, capsys):
    project, launch, task, handoff = native_task
    deferred = experience.choose_next(project, task["task_id"], smaller="Inspect the hypothesis",
                                      ask=lambda request: "defer", provenance="test_fixture")
    assert deferred["status"] == "deferred"
    args = arguments(native_task)
    args[args.index("--acknowledge") + 1] = "handoff-no-selection"
    before = log.read(task["session_id"])
    assert learning_cli.main(args) == 1
    assert capsys.readouterr().out == ""
    assert log.read(task["session_id"]) == before


@pytest.mark.parametrize("flags", [[], ["--acknowledge", "handoff"],
                                   ["--smaller", "Inspect", "--acknowledge", "handoff", "--received-sha256", "a" * 64],
                                   ["--smaller", "Inspect", "--received-sha256", "a" * 64],
                                   ["--acknowledge", "handoff", "--received-sha256", "a" * 64, "--larger", "Audit"]])
def test_selection_and_acknowledgment_flags_are_strictly_separate(flags):
    with pytest.raises(SystemExit) as error:
        learning_cli.main(["next", "fixture-task", *flags])
    assert error.value.code == 2


def test_selected_cli_output_supplies_utf8_digest_but_no_implicit_acknowledgment(native_task, monkeypatch, capsys):
    project, launch, task, handoff = native_task
    original = experience.choose_next

    def fixture(path, task_id, **kwargs):
        assert kwargs["provenance"] == "human_ipc"
        kwargs.update(ask=lambda request: "smaller", show=None, provenance="test_fixture")
        return original(path, task_id, **kwargs)

    monkeypatch.setattr(experience, "choose_next", fixture)
    before = log.read(task["session_id"])
    assert learning_cli.main(["next", task["task_id"], "-C", str(project), "--smaller", "Inspect 理解", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "selected"
    assert result["instruction_sha256"] == hashlib.sha256("Inspect 理解".encode("utf-8")).hexdigest()
    assert result["delivery_status"] == "not_attempted"
    assert [event["event"] for event in log.read(task["session_id"])[len(before):]] == ["next_task"]
