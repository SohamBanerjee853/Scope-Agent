"""Disposable launch identity contracts, without native host or model calls."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

import pytest

from scope import host_session as hs, ipc, log, paths


@pytest.fixture
def launch(tmp_path, monkeypatch):
    for key in ("SCOPE_LAUNCH_ID", "SCOPE_HOST", "CODEX_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "run"
    value = hs.create_launch(home, launch_id="launch-test", host="codex", cwd=project,
                             parent_session_ids=["parent-thread"])
    monkeypatch.setenv("SCOPE_HOME", str(home))
    monkeypatch.setenv("SCOPE_LAUNCH_ID", value["launch_id"])
    monkeypatch.setenv("SCOPE_HOST", value["host"])
    return home, project, value


def register(launch, **kwargs):
    _, project, _ = launch
    return hs.register("native-child", host="codex", cwd=project, **kwargs)


def test_waiting_is_not_registered_or_permission_observed(launch):
    home, _, record = launch
    assert hs.current_launch() == hs.read_launch(home) == record
    assert hs.status()["status"] == "waiting"
    assert hs.status()["permission_status"] == "configured/waiting"
    with pytest.raises(hs.HostSessionError, match="missing or closed"):
        hs.session_id()
    assert not (home / "host-session.json").exists()


def test_outside_launch_is_none_and_does_not_borrow_parent(monkeypatch):
    monkeypatch.delenv("SCOPE_LAUNCH_ID", raising=False)
    monkeypatch.delenv("SCOPE_HOST", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-thread")
    assert hs.session_id() is None
    assert hs.require_session("explicit") == "explicit"


@pytest.mark.parametrize("name,value", [("SCOPE_LAUNCH_ID", "wrong"), ("SCOPE_HOST", "claude"),
                                         ("SCOPE_HOST", ""), ("SCOPE_LAUNCH_ID", "")])
def test_environment_mismatch_is_an_error(launch, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(hs.HostSessionError):
        hs.current_launch()


@pytest.mark.parametrize("changed", [{"session_id": "parent-thread"}, {"session_id": None},
                                    {"session_id": "bad\nidentity"}, {"host": "claude"},
                                    {"source": "subagent"}, {"agent_id": "child"}])
def test_invalid_or_subagent_registration_never_claims_startup(launch, changed):
    home, project, _ = launch
    values = {"session_id": "native-child", "host": "codex", "cwd": project, **changed}
    with pytest.raises(hs.HostSessionError):
        hs.register(**values)
    assert not (home / "host-session.json").exists()
    assert log.read("native-child") == []


def test_registration_is_idempotent_but_cannot_take_over(launch):
    first = register(launch)
    assert register(launch, source="compact") == first
    assert hs.session_id() == "native-child"
    assert [event["event"] for event in log.read("native-child")] == ["host_connected"]
    with pytest.raises(hs.HostSessionError):
        hs.register("unrelated", host="codex", cwd=launch[1])
    assert hs.session_id() == "native-child"


def test_main_custom_agent_name_is_not_subagent(launch, monkeypatch, tmp_path):
    home = tmp_path / "claude-run"
    hs.create_launch(home, launch_id="claude-launch", host="claude", cwd=launch[1])
    monkeypatch.setenv("SCOPE_HOME", str(home))
    monkeypatch.setenv("SCOPE_LAUNCH_ID", "claude-launch")
    monkeypatch.setenv("SCOPE_HOST", "claude")
    result = hs.register("claude-native", host="claude", cwd=launch[1], agent_type="reviewer", source="fork")
    assert result["agent_type"] == "reviewer"
    assert hs.session_id() == "claude-native"


def test_parent_and_foreign_task_and_host_claims_are_rejected(launch, monkeypatch):
    register(launch)
    for expected in ("parent-thread", "another-task"):
        with pytest.raises(hs.HostSessionError):
            hs.require_session(expected)
    with pytest.raises(hs.HostSessionError):
        hs.require_session("native-child", host="claude")
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-thread")
    with pytest.raises(hs.HostSessionError, match="Codex thread"):
        hs.session_id()


def test_project_descendants_allowed_but_sibling_and_startup_subdir_rejected(launch, tmp_path):
    _, project, _ = launch
    subdir = project / "src"
    subdir.mkdir()
    with pytest.raises(hs.HostSessionError):
        hs.register("native-child", host="codex", cwd=subdir)
    register(launch)
    assert hs.require_session("native-child", cwd=subdir) == "native-child"
    with pytest.raises(hs.HostSessionError):
        hs.require_session("native-child", cwd=tmp_path)


def test_permission_observations_serialize_and_do_not_count_as_allows(launch):
    register(launch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: hs.observe_permission("native-child"), range(16)))
    assert hs.status()["permission_requests"] == 16
    assert hs.status()["permission_status"] == "requests observed"
    assert [event["event"] for event in log.read("native-child")] == ["host_connected"]


def test_closure_sources_are_explicit_idempotent_and_never_reopen(launch):
    register(launch)
    hs.close_session("native-child", inferred=True)
    assert hs.status()["closure_source"] == "launcher"
    with pytest.raises(hs.HostSessionError):
        register(launch)
    with pytest.raises(hs.HostSessionError):
        hs.session_id()
    hs.close_session("native-child", inferred=False)
    hs.close_session("native-child", inferred=False)
    state = ipc._json_object((launch[0] / "host-session.json").read_bytes())
    assert state["native_end_at"] and state["inferred_end_at"]
    assert state["closure_source"] == "native"
    assert [event["event"] for event in log.read("native-child")] == ["host_connected", "agent_process", "session_end"]


def test_unregistered_owner_exit_is_not_native_startup_and_blocks_late_start(launch):
    result = hs.close_session(inferred=True)
    assert result["session_id"] is None
    assert hs.status()["status"] == "closed"
    with pytest.raises(hs.HostSessionError):
        register(launch)
    assert not (launch[0] / "sessions").exists()


def test_explicit_home_read_and_close_do_not_touch_parent_home(launch, tmp_path, monkeypatch):
    home, _, _ = launch
    register(launch)
    parent = tmp_path / "parent-home"
    monkeypatch.setenv("SCOPE_HOME", str(parent))
    monkeypatch.delenv("SCOPE_LAUNCH_ID")
    monkeypatch.delenv("SCOPE_HOST")
    assert hs.status(home=home)["session_id"] == "native-child"
    hs.close_session("native-child", inferred=True, home=home)
    assert not parent.exists()
    events = [json.loads(line) for line in (home / "sessions" / paths.session_log("native-child").name).read_text().splitlines()]
    assert [record["event"] for record in events] == ["host_connected", "agent_process"]


@pytest.mark.parametrize("mutation", [b"{", b'{"schema_version":1,"schema_version":1}', b"[]", b"x" * (hs.MAX_RECORD + 1)])
def test_malformed_launch_file_never_falls_back_to_environment(launch, mutation):
    (launch[0] / "launch.json").write_bytes(mutation)
    with pytest.raises(hs.HostSessionError):
        hs.session_id()


def test_launch_files_are_private_and_cannot_be_overwritten(launch):
    home, project, _ = launch
    original = (home / "launch.json").read_bytes()
    with pytest.raises(hs.HostSessionError):
        hs.create_launch(home, launch_id="replacement", host="codex", cwd=project)
    assert (home / "launch.json").read_bytes() == original
    register(launch)
    for name in ("launch.json", "host-session.json", "host-session.lock"):
        fd = ipc._private_open(home / name)
        os.close(fd)


def test_redirected_run_parent_rejected_before_creation(launch, tmp_path):
    target = tmp_path / "redirect-target"
    target.mkdir()
    link = tmp_path / "redirected"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("native symlink creation is unavailable")
    with pytest.raises(hs.HostSessionError):
        hs.create_launch(link / "new-run", launch_id="redirected", host="codex", cwd=launch[1])
    assert list(target.iterdir()) == []


def test_project_symlink_escape_rejected(launch, tmp_path):
    register(launch)
    outside = tmp_path / "outside-project"
    outside.mkdir()
    link = launch[1] / "outside-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("native symlink creation is unavailable")
    with pytest.raises(hs.HostSessionError):
        hs.require_session("native-child", cwd=link)


@pytest.mark.parametrize("field,value", [("session_id", "parent-thread"), ("host", "claude"),
                                        ("launch_id", "another"), ("permission_requests", True),
                                        ("status", "open-without-proof"), ("ended_at", "2026-09-12T00:00:00Z")])
def test_corrupt_native_state_cannot_authorize(launch, field, value):
    register(launch)
    path = launch[0] / "host-session.json"
    record = json.loads(path.read_text())
    record[field] = value
    path.write_text(json.dumps(record))
    with pytest.raises(hs.HostSessionError):
        hs.require_session("native-child")


def test_log_explicit_home_retains_legacy_home_field(tmp_path):
    alternate = tmp_path / "alternate"
    log.append("legacy", "example", home="event-field")
    log.append_at(alternate, "other", "example", home="still-event-field")
    assert log.read("legacy")[0]["fields"]["home"] == "event-field"
    assert log.read("other") == []
    data = json.loads((alternate / "sessions" / paths.session_log("other").name).read_text())
    assert data["fields"] == {"home": "still-event-field"}
