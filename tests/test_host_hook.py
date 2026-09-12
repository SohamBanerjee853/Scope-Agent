"""Real Scope adapters with labeled fixture answers; no live coding hosts."""

import io
import json
import sys

import pytest

from scope import host_hook, host_session, ipc, log, wire


@pytest.fixture(params=["codex", "claude"])
def launched(request, tmp_path, monkeypatch):
    for key in ("SCOPE_LAUNCH_ID", "SCOPE_HOST", "CODEX_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    host = request.param
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "run"
    host_session.create_launch(home, launch_id="adapter-launch", host=host, cwd=project,
                               parent_session_ids=["parent"])
    monkeypatch.setenv("SCOPE_HOME", str(home))
    monkeypatch.setenv("SCOPE_LAUNCH_ID", "adapter-launch")
    monkeypatch.setenv("SCOPE_HOST", host)
    return host, project, home


def event(launched, kind="SessionStart", **changes):
    result = {"hook_event_name": kind, "session_id": "fixture-native", "cwd": str(launched[1])}
    if kind == "SessionStart":
        result["source"] = "startup"
    elif kind == "PermissionRequest":
        result.update(tool_name="Bash", tool_input={"command": "git status"})
    result.update(changes)
    return result


def run(launched, value, monkeypatch):
    raw = value if isinstance(value, str) else json.dumps(value)
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    monkeypatch.setattr(sys, "stdout", output)
    assert host_hook.main(["--host", launched[0]]) == 0
    return output.getvalue()


def test_actual_startup_event_registers_without_watcher_and_returns_only_guidance(launched, monkeypatch):
    output = json.loads(run(launched, event(launched), monkeypatch))
    assert set(output) == {"hookSpecificOutput"}
    assert set(output["hookSpecificOutput"]) == {"hookEventName", "additionalContext"}
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "scope ready" in output["hookSpecificOutput"]["additionalContext"]
    assert host_session.session_id() == "fixture-native"
    assert host_session.status()["permission_status"] == "configured/waiting"
    assert ipc.exchange({"kind": "ping"}, timeout=0.05) is None


@pytest.mark.parametrize("bad", ["{", "[]", '{"hook_event_name":"SessionStart","hook_event_name":"PermissionRequest"}',
                                 '{"value":NaN}', "x" * (wire.MAX_INPUT_BYTES + 1)])
def test_invalid_wire_always_empty_and_zero(launched, monkeypatch, bad):
    assert run(launched, bad, monkeypatch) == ""
    assert host_session.status()["status"] == "waiting"


def test_subagent_startup_and_parent_identity_never_replace_native(launched, monkeypatch):
    assert run(launched, event(launched, agent_id="subagent"), monkeypatch) == ""
    assert run(launched, event(launched, session_id="parent"), monkeypatch) == ""
    run(launched, event(launched, agent_type="custom-main"), monkeypatch)
    assert host_session.session_id() == "fixture-native"
    assert run(launched, event(launched, session_id="other"), monkeypatch) == ""
    assert host_session.session_id() == "fixture-native"


@pytest.mark.parametrize("tool,shell", [("Bash", "posix"), ("PowerShell", "powershell")])
def test_shared_engine_t1_allows_exact_wire_and_t3_never_asks(launched, monkeypatch, tool, shell):
    run(launched, event(launched), monkeypatch)
    request = event(launched, "PermissionRequest", tool_name=tool,
                    tool_input={"command": "git status", "shell": shell})
    assert run(launched, request, monkeypatch) == wire.decision_json("allow")
    request["tool_input"]["command"] = "git push origin main"
    assert run(launched, request, monkeypatch) == ""
    assert host_session.status()["permission_requests"] == 2
    assert [record["fields"]["tier"] for record in log.read("fixture-native")
            if record["event"] == "permission_request"] == ["T1", "T3"]


@pytest.mark.parametrize("changes", [{"session_id": "parent"}, {"session_id": "other"},
                                     {"tool_name": "Write"}, {"tool_input": {"command": "git status", "shell": "cmd"}}])
def test_unsupported_or_unregistered_request_never_allows(launched, monkeypatch, changes):
    request = event(launched, "PermissionRequest", **changes)
    assert run(launched, request, monkeypatch) == ""
    run(launched, event(launched), monkeypatch)
    assert run(launched, request, monkeypatch) == ""
    assert host_session.status()["permission_requests"] == 0


def test_understanding_only_has_no_permission_decisions(launched, monkeypatch):
    path = launched[2] / "launch.json"
    record = json.loads(path.read_text())
    record["permissions"] = False
    path.write_text(json.dumps(record))
    output = run(launched, event(launched), monkeypatch)
    assert "scope-understand" in output and "scope-permissions" not in output
    assert run(launched, event(launched, "PermissionRequest"), monkeypatch) == ""
    assert host_session.status()["permission_status"] == "disabled"


def test_lifecycle_has_no_decision_and_closes_only_main(launched, monkeypatch):
    run(launched, event(launched), monkeypatch)
    assert run(launched, event(launched, "Stop", agent_id="child"), monkeypatch) == ""
    assert run(launched, event(launched, "SessionEnd", agent_id="child"), monkeypatch) == ""
    assert host_session.status()["status"] == "open"
    assert run(launched, event(launched, "Stop"), monkeypatch) == ""
    assert run(launched, event(launched, "SessionEnd"), monkeypatch) == ""
    assert host_session.status()["closure_source"] == "native"
    assert run(launched, event(launched, "PermissionRequest"), monkeypatch) == ""
    names = [record["event"] for record in log.read("fixture-native")]
    assert names == ["host_connected", "stop", "session_end"]


def test_real_watcher_reuses_grant_exhausts_budget_and_cancels_on_native_end(launched):
    from scope.watch import Watcher

    class FixtureUI:
        interactive = True
        calls = 0

        def review(self, request, card, context):
            self.calls += 1
            return {"action": "grant"} if self.calls == 1 else {"action": "abstain"}

        def notice(self, message):
            pass

    host_hook.process(event(launched), host=launched[0])
    ui = FixtureUI()
    with Watcher(ui) as watcher:
        reply = ipc.exchange({"kind": "proposal", "session_id": "fixture-native", "cwd": str(launched[1]),
                              "card": {"summary": "Fixture local staging", "commands": ["git add *"], "domains": [], "budget": 2}})
        assert reply == {"accepted": True}
        request = event(launched, "PermissionRequest", tool_input={"command": "git add file.py"})
        assert host_hook.process(request, host=launched[0]) == wire.decision_json("allow")
        assert host_hook.process(request, host=launched[0]) == wire.decision_json("allow")
        assert ui.calls == 1
        assert host_hook.process(request, host=launched[0]) == ""
        assert ui.calls == 2
        assert host_hook.process(event(launched, "SessionEnd"), host=launched[0]) == ""
        assert watcher.closed.wait(1)
    assert host_session.status()["status"] == "closed"


def test_native_close_while_permission_engine_waits_invalidates_output(launched, monkeypatch):
    host_hook.process(event(launched), host=launched[0])

    def late(event):
        host_session.close_session("fixture-native", inferred=True)
        return wire.decision_json("allow")

    monkeypatch.setattr(host_hook, "_permission", late)
    assert run(launched, event(launched, "PermissionRequest"), monkeypatch) == ""
