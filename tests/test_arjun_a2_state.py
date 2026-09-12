"""A2 persistence contracts; every answer and execution below is synthetic."""

from contextlib import contextmanager
from copy import deepcopy
import json
import subprocess
import sys

import pytest

from scope import learning, log, repository, storage


@pytest.fixture
def task_state(tmp_path, monkeypatch):
    for name in ("CODEX_THREAD_ID", "SCOPE_LAUNCH_ID", "GIT_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "synthetic-repository"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], capture_output=True, check=True)
    (root / "probe.py").write_text('print("synthetic fixture")\n', encoding="utf-8")
    task = learning.start(root, "Synthetic A2 task state", session_id="a2-state-test-fixture")
    ref = repository.validate_citation(task["source"], "probe.py:1")
    argv = [sys.executable, "-B", "probe.py"]
    prediction = {"value": {"nested": [True, None, 2]}, "reason": "Synthetic fixture reason",
                  "assistance": "Automated fixture", "provenance": "test_fixture", "references": [ref],
                  "question": "Synthetic fixture question?", "field": "result", "argv": argv}
    execution = {"argv": argv, "cwd": str(root), "timestamp": task["started_at"], "exit_code": 0,
                 "stdout": '{"result":{"nested":[true,null,2]}}', "stderr": "", "timed_out": False,
                 "stdout_truncated": False, "stderr_truncated": False, "error": None, "cleanup_incomplete": False}
    observation = {"status": "matched", "field": "result", "expected": prediction["value"],
                   "actual": prediction["value"], "reason": "Synthetic fixture comparison",
                   "references": [ref], "source_status": repository.evidence_status([ref], task["source"]),
                   "execution_timestamp": execution["timestamp"], "check_id": "check-fixture"}
    check = {"check_id": "check-fixture", "created_at": task["started_at"], "phase": "completed",
             "spec": {"question": prediction["question"], "citations": [ref["citation"]], "field": "result",
                      "argv": argv, "shell": "posix", "timeout": 20}, "references": [ref],
             "prediction": prediction, "approval": {"approved": True, "provenance": "test_fixture", "reason": "approved"},
             "execution": execution, "observation": observation}
    handoff = {"handoff_id": "handoff-fixture", "choice": "smaller", "instruction": "Synthetic next task",
               "target": "current_agent", "status": "selected", "provenance": "test_fixture",
               "created_at": task["started_at"]}
    return root, task, check, handoff


def seed_records(task_state):
    root, task, check, handoff = task_state

    def change(saved):
        saved["checks"] = {check["check_id"]: deepcopy(check)}
        saved["handoffs"] = {handoff["handoff_id"]: deepcopy(handoff)}

    return learning.update_task(root, task["task_id"], change)


def test_task_helpers_copy_results_and_ignore_callback_return(task_state):
    root, task, _, _ = task_state
    callback_tasks = []

    def change(saved):
        callback_tasks.append(saved)
        saved["description"] = "Updated synthetic context"
        return {"task_id": "must-be-ignored"}

    updated = learning.update_task(root, task["task_id"], change)
    assert updated["task_id"] == task["task_id"]
    assert updated["description"] == "Updated synthetic context"
    updated["baseline"]["files"].clear()
    callback_tasks[0]["description"] = "Mutation after save"
    first = learning.read_task(root, task["task_id"])
    first["source"]["files"].clear()
    second = learning.read_task(root, task["task_id"])
    assert second["description"] == "Updated synthetic context"
    assert second["baseline"] == task["baseline"]
    assert second["source"]["files"] == task["source"]["files"]


@pytest.mark.parametrize("key,value", [
    ("task_id", "other-task"), ("session_id", "other-session"),
    ("baseline", {}), ("started_at", "2000-01-01T00:00:00+00:00"),
])
def test_update_preserves_task_identity_and_baseline(task_state, key, value):
    root, task, _, _ = task_state
    path = storage.project_dir(root) / "state.json"
    before = path.read_bytes()
    with pytest.raises(storage.StateError):
        learning.update_task(root, task["task_id"], lambda saved: saved.__setitem__(key, value))
    assert path.read_bytes() == before


def test_update_rejects_root_rewrite_and_callback_failure_without_losing_evidence(task_state):
    root, task, _, _ = task_state
    path = storage.project_dir(root) / "state.json"
    before = path.read_bytes()

    def fail(saved):
        saved["description"] = "Must not be saved"
        raise RuntimeError("synthetic callback failure")

    with pytest.raises(RuntimeError, match="synthetic callback"):
        learning.update_task(root, task["task_id"], fail)
    with pytest.raises(storage.StateError):
        learning.update_task(root, task["task_id"], lambda saved: saved["source"].update(root="different-root"))
    assert path.read_bytes() == before


def test_helpers_validate_whole_state_before_callback(task_state):
    root, task, _, _ = task_state
    second = learning.start(root, "Another synthetic task")
    state = storage.load(root)
    state["tasks"][second["task_id"]]["baseline"] = {}
    path = storage.project_dir(root) / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    before = path.read_bytes()
    calls = []
    with pytest.raises(storage.StateError):
        learning.read_task(root, task["task_id"])
    with pytest.raises(storage.StateError):
        learning.update_task(root, task["task_id"], lambda saved: calls.append(saved))
    assert calls == []
    assert path.read_bytes() == before


def test_old_a1_tasks_without_new_collections_remain_readable(task_state):
    root, task, _, _ = task_state

    def old_a1(state):
        state["tasks"][task["task_id"]].pop("checks")
        state["tasks"][task["task_id"]].pop("handoffs")
        return state

    storage.update(root, old_a1)
    before = (storage.project_dir(root) / "state.json").read_bytes()
    recovered = learning.read_task(root, task["task_id"])
    assert "checks" not in recovered and "handoffs" not in recovered
    knowledge = learning.knowledge(root, task["task_id"])
    assert knowledge["checks"] == knowledge["handoffs"] == {}
    assert knowledge["observations"] == []
    assert (storage.project_dir(root) / "state.json").read_bytes() == before


def test_no_snapshot_or_external_work_inside_task_helpers(task_state, monkeypatch):
    root, task, _, _ = task_state
    original_lock = storage.project_lock
    held = []
    snapshots = []

    @contextmanager
    def checked_lock(*args, **kwargs):
        with original_lock(*args, **kwargs):
            held.append(True)
            try:
                yield
            finally:
                held.pop()

    original_snapshot = repository.snapshot

    def snapshot(*args):
        assert not held
        snapshots.append(True)
        return original_snapshot(*args)

    monkeypatch.setattr(storage, "project_lock", checked_lock)
    monkeypatch.setattr(repository, "snapshot", snapshot)
    learning.read_task(root, task["task_id"])
    learning.update_task(root, task["task_id"], lambda saved: pytest.fail("not locked") if not held else None)
    assert snapshots == []
    learning.knowledge(root, task["task_id"])
    assert snapshots == [True]


@pytest.mark.parametrize("phase", ["prepared", "predicted", "approved", "running", "completed", "skipped", "interrupted"])
def test_all_recorded_phases_survive_recovery_without_inference(task_state, phase):
    root, task, check, _ = task_state
    check["phase"] = phase
    seed_records(task_state)
    path = storage.project_dir(root) / "state.json"
    before = path.read_bytes()
    events = log.read(task["session_id"])
    recovered = learning.knowledge(root, task["task_id"])
    assert recovered["checks"][check["check_id"]]["phase"] == phase
    recovered["checks"][check["check_id"]]["phase"] = "caller mutation"
    assert path.read_bytes() == before
    assert log.read(task["session_id"]) == events


def test_knowledge_recovers_context_and_versions_only_observations(task_state):
    root, task, check, handoff = task_state
    seed_records(task_state)
    learning.update_task(root, task["task_id"], lambda saved: saved["observations"].append(deepcopy(check["observation"])))
    learning.checkpoint(root, task["task_id"], note="Synthetic recent checkpoint")
    (root / "probe.py").write_text('print("changed synthetic fixture")\n', encoding="utf-8")
    knowledge = learning.knowledge(root, task["task_id"])
    assert knowledge["description"] == task["description"]
    assert "text" not in knowledge["source"]["files"]["probe.py"]
    assert knowledge["source"]["files"]["probe.py"]["sha256"] != task["baseline"]["files"]["probe.py"]["sha256"]
    assert knowledge["checkpoints"][-1]["note"] == "Synthetic recent checkpoint"
    assert knowledge["checks"][check["check_id"]] == check
    assert knowledge["handoffs"][handoff["handoff_id"]] == handoff
    observation = knowledge["observations"][0]
    assert observation["status"] == "matched"
    assert observation["current_status"] == "not_verified"
    assert observation["source_status"]["status"] == "stale"
    assert learning.read_task(root, task["task_id"])["observations"] == [check["observation"]]


@pytest.mark.parametrize("trail,value", [
    (("checks",), []), (("handoffs",), []),
    (("checks", "check-fixture"), None),
    (("checks", "check-fixture", "check_id"), "other-id"),
    (("checks", "check-fixture", "created_at"), "not-a-time"),
    (("checks", "check-fixture", "phase"), []),
    (("checks", "check-fixture", "phase"), "complete"),
    (("checks", "check-fixture", "references"), []),
    (("checks", "check-fixture", "references", 0, "start_line"), True),
    (("checks", "check-fixture", "references", 0, "citation"), "probe.py:2"),
    (("checks", "check-fixture", "references", 0, "sha256"), "bad"),
    (("checks", "check-fixture", "spec", "citations"), ["other.py:1"]),
    (("checks", "check-fixture", "spec", "question"), ""),
    (("checks", "check-fixture", "spec", "field"), []),
    (("checks", "check-fixture", "spec", "argv"), "python probe.py"),
    (("checks", "check-fixture", "spec", "argv"), ["probe.cmd"]),
    (("checks", "check-fixture", "spec", "argv"), ["x\0y"]),
    (("checks", "check-fixture", "spec", "shell"), "unknown"),
    (("checks", "check-fixture", "spec", "timeout"), True),
    (("checks", "check-fixture", "spec", "timeout"), 10 ** 400),
    (("checks", "check-fixture", "spec", "timeout"), 301),
    (("checks", "check-fixture", "prediction", "value"), {"bad": float("nan")}),
    (("checks", "check-fixture", "prediction", "reason"), None),
    (("checks", "check-fixture", "prediction", "provenance"), "agent"),
    (("checks", "check-fixture", "prediction", "question"), "different question"),
    (("checks", "check-fixture", "prediction", "references", 0, "start_line"), True),
    (("checks", "check-fixture", "approval", "approved"), 1),
    (("checks", "check-fixture", "approval", "provenance"), "agent"),
    (("checks", "check-fixture", "approval", "reason"), None),
    (("checks", "check-fixture", "execution", "argv"), ["other-command"]),
    (("checks", "check-fixture", "execution", "cwd"), "other-root"),
    (("checks", "check-fixture", "execution", "timestamp"), None),
    (("checks", "check-fixture", "execution", "exit_code"), False),
    (("checks", "check-fixture", "execution", "stdout"), "x" * (64 * 1024 + 1)),
    (("checks", "check-fixture", "execution", "stderr"), "\ud800"),
    (("checks", "check-fixture", "execution", "timed_out"), "false"),
    (("checks", "check-fixture", "execution", "cleanup_incomplete"), 0),
    (("checks", "check-fixture", "observation", "status"), {}),
    (("checks", "check-fixture", "observation", "field"), "other-field"),
    (("checks", "check-fixture", "observation", "expected"), {"bad": float("inf")}),
    (("checks", "check-fixture", "observation", "actual"), {1: "non-JSON key"}),
    (("checks", "check-fixture", "observation", "references", 0, "start_line"), True),
    (("checks", "check-fixture", "observation", "source_status", "status"), []),
    (("checks", "check-fixture", "observation", "source_status", "files"), [None]),
    (("checks", "check-fixture", "observation", "execution_timestamp"), "invalid"),
    (("checks", "check-fixture", "observation", "check_id"), "other-id"),
    (("handoffs", "handoff-fixture", "handoff_id"), "other-id"),
    (("handoffs", "handoff-fixture", "choice"), "defer"),
    (("handoffs", "handoff-fixture", "instruction"), ""),
    (("handoffs", "handoff-fixture", "target"), "new_agent"),
    (("handoffs", "handoff-fixture", "status"), "completed"),
    (("handoffs", "handoff-fixture", "provenance"), "agent"),
    (("handoffs", "handoff-fixture", "created_at"), None),
])
def test_invalid_a2_records_fail_atomically(task_state, trail, value):
    root, task, _, _ = task_state
    seed_records(task_state)
    path = storage.project_dir(root) / "state.json"
    before = path.read_bytes()

    def corrupt(saved):
        # Decouple fixture aliases so each corruption targets exactly one field.
        for key in ("checks", "handoffs"):
            saved[key] = json.loads(json.dumps(saved[key]))
        target = saved
        for key in trail[:-1]:
            target = target[key]
        target[trail[-1]] = value

    with pytest.raises(storage.StateError):
        learning.update_task(root, task["task_id"], corrupt)
    assert path.read_bytes() == before


@pytest.mark.parametrize("record", ["check", "spec", "prediction", "approval", "execution", "observation", "handoff"])
@pytest.mark.parametrize("operation", ["missing", "unknown"])
def test_frozen_records_reject_missing_and_unknown_fields(task_state, record, operation):
    root, task, _, _ = task_state
    seed_records(task_state)
    path = storage.project_dir(root) / "state.json"
    before = path.read_bytes()

    def invalid(saved):
        target = saved["handoffs"]["handoff-fixture"] if record == "handoff" else saved["checks"]["check-fixture"]
        if record not in ("check", "handoff"):
            target = target[record]
        if operation == "unknown":
            target["unrecorded-permission"] = True
        else:
            target.pop(next(iter(target)))

    with pytest.raises(storage.StateError):
        learning.update_task(root, task["task_id"], invalid)
    assert path.read_bytes() == before


@pytest.mark.parametrize("status", ["selected", "dispatching", "dispatched", "failed"])
def test_handoff_recovery_preserves_delivery_status(task_state, status):
    root, task, _, handoff = task_state
    handoff["status"] = status
    if status == "failed":
        handoff["reason"] = "Synthetic delivery failure"
    seed_records(task_state)
    assert learning.knowledge(root, task["task_id"])["handoffs"]["handoff-fixture"] == handoff


def test_corrupt_saved_a2_record_is_not_overwritten(task_state):
    root, task, _, _ = task_state
    seed_records(task_state)
    state = storage.load(root)
    state["tasks"][task["task_id"]]["checks"]["check-fixture"]["approval"]["approved"] = "true"
    path = storage.project_dir(root) / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    before = path.read_bytes()
    calls = []
    with pytest.raises(storage.StateError):
        learning.read_task(root, task["task_id"])
    with pytest.raises(storage.StateError):
        learning.update_task(root, task["task_id"], lambda saved: calls.append(saved))
    assert calls == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["status", "json_type"])
