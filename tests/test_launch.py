"""Launcher configuration and lifecycle use disposable homes and fake hosts."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tomllib

import pytest

from scope import host_session, launch, paths


@pytest.fixture(autouse=True)
def isolate_parent_identity(monkeypatch):
    for name in (*launch._PARENT_MARKERS, "SCOPE_LAUNCH_ID", "SCOPE_HOST"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".git").mkdir()
    return root


def plan_for(project, *, host="codex", permissions=True, understanding=True, manual_watch=True):
    return launch.preflight(host, cwd=project, permissions=permissions, understanding=understanding,
                            manual_watch=manual_watch, dry_run=True)


@pytest.mark.parametrize("host", ["codex", "claude"])
@pytest.mark.parametrize("permissions,understanding", [(True, True), (True, False), (False, True)])
def test_dry_run_does_not_create_files_or_start_any_process(project, monkeypatch, capsys, host, permissions, understanding):
    before = set(project.iterdir())
    monkeypatch.setattr(launch.subprocess, "run", lambda *a, **kw: pytest.fail("dry run started a process"))
    monkeypatch.setattr(launch.subprocess, "Popen", lambda *a, **kw: pytest.fail("dry run started a host"))
    arguments = [host, "literal task", "-C", str(project), "--dry-run", "-m", "fixture-model"]
    arguments += ["--permissions-only"] if not understanding else ["--understanding-only"] if not permissions else []
    assert launch.main(arguments) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["performed"] is False and plan["dry_run"] is True
    assert plan["permissions"] is permissions and plan["understanding"] is understanding
    assert ("PermissionRequest" in plan["hook_additions"]) is permissions
    assert set(project.iterdir()) == before and not paths.scope_home().exists()


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_invocation_settings_preserve_visible_user_hooks_and_restrictions(project, monkeypatch, host):
    directory = paths.codex_home() if host == "codex" else Path(os.environ["CLAUDE_CONFIG_DIR"])
    directory.mkdir()
    path = directory / ("config.toml" if host == "codex" else "settings.json")
    data = (b'approval_policy = "on-request"\nsandbox_mode = "read-only"\n[[hooks.Stop]]\n'
            b'[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "other-tool audit"\n' if host == "codex" else
            b'{"permissions":{"defaultMode":"default","deny":["Bash(rm *)"]},"hooks":{"Stop":[{"hooks":[{"type":"command","command":"other-tool audit"}]}]}}')
    path.write_bytes(data)
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-native")
    monkeypatch.setenv("CLAUDECODE", "parent-nesting")
    monkeypatch.setenv("FIXTURE_AUTH_MARKER", "preserved-private-value")
    monkeypatch.setenv("PATH", os.defpath)
    plan = plan_for(project, host=host)
    plan["host_executable"] = sys.executable
    home, definition = launch.prepare_launch(plan)
    assert path.read_bytes() == data
    assert definition["parent_session_ids"] == ["parent-native"]
    environment = launch._read_private(home / "environment.json")
    assert environment["FIXTURE_AUTH_MARKER"] == "preserved-private-value"
    assert environment["CODEX_HOME"] == os.environ["CODEX_HOME"]
    assert environment["CLAUDE_CONFIG_DIR"] == os.environ["CLAUDE_CONFIG_DIR"]
    assert environment["SCOPE_HOME"] == str(home) and environment["SCOPE_POPUP"] == "0"
    assert "CODEX_THREAD_ID" not in environment and "CLAUDECODE" not in environment
    selected_scope = subprocess.run([sys.executable, "-c", "import shutil; print(shutil.which('scope'))"],
                                    cwd=project, env=environment, text=True, capture_output=True, check=True)
    assert Path(selected_scope.stdout.strip()) == Path(plan["scope_executable"])
    record = launch._read_private(home / "launcher.json")
    argv = record["argv"]
    assert not any(flag in argv for flag in ("--dangerously-skip-permissions", "--yolo", "--full-auto", "--sandbox", "--ask-for-approval"))
    if host == "codex":
        for index, argument in enumerate(argv):
            if argument == "-c":
                document = tomllib.loads(argv[index + 1])
                assert set(document) == {"hooks"}
                groups = next(iter(document["hooks"].values()))
                assert len(groups) == 1 and "host-hook --host codex" in groups[0]["hooks"][0]["command"]
    else:
        settings = launch._read_private(home / "claude-settings.json")
        assert set(settings) == {"hooks"} and len(settings["hooks"]["Stop"]) == 1
        assert "--append-system-prompt" in argv and "--system-prompt" not in argv
        assert "scope ready" in argv[argv.index("--append-system-prompt") + 1]


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_duplicate_scope_install_is_refused_without_global_edits(project, host):
    directory = paths.codex_home() if host == "codex" else Path(os.environ["CLAUDE_CONFIG_DIR"])
    directory.mkdir()
    path = directory / ("hooks.json" if host == "codex" else "settings.json")
    original = json.dumps({"hooks": {"PermissionRequest": [{"hooks": [{"type": "command", "command": "/old/scope hook"}]}]}}).encode()
    path.write_bytes(original)
    with pytest.raises(ValueError, match="migration"):
        plan_for(project, host=host)
    assert path.read_bytes() == original and not paths.scope_home().exists()


def test_powershell_hook_formatter_quotes_literal_paths_and_codex_keeps_both_dialects(tmp_path):
    executable = tmp_path / "odd ' name" / "scope"
    codex = launch.hook_groups("codex", executable)
    command = codex["PermissionRequest"][0]["hooks"][0]
    assert command["commandWindows"].startswith("& '") and "''" in command["commandWindows"]
    claude = launch.hook_groups("claude", executable, windows=True)["PermissionRequest"][0]["hooks"][0]
    assert claude["shell"] == "powershell" and claude["command"].startswith("& '")
    assert "commandWindows" not in claude


def test_printed_powershell_replay_preserves_exact_windows_paths_and_quotes(capsys):
    result = {"exit_code": 7, "session_id": "fixture-session", "receipt_available": True,
              "home": r"\\server\share\review λ"}
    launch._print_exit(result, r"C:\Program Files\O'Brien\scope.exe", windows=True)
    assert capsys.readouterr().out.splitlines() == [
        "Scope host exit: 7",
        r"Receipt: & 'C:\Program Files\O''Brien\scope.exe' 'receipt' 'fixture-session' '--home' '\\server\share\review λ'",
    ]


def test_printed_posix_replay_roundtrips_exact_arguments(capsys):
    executable = "/fixture/space ' and \\\" λ/scope"
    home = "/fixture/run ' and \\\" λ"
    result = {"exit_code": 0, "session_id": "fixture-session", "receipt_available": True, "home": home}
    launch._print_exit(result, executable, windows=False)
    command = capsys.readouterr().out.splitlines()[1].removeprefix("Receipt: ")
    assert shlex.split(command) == [executable, "receipt", "fixture-session", "--home", home]


def test_command_display_rejects_terminal_controls_without_escaping_printable_paths(capsys):
    for control in ("\x1b", "\x9b", "\u202e"):
        with pytest.raises(ValueError, match="control characters"):
            launch._quote(["scope", control])
        result = {"exit_code": 7, "session_id": "fixture" + control, "receipt_available": True,
                  "home": "C:\\fixture\\run" + control}
        launch._print_exit(result, "scope", windows=True)
        output = capsys.readouterr().out
        assert control not in output and "Receipt: " not in output
        assert "Receipt saved" in output and "C:\\fixture\\run" in output


def test_nested_claude_parent_identity_is_preserved_without_environment_session_markers(project):
    plan = plan_for(project, host="claude")
    plan["host_executable"] = sys.executable
    parent_home, parent = launch.prepare_launch(plan)
    with launch._environment(launch._load_environment(parent_home, parent)):
        assert not any(name in os.environ for name in launch._PARENT_MARKERS)
        host_session.register("fixture-claude-parent", host="claude", cwd=str(project), source="startup")
        child_plan = plan_for(project, host="codex")
        child_plan["host_executable"] = sys.executable
        child_home, child = launch.prepare_launch(child_plan)
        assert child["parent_session_ids"] == ["fixture-claude-parent"]
        assert child["launch_id"] != parent["launch_id"] and child_home != parent_home
        with launch._environment(launch._load_environment(child_home, child)):
            with pytest.raises(host_session.HostSessionError):
                host_session.register("fixture-claude-parent", host="codex", cwd=str(project), source="startup")
        before = set((parent_home / "runs").iterdir())
        os.environ["SCOPE_LAUNCH_ID"] = "invalid-prior-launch"
        with pytest.raises(host_session.HostSessionError):
            launch.prepare_launch(child_plan)
        assert set((parent_home / "runs").iterdir()) == before


def test_non_git_understanding_and_noninteractive_launch_are_actionable(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="Git project"):
        plan_for(tmp_path)
    plan = plan_for(tmp_path, understanding=False)
    assert plan["permissions"] is True
    monkeypatch.setattr(launch.shutil, "which", lambda name: sys.executable)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(ValueError, match="interactive terminal"):
        launch.preflight("codex", cwd=tmp_path, understanding=False, manual_watch=True)


def test_owner_lifetime_lock_and_start_marker_cannot_be_reused(project):
    plan = plan_for(project)
    plan["host_executable"] = sys.executable
    home, definition = launch.prepare_launch(plan)
    assert launch.owner_started(home) is False and launch.owner_alive(home) is False
    with launch.owner_lock(home):
        assert launch.owner_started(home) is True and launch.owner_alive(home) is True
        with pytest.raises(ValueError, match="already had an owner"):
            with launch.owner_lock(home):
                pytest.fail("second owner entered")
    assert launch.owner_alive(home) is False and launch.owner_started(home) is True
    with pytest.raises(ValueError, match="fresh launch"):
        with launch.owner_lock(home):
            pytest.fail("ended owner was reused")


def test_crashed_owner_releases_actual_os_lock_but_keeps_start_evidence(project):
    plan = plan_for(project)
    plan["host_executable"] = sys.executable
    home, definition = launch.prepare_launch(plan)
    code = ("import sys; from scope import launch; "
            "lock=launch.owner_lock(sys.argv[1]); lock.__enter__(); "
            "print('held',flush=True); sys.stdin.read()")
    child = subprocess.Popen([sys.executable, "-c", code, str(home)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        assert launch.owner_alive(home) is True and launch.owner_started(home) is True
        child.kill()
        child.wait(timeout=5)
        assert launch.owner_alive(home) is False and launch.owner_started(home) is True
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_readiness_requires_actual_registration_and_reachable_review(project, monkeypatch):
    from scope import ipc, log
    plan = plan_for(project)
    plan["host_executable"] = sys.executable
    home, definition = launch.prepare_launch(plan)
    for name, value in launch.child_environment(home, definition["launch_id"], "codex").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(ipc, "exchange", lambda *a, **kw: {"ready": True})
    waiting = launch.readiness()
    assert waiting["ready"] is False and waiting["status"] == "waiting"
    host_session.register("fixture-native", host="codex", cwd=str(project), source="startup")
    connected = launch.readiness()
    assert connected["ready"] is True and connected["permission_status"] == "configured/waiting"
    host_session.observe_permission("fixture-native", host="codex", cwd=str(project))
    assert launch.readiness()["permission_status"] == "requests observed"
    monkeypatch.setattr(ipc, "exchange", lambda *a, **kw: None)
    assert launch.readiness()["ready"] is False
    ready_events = [event["fields"] for event in log.read("fixture-native") if event["event"] == "client_ready"]
    assert [event["permission_status"] for event in ready_events] == ["configured/waiting", "requests observed"]
    assert all(event["host"] == "codex" and event["launch_id"] == definition["launch_id"]
               and event["session"] == "fixture-native" and event["review_reachable"] is True for event in ready_events)


@pytest.mark.parametrize("inside", [False, True])
def test_tmux_targets_actual_pane_ids_even_with_nonzero_numbering(project, monkeypatch, inside):
    plan = plan_for(project, manual_watch=False)
    plan.update(host_executable=sys.executable, tmux_executable="/fixture/tmux")
    home, definition = launch.prepare_launch(plan)
    if inside:
        monkeypatch.setenv("TMUX", "/owned/socket,100,7")
    else:
        monkeypatch.delenv("TMUX", raising=False)
    calls = []
    def tmux(arguments, **kwargs):
        calls.append((arguments, kwargs))
        if arguments[0] == "display-message":
            return "$4"
        if arguments[0] in {"new-window", "new-session"}:
            return "@12\t%41"
        if arguments[0] == "split-window":
            assert arguments[arguments.index("-t") + 1] == "%41"
            return "%49"
        return ""
    monkeypatch.setattr(launch, "_tmux", tmux)
    panes = launch.open_panes(home, plan)
    assert panes["agent_pane"] == "%41" and panes["review_pane"] == "%49" and panes["window"] == "@12"
    assert panes["server"] == (None if inside else definition["launch_id"])
    assert calls[-1][0] == ["select-pane", "-t", "%41"]
    assert not any("kill-server" in arguments for arguments, kwargs in calls)


@pytest.mark.parametrize("lose_review,receipt_failure", [(False, False), (True, False), (False, True)])
def test_fake_host_exit_finalizes_only_native_run_and_preserves_exit_status(project, monkeypatch, capsys, lose_review, receipt_failure):
    from scope import ipc, receipt
    from scope.watch import Watcher
    plan = plan_for(project)
    plan["host_executable"] = sys.executable
    home, definition = launch.prepare_launch(plan)
    record = launch._read_private(home / "launcher.json")
    script = ("import os; from scope import host_hook; "
              "host_hook.process({'hook_event_name':'SessionStart','session_id':'fixture-child-native',"
              "'cwd':os.getcwd(),'source':'startup'},host='codex'); ")
    if lose_review:
        script += "from scope import ipc; import time; ipc.exchange({'kind':'shutdown'}); time.sleep(2.8); "
    script += "raise SystemExit(7)"
    record["argv"] = [sys.executable, "-c", script]
    launch._write_private(home / "launcher.json", record)
    environment = launch._load_environment(home, definition)
    for name in tuple(os.environ):
        if name not in environment:
            monkeypatch.delenv(name)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    if receipt_failure:
        monkeypatch.setattr(receipt, "write", lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture disk failure")))
    with Watcher(human_timeout=2) as watcher:
        assert launch.run_agent(home) == 7
        assert watcher.closed.is_set()
    result = launch._read_private(home / "exit.json")
    assert result["exit_code"] == 7 and result["session_id"] == "fixture-child-native" and result["host_started"] is True
    assert host_session.status()["status"] == "closed"
    assert host_session.status()["closure_source"] == "launcher"
    assert launch.owner_started(home) is True and launch.owner_alive(home) is False
    assert not (home / "environment.json").exists()
    receipt_path = receipt.receipt_path("fixture-child-native", home=home)
    assert result["receipt_available"] is not receipt_failure
    if receipt_failure:
        assert not receipt_path.exists() and result["evidence_unavailable"] == ["receipt"]
    else:
        document = receipt.load(receipt_path)
        assert document["session_id"] == "fixture-child-native" and document["host"] == "codex"
        assert document["counts"]["requests"] == 0
    captured = capsys.readouterr()
    assert str(home) in captured.out
    if receipt_failure:
        assert "Receipt: " not in captured.out and "Receipt unavailable" in captured.out
        assert "Scope exit evidence unavailable: receipt" in captured.err
    else:
        assert "fixture-child-native" in captured.out
    assert captured.err.count("Scope review is unavailable") == (1 if lose_review else 0)
    from scope import log
    assert [event["event"] for event in log.read("fixture-child-native")].count("review_unavailable") == (1 if lose_review else 0)


def test_missing_reviewer_stops_before_host_launch_and_records_no_native_event(project, monkeypatch, capsys):
    plan = plan_for(project)
    plan["host_executable"] = sys.executable
    home, definition = launch.prepare_launch(plan)
    monkeypatch.setattr(launch, "_wait_review", lambda *a, **kw: (_ for _ in ()).throw(ValueError("review unavailable")))
    monkeypatch.setattr(launch.subprocess, "Popen", lambda *a, **kw: pytest.fail("host started without review"))
    assert launch.run_agent(home) == 1
    result = launch._read_private(home / "exit.json")
    assert result["session_id"] is None and result["host_started"] is False
    assert result["closure_source"] == "launcher_startup_failure"
    assert not (home / "sessions").exists()
    assert not (home / "environment.json").exists()
    assert host_session.status(home=home)["status"] == "closed"
