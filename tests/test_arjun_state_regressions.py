"""Corrupt-state regressions using disposable repositories and synthetic evidence."""

from copy import deepcopy
import json
import subprocess

import pytest

from scope import learning, log, repository, storage


@pytest.fixture
def saved_task(tmp_path, monkeypatch):
    for name in ("SCOPE_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))
    for name in ("CODEX_THREAD_ID", "SCOPE_LAUNCH_ID", "GIT_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "fixture-repository"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    (root / "main.py").write_text("def total():\n    return 2\n", encoding="utf-8")
    task = learning.start(root, "Synthetic state regression", session_id="state-test-fixture")
    learning.checkpoint(root, task["task_id"], note="Synthetic checkpoint")
    return root, task


@pytest.mark.parametrize("trail,value", [
    (("baseline",), {}),
    (("baseline", "timestamp"), None),
    (("baseline", "files", "main.py", "sha256"), "invalid"),
    (("baseline", "files", "main.py", "line_count"), True),
    (("description",), None),
    (("session_id",), " "),
    (("source", "timestamp"), None),
    (("source", "skipped"), None),
    (("source", "listing_complete"), "true"),
    (("source", "total_bytes"), 0),
    (("source", "files", "main.py", "bytes"), 1),
    (("source", "files", "main.py", "text"), "\ud800"),
    (("checkpoints",), [None]),
    (("checkpoints", 0, "source"), {}),
    (("checkpoints", 0, "changes", "unavailable"), [None]),
    (("checkpoints", 0, "changes", "diff_truncated"), "false"),
])
def test_checkpoint_rejects_nested_corruption_without_replacing_evidence(saved_task, trail, value):
    root, task = saved_task
    state = storage.load(root)
    current = state["tasks"][task["task_id"]]
    for part in trail[:-1]:
        current = current[part]
    current[trail[-1]] = value
    path = storage.project_dir(root) / "state.json"
    # A damaged JSON file is read directly; do not pretend a producer emitted it.
    path.write_text(json.dumps(state), encoding="utf-8")
    damaged = path.read_bytes()
    events = log.read(task["session_id"])
    with pytest.raises(storage.StateError):
        learning.checkpoint(root, task["task_id"], note="Must not overwrite corrupt evidence")
    assert path.read_bytes() == damaged
    assert log.read(task["session_id"]) == events


@pytest.mark.parametrize("operation", ["start", "knowledge"])
def test_other_entrypoints_reject_incomplete_baseline(saved_task, operation):
    root, task = saved_task
    state = storage.load(root)
    state["tasks"][task["task_id"]]["baseline"] = {}
    path = storage.project_dir(root) / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    damaged = path.read_bytes()
    with pytest.raises(storage.StateError):
        if operation == "start":
            learning.start(root, "Synthetic second task")
        else:
            learning.knowledge(root, task["task_id"])
    assert path.read_bytes() == damaged


@pytest.mark.parametrize("status", [[], {}, None, True, 1, "unsupported"])
def test_malformed_observation_status_stays_unverified_and_unchanged(saved_task, status):
    root, task = saved_task
    reference = repository.validate_citation(task["source"], "main.py:2")
    observation = {"status": status, "references": [reference], "provenance": "test_fixture"}
    original = deepcopy(observation)

    def seed(state):
        state["tasks"][task["task_id"]]["observations"] = [observation]
        return state

    storage.update(root, seed)
    path = storage.project_dir(root) / "state.json"
    before = path.read_bytes()
    result = learning.knowledge(root, task["task_id"])["observations"][0]
    assert result["current_status"] == "not_verified"
    assert result["source_status"]["status"] == "current"
    assert result["status"] == status
    assert observation == original
    assert path.read_bytes() == before


def test_unicode_separator_uses_source_lines_during_state_validation(saved_task):
    root, task = saved_task
    (root / "main.py").write_text("# same physical line\u2028still the comment\nvalue = 2\n", encoding="utf-8")
    learning.checkpoint(root, task["task_id"])
    # Reopening the recorded snapshot must agree with the corrected citation count.
    learning.checkpoint(root, task["task_id"])
    saved = storage.load(root)["tasks"][task["task_id"]]
    assert saved["source"]["files"]["main.py"]["line_count"] == 2
    assert learning.knowledge(root, task["task_id"])["observations"] == []