def test_linked_observation_cannot_disagree_with_saved_a2_evidence(task_state, change):
    root, task, check, _ = task_state
    seed_records(task_state)
    learning.update_task(root, task["task_id"], lambda saved: saved["observations"].append(deepcopy(check["observation"])))
    path = storage.project_dir(root) / "state.json"
    before = path.read_bytes()

    def corrupt(saved):
        observation = saved["observations"][0]
        if change == "status":
            observation["status"] = "mismatched"
        else:
            observation["actual"]["nested"][0] = 1

    with pytest.raises(storage.StateError, match="disagree"):
        learning.update_task(root, task["task_id"], corrupt)
    assert path.read_bytes() == before


def test_parallel_task_updates_preserve_each_handoff(task_state):
    root, task, _, _ = task_state
    code = """import sys
from scope import learning
root, task_id, worker = sys.argv[1:]
for index in range(5):
    def change(task):
        identity = f'handoff-fixture-{worker}-{index}'
        task['handoffs'][identity] = {
            'handoff_id': identity, 'created_at': task['started_at'],
            'choice': 'smaller', 'instruction': 'Synthetic concurrent fixture',
            'target': 'current_agent', 'status': 'selected', 'provenance': 'test_fixture'}
    learning.update_task(root, task_id, change)
"""
    processes = [subprocess.Popen([sys.executable, "-c", code, str(root), task["task_id"], str(worker)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for worker in range(2)]
    try:
        for process in processes:
            _, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr.decode()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
    assert len(learning.read_task(root, task["task_id"])["handoffs"]) == 10


def test_prediction_json_depth_is_bounded(task_state):
    root, task, _, _ = task_state
    seed_records(task_state)
    deeply_nested = None
    for _ in range(66):
        deeply_nested = [deeply_nested]
    with pytest.raises(storage.StateError):
        learning.update_task(root, task["task_id"], lambda saved: saved["checks"]["check-fixture"]["prediction"].update(value=deeply_nested))


def test_unfinished_check_reopens_in_fresh_process_without_execution(task_state):
    root, task, check, _ = task_state
    check["phase"] = "running"
    check.pop("execution")
    check.pop("observation")
    seed_records(task_state)
    code = ("import json,sys; from scope import learning; "
            "print(json.dumps(learning.knowledge(sys.argv[1],sys.argv[2])))")
    result = subprocess.run([sys.executable, "-c", code, str(root), task["task_id"]],
                            capture_output=True, text=True, check=True, timeout=10)
    recovered = json.loads(result.stdout)
    assert recovered["checks"]["check-fixture"]["phase"] == "running"
    assert "execution" not in recovered["checks"]["check-fixture"]
    assert recovered["observations"] == []
    assert len(log.read(task["session_id"])) == 1
