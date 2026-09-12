"""Offline A1 evidence; all identities and any answers are synthetic fixtures."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from scope import learning, log, repository, runner, storage, understanding_summary


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path, monkeypatch):
    for name in ("SCOPE_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))
    for name in ("CODEX_THREAD_ID", "SCOPE_LAUNCH_ID", "GIT_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(name, raising=False)


def git(root, *args):
    return subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
                          check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "project with spaces"
    root.mkdir()
    git(root, "init", "-q")
    (root / "main.py").write_text("def charges():\n    return 2\n", encoding="utf-8")
    git(root, "add", "main.py")
    return root


def state_seed(repository_root, **fields):
    return {"schema_version": 1, "project_id": storage.project_id(repository_root), **fields}


def test_source_git_root_hash_citations_symbols_and_untracked(repo):
    (repo / "extra.py").write_text("answer = '理解'\n", encoding="utf-8")
    (repo / "sub").mkdir()
    source = repository.snapshot(repo / "sub")
    assert source["root"] == str(repo.resolve())
    assert source["listing_complete"]
    assert set(source["files"]) == {"main.py", "extra.py"}
    file = source["files"]["main.py"]
    assert file["sha256"] == hashlib.sha256((repo / "main.py").read_bytes()).hexdigest()
    assert file["symbols"] == [{"name": "charges", "kind": "FunctionDef", "line": 1,
                                "end_line": 2, "citation": "main.py:1"}]
    ref = repository.validate_citation(source, "main.py:1-2")
    assert ref["sha256"] == file["sha256"]
    assert repository.evidence_status([ref], source)["status"] == "current"


@pytest.mark.parametrize("citation", ["main.py:0", "main.py:3", "main.py:2-1", "main.py:1-3", "missing.py:1", "../main.py:1", "main.py", None])
def test_invalid_citations(repo, citation):
    with pytest.raises(ValueError):
        repository.validate_citation(repository.snapshot(repo), citation)


@pytest.mark.parametrize("name", [".env", ".env.example", "secret.json", "api-token.txt", "private/data.py",
                                   ".aws/config", "key.pem", "node_modules/index.js", ".venv/lib.py",
                                   "uv.lock", "package-lock.json", "app.min.js", "out.generated.py", "LOCAL-NOTES.md"])
def test_private_and_generated_paths_are_excluded_even_if_tracked(repo, name):
    file = repo / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("DO_NOT_CAPTURE", encoding="utf-8")
    git(repo, "add", "-f", "--", name)
    source = repository.snapshot(repo)
    assert name not in source["files"]
    assert name in source["skipped"]
    assert "DO_NOT_CAPTURE" not in json.dumps(source)


def test_gitignored_tracked_and_untracked(repo):
    (repo / "hidden.py").write_text("HIDDEN_MARKER", encoding="utf-8")
    git(repo, "add", "hidden.py")
    (repo / "other.py").write_text("OTHER_MARKER", encoding="utf-8")
    (repo / ".gitignore").write_text("hidden.py\nother.py\n", encoding="utf-8")
    source = repository.snapshot(repo)
    assert source["skipped"]["hidden.py"] == "gitignored"
    assert source["skipped"]["other.py"] == "gitignored"
    assert "HIDDEN_MARKER" not in json.dumps(source)
    assert "OTHER_MARKER" not in json.dumps(source)


@pytest.mark.parametrize("content", [b"a\0b", b"\xff\xfe", b'password = "DO_NOT_CAPTURE"', b'-----BEGIN PRIVATE KEY-----'])
def test_binary_and_obvious_secret_content(repo, content):
    (repo / "config.py").write_bytes(content)
    source = repository.snapshot(repo)
    assert "config.py" not in source["files"]
    assert source["skipped"]["config.py"] in {"binary_or_non_utf8", "possible_secret_content"}


def test_symlink_files_and_directories(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.py").write_text("PRIVATE_MARKER", encoding="utf-8")
    try:
        (repo / "outside.py").symlink_to(outside / "data.py")
        (repo / "inside.py").symlink_to(repo / "main.py")
        (repo / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("native symlink creation is unavailable")
    source = repository.snapshot(repo)
    assert source["skipped"]["outside.py"] == "symlink"
    assert source["skipped"]["inside.py"] == "symlink"
    assert "PRIVATE_MARKER" not in json.dumps(source)
    assert repository._read_source(repo, "linked/data.py")[1] == "symlink"


@pytest.mark.parametrize("shell,name", [("posix", "../../data.py"), ("posix", "/tmp/data.py"),
                                       ("powershell", "C:\\secret.py"), ("powershell", "..\\data.py"),
                                       ("powershell", "C:relative.py"), ("posix", "bad\nname.py")])
def test_portable_path_shapes(shell, name):
    assert repository._path_reason(name) == "unsafe_path"


def test_file_size_limit_and_missing_is_unavailable(repo):
    before = repository.snapshot(repo)
    (repo / "large.py").write_bytes(b"#" * (repository.MAX_FILE_BYTES + 1))
    (repo / "main.py").unlink()
    after = repository.snapshot(repo)
    assert after["skipped"]["large.py"] == "file_size_limit"
    assert after["skipped"]["main.py"] == "unavailable"
    diff = repository.changes(before, after)
    assert diff["unavailable"] == [{"path": "main.py", "reason": "unavailable"}]
    assert "deleted" not in diff


def test_total_source_byte_limit(repo):
    for index in range(18):
        (repo / f"source{index:02}.py").write_bytes(b"#" * repository.MAX_FILE_BYTES)
    source = repository.snapshot(repo)
    assert source["total_bytes"] <= repository.MAX_TOTAL_BYTES
    assert "total_size_limit" in source["skipped"].values()


def test_file_count_limit(repo):
    for index in range(170):
        (repo / f"source{index:03}.py").write_text("x=1\n", encoding="utf-8")
    source = repository.snapshot(repo)
    assert len(source["files"]) == repository.MAX_FILES
    assert "file_count_limit" in source["skipped"].values()


def test_truncated_listing_and_skip_metadata_are_explicit(repo, monkeypatch):
    actual = repository._git
    def git_listing(root, *args):
        if "--cached" in args and "--ignored" not in args:
            return runner.RunResult([], str(root), "fixture", 0, "main.py\0partial.py", "", stdout_truncated=True)
        return actual(root, *args)
    monkeypatch.setattr(repository, "_git", git_listing)
    source = repository.snapshot(repo)
    assert not source["listing_complete"]
    assert "partial.py" not in source["files"]
    monkeypatch.setattr(repository, "_git", actual)
    monkeypatch.setattr(repository, "MAX_SKIPPED", 1)
    for name in ("first.bin", "second.bin", "third.bin"):
        (repo / name).write_bytes(b"\0")
    source = repository.snapshot(repo)
    assert len(source["skipped"]) == 1
    assert source["skipped_overflow"] == 2


def test_stale_missing_empty_and_malformed_evidence(repo):
    before = repository.snapshot(repo)
    ref = repository.validate_citation(before, "main.py:2")
    (repo / "main.py").write_text("def charges():\n    return 1\n", encoding="utf-8")
    after = repository.snapshot(repo)
    assert repository.evidence_status([ref], after)["status"] == "stale"
    (repo / "main.py").unlink()
    after = repository.snapshot(repo)
    for refs in ([ref], [], [None], [{"path": "main.py", "sha256": "fake"}]):
        assert repository.evidence_status(refs, after)["status"] == "unverified"


def test_diff_is_bounded_and_omits_newly_private_source(repo):
    (repo / "large.py").write_text("before\n" * 4000, encoding="utf-8")
    before = repository.snapshot(repo)
    (repo / "large.py").write_text("after!\n" * 4000, encoding="utf-8")
    after = repository.snapshot(repo)
    diff = repository.changes(before, after)
    assert "large.py" in diff["modified"]
    assert len(diff["diff"].encode()) <= repository.MAX_DIFF_BYTES
    (repo / "large.py").write_text('password = "PRIVATE_MARKER"', encoding="utf-8")
    diff = repository.changes(before, repository.snapshot(repo))
    assert "PRIVATE_MARKER" not in json.dumps(diff)
    assert diff["unavailable"][0]["path"] == "large.py"


def test_non_repository_fails(tmp_path):
    with pytest.raises(repository.RepositoryError):
        repository.find_root(tmp_path)


def test_storage_identity_atomic_replacement_and_callback_failure(repo, monkeypatch):
    assert storage.project_dir(repo) == Path(os.environ["SCOPE_HOME"]) / "projects" / storage.project_id(repo)
    assert storage.project_id(repo) == storage.project_id(repo / ".")
    storage.update(repo, lambda old: state_seed(repo, value="理解"))
    original = storage.load(repo)
    path = storage.project_dir(repo) / "state.json"
    before = path.read_bytes()
    def fail_replace(*args):
        raise OSError("fixture disk error")
    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "replace", fail_replace)
        with pytest.raises(OSError):
            storage.update(repo, lambda old: {**old, "value": "different"})
    assert path.read_bytes() == before
    assert storage.load(repo) == original
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("data", [b"{", b"[]", b"{}", b'{"schema_version":true}', b'{"x":NaN}', b"\xff"])
def test_corrupt_state_preserved(repo, data):
    directory = storage.project_dir(repo)
    directory.mkdir(parents=True)
    path = directory / "state.json"
    path.write_bytes(data)
    with pytest.raises(storage.StateError):
        storage.update(repo, lambda old: state_seed(repo))
    assert path.read_bytes() == data


def test_storage_size_limit_preserves_previous_state(repo, monkeypatch):
    storage.update(repo, lambda old: state_seed(repo, value=1))
    monkeypatch.setattr(storage, "MAX_STATE_BYTES", 200)
    with pytest.raises(storage.StateError):
        storage.update(repo, lambda old: {**old, "value": "x" * 500})
    assert storage.load(repo)["value"] == 1


def test_cross_process_updates_and_lock_release(repo):
    storage.update(repo, lambda old: state_seed(repo, counter=0))
    code = "from scope.storage import update; import sys;\nfor _ in range(15): update(sys.argv[1], lambda old: {**old, 'counter': old['counter'] + 1})"
    processes = [subprocess.Popen([sys.executable, "-c", code, str(repo)], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr.decode()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert storage.load(repo)["counter"] == 45


@pytest.mark.parametrize("ready_line", [b"locked\n", b"locked\r\n"], ids=["lf", "crlf"])
def test_lock_deadline_and_crashed_owner(repo, ready_line):
    code = ("from scope.storage import project_lock; import sys,time;\nwith project_lock(sys.argv[1]):\n"
            f" sys.stdout.buffer.write({ready_line!r}); sys.stdout.flush()\n time.sleep(30)")
    process = subprocess.Popen([sys.executable, "-c", code, str(repo)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        assert process.stdout.readline() == "locked\n"
        with pytest.raises(storage.StateError, match="lock"):
            with storage.project_lock(repo, timeout=0.05):
                pytest.fail("held lock was acquired")
    finally:
        process.kill()
        process.communicate(timeout=5)
    with storage.project_lock(repo, timeout=1):
        pass


def test_windows_byte_lock_contract(tmp_path, monkeypatch):
    calls = []
    fake = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=lambda *args: calls.append(args))
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(storage, "_WINDOWS", True)
    with (tmp_path / "lock").open("w+b") as stream:
        stream.write(b"\0")
        storage._try_lock(stream)
        storage._unlock(stream)
        assert calls == [(stream.fileno(), 2, 1), (stream.fileno(), 0, 1)]


def test_start_checkpoint_retention_and_session_events(repo, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "synthetic-host")
    task = learning.start(repo, "Repair retry key")
    assert task["session_id"] == "synthetic-host"
    baseline = deepcopy(task["baseline"])
    for value in range(7):
        (repo / "main.py").write_text(f"count = {value}\n", encoding="utf-8")
        learning.checkpoint(repo, task["task_id"], note=f"fixture {value}")
    saved = storage.load(repo)["tasks"][task["task_id"]]
    assert saved["baseline"] == baseline
    assert len(saved["checkpoints"]) == 5
    assert saved["checkpoints"][0]["note"] == "fixture 2"
    events = log.read("synthetic-host")
    assert events[0]["event"] == "task_start"
    assert events[0].get("fields", events[0])["session"] == "synthetic-host"
    assert [event["event"] for event in events[1:]] == ["task_checkpoint"] * 7


def test_session_precedence_fresh_tasks_and_launch_failure(repo, monkeypatch):
    one = learning.start(repo, "first")
    two = learning.start(repo, "second")
    assert one["task_id"] != two["task_id"]
    assert one["session_id"] == one["task_id"]
    monkeypatch.setenv("CODEX_THREAD_ID", "parent")
    assert learning.resolve_session("explicit") == "explicit"
    monkeypatch.setenv("SCOPE_LAUNCH_ID", "unregistered-launch")
    with pytest.raises(storage.StateError, match="identity"):
        learning.resolve_session()
    assert len(storage.load(repo)["tasks"]) == 2


def test_knowledge_staleness_preserves_historical_observation(repo):
    task = learning.start(repo, "fixture task")
    ref = repository.validate_citation(task["source"], "main.py:2")
    observation = {"status": "matched", "actual": 2, "references": [ref], "provenance": "test_fixture"}
    def seed(state):
        state["tasks"][task["task_id"]]["observations"].append(observation)
        return state
    storage.update(repo, seed)
    assert learning.knowledge(repo, task["task_id"])["observations"][0]["current_status"] == "matched"
    (repo / "main.py").write_text("def charges():\n    return 1\n", encoding="utf-8")
    current = learning.knowledge(repo, task["task_id"])["observations"][0]
    assert current["current_status"] == "not_verified"
    assert current["source_status"]["status"] == "stale"
    assert storage.load(repo)["tasks"][task["task_id"]]["observations"] == [observation]


def test_incomplete_task_state_is_not_reset(repo):
    storage.update(repo, lambda old: state_seed(repo, root=str(repo), tasks={"bad": {}}))
    with pytest.raises(storage.StateError, match="incomplete"):
        learning.start(repo, "fixture")
    assert storage.load(repo)["tasks"] == {"bad": {}}


def test_corrupt_saved_source_cannot_support_new_checkpoints(repo):
    task = learning.start(repo, "fixture")
    def damage(state):
        state["tasks"][task["task_id"]]["source"]["files"]["main.py"]["sha256"] = "0" * 64
        return state
    storage.update(repo, damage)
    with pytest.raises(storage.StateError, match="corrupt source"):
        learning.checkpoint(repo, task["task_id"])
    assert len(log.read(task["session_id"])) == 1


@pytest.mark.parametrize("shell,literal", [("posix", "x; touch should-not-exist"), ("posix", "$(echo no)"),
                                          ("powershell", "$env:SECRET; Remove-Item anything"),
                                          ("powershell", "`Get-Content x`"), ("powershell", "C:\\path with spaces\\x")])
def test_runner_preserves_literal_shell_arguments(repo, shell, literal):
    result = runner.run([sys.executable, "-c", "import json,sys; print(json.dumps(sys.argv[1:]))", literal], cwd=repo)
    assert result.succeeded
    assert json.loads(result.stdout) == [literal]
    assert not (repo / "should-not-exist").exists()


def test_runner_cwd_environment_utf8_and_exit(repo, monkeypatch):
    monkeypatch.setenv("SCOPE_A1_FIXTURE", "fixture-value")
    code = "import os,sys; print(os.getcwd()); print(os.environ['SCOPE_A1_FIXTURE']); sys.stdout.buffer.write('理解'.encode()); sys.stderr.write('err'); sys.exit(3)"
    result = runner.run([sys.executable, "-c", code], cwd=repo)
    assert result.exit_code == 3
    assert str(repo) in result.stdout
    assert "理解" in result.stdout and "fixture-value" in result.stdout
    assert result.stderr == "err"
    assert not result.succeeded
    assert result.timestamp.endswith("+00:00")


def test_runner_drains_large_both_streams_without_deadlock(repo):
    code = "import os;\nfor _ in range(256):\n os.write(1,b'x'*8192)\n os.write(2,b'y'*8192)"
    result = runner.run([sys.executable, "-c", code], cwd=repo, timeout=10)
    assert result.exit_code == 0 and not result.timed_out
    assert len(result.stdout.encode()) == runner.MAX_OUTPUT_BYTES
    assert len(result.stderr.encode()) == runner.MAX_OUTPUT_BYTES
    assert result.stdout_truncated and result.stderr_truncated
    assert not result.succeeded


def test_runner_invalid_utf8_stays_bounded(repo):
    result = runner.run([sys.executable, "-c", "import os; os.write(1,b'\\xff'*65536)"], cwd=repo)
    assert len(result.stdout.encode()) <= runner.MAX_OUTPUT_BYTES
    assert result.stdout_truncated


def test_runner_timeout_and_missing_executable(repo):
    result = runner.run([sys.executable, "-c", "import time; print('began',flush=True); time.sleep(30)"], cwd=repo, timeout=0.3)
    assert result.timed_out and not result.succeeded
    assert result.exit_code is not None
    missing = runner.run([str(repo / "missing-executable")], cwd=repo)
    assert missing.exit_code is None and "spawn failed" in missing.error
    invalid_cwd = runner.run([sys.executable], cwd=repo / "missing")
    assert invalid_cwd.error


def test_runner_cleanup_descendant_inheriting_pipes(repo):
    child_code = "import time; time.sleep(30)"
    parent_code = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',sys.argv[1]]); print('parent done',flush=True)"
    started = time.monotonic()
    result = runner.run([sys.executable, "-c", parent_code, child_code], cwd=repo, timeout=2)
    assert time.monotonic() - started < 7
    assert "parent done" in result.stdout
    if os.name != "nt":
        assert not result.cleanup_incomplete
    else:
        # Windows taskkill may no longer find the parent; never infer containment.
        assert result.cleanup_incomplete or result.exit_code == 0


@pytest.mark.parametrize("argv", ["echo hello", [], [1], [""], ["x\0y"], ["probe.cmd"], [r"C:\probe.BAT"]])
def test_runner_invalid_argv(repo, argv):
    with pytest.raises(ValueError):
        runner.run(argv, cwd=repo)


@pytest.mark.parametrize("timeout", [0, -1, 301, True, float("nan"), float("inf"), "20"])
def test_runner_invalid_timeout(repo, timeout):
    with pytest.raises(ValueError):
        runner.run([sys.executable], cwd=repo, timeout=timeout)


def test_windows_cleanup_contract(repo, monkeypatch):
    calls = []
    fake = SimpleNamespace(pid=42, poll=lambda: None, kill=lambda: calls.append("kill"))
    def taskkill(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runner, "_WINDOWS", True)
    monkeypatch.setattr(runner.subprocess, "run", taskkill)
    assert runner._terminate_tree(fake)
    assert calls[0][0][-4:] == ["/PID", "42", "/T", "/F"]
    assert calls[0][1]["shell"] is False
    assert calls[-1] == "kill"


def test_summary_exact_shape_pure_historical_and_permission_independent():
    events = [{"event": "task_start", "task_id": "fixture-task", "session": "fixture-session", "baseline": {}},
              {"event": "observation", "task_id": "fixture-task", "check_id": "fixture-check",
               "observation": {"status": "not_verified", "reason": "timeout"}},
              {"event": "next_task", "task_id": "fixture-task", "handoff": {"status": "selected"}},
              {"event": "grant", "behavior": "allow"}, {"event": "observation"}, None]
    saved = deepcopy(events)
    result = understanding_summary.summarize(events)
    assert set(result) == {"tasks", "predictions", "observations", "executions", "next_tasks", "dispatches",
                           "skipped_checks", "deferred_tasks", "revocations", "meaning"}
    assert len(result["tasks"]) == len(result["observations"]) == len(result["next_tasks"]) == 1
    assert result["executions"] == []
    assert result["observations"][0]["observation"]["status"] == "not_verified"
    result["observations"][0]["observation"]["status"] = "modified-by-caller"
    assert events == saved
    assert "not general mastery" in result["meaning"]


def test_summary_supports_published_foundation_nested_fields():
    event = {"ts": "fixture-time", "event": "observation", "fields": {
        "task_id": "fixture-task", "check_id": "fixture-check",
        "observation": {"status": "not_verified", "reason": "missing evidence"}}}
    result = understanding_summary.summarize([event, {"event": "observation", "fields": None}])
    assert result["observations"] == [event]
    assert result["observations"][0] is not event
