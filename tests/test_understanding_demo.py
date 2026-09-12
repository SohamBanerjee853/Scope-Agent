"""Disposable preparation and real, explicitly scripted combined rehearsal."""

import json
from pathlib import Path
import sys

import pytest

from scope import demo, demo_agent, paths
from scope.cli import main


def test_prepare_only_is_isolated_from_parent_and_installs_project_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "real-parent")
    root = tmp_path / "disposable"
    result = demo.prepare(root)
    assert result["session_id"].startswith("demo-") and result["session_id"] != "real-parent"
    assert (root / ".git").is_dir()
    assert (root / ".agents/skills/scope-understand/SKILL.md").is_file()
    assert not paths.scope_home().exists()
    assert demo_agent.probe(root)["charge_count"] == 2


def test_fresh_demo_ids_do_not_repeat_when_parent_identity_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "real-parent")
    assert demo.prepare(tmp_path / "one")["session_id"] != demo.prepare(tmp_path / "two")["session_id"]


def test_dry_run_creates_nothing_and_starts_no_process(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("preview started a process")
    monkeypatch.setattr(demo.runner, "run", forbidden)
    assert demo.prepare(tmp_path / "preview", dry_run=True)["dry_run"] is True
    assert not (tmp_path / "preview").exists()


def test_nonempty_directory_and_repeat_preparation_preserve_files(tmp_path):
    root = tmp_path / "existing"
    root.mkdir()
    original = root / "important.txt"
    original.write_text("existing work")
    with pytest.raises(ValueError, match="new or empty"):
        demo.prepare(root)
    assert original.read_text() == "existing work"
    fresh = tmp_path / "fixture"
    demo.prepare(fresh)
    marker = (fresh / demo.MARKER).read_bytes()
    with pytest.raises(ValueError):
        demo.prepare(fresh)
    assert (fresh / demo.MARKER).read_bytes() == marker


def test_adapter_accepts_only_the_known_stable_key_repair(tmp_path):
    demo.prepare(tmp_path)
    source = tmp_path / "payment.py"
    source.write_text(source.read_text().replace(demo_agent.BUGGY_KEY, demo_agent.FIXED_KEY))
    observed = demo_agent.probe(tmp_path)
    assert observed["charge_count"] == 1 and observed["charged_cents"] == 100
    source.write_text("raise AssertionError('must never execute')\n")
    with pytest.raises(ValueError, match="only the bundled"):
        demo_agent.probe(tmp_path)


def test_adapter_isolates_python_from_project_module_injection(tmp_path):
    demo.prepare(tmp_path)
    (tmp_path / "json.py").write_text("raise AssertionError('project import must not execute')\n")
    assert demo_agent.probe(tmp_path)["charge_count"] == 2


@pytest.mark.parametrize("invalid", [[], {"schema_version": True, "fixture": "scope-payment-retry", "session_id": "demo-123"}])
def test_invalid_marker_never_launches_probe(tmp_path, monkeypatch, invalid):
    demo.prepare(tmp_path)
    (tmp_path / demo.MARKER).write_text(json.dumps(invalid))
    monkeypatch.setattr(demo_agent.runner, "run", lambda *a, **kw: pytest.fail("invalid marker launched"))
    with pytest.raises(ValueError, match="marker"):
        demo_agent.probe(tmp_path)


def test_scripted_flow_refuses_caller_directory_without_running_fixtures(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(demo.subprocess, "run", lambda *a, **kw: pytest.fail("invalid fixture destination ran"))
    assert main(["demo", "--scripted", str(tmp_path / "never")]) == 1
    assert "creates its own temporary project" in capsys.readouterr().err
    assert not (tmp_path / "never").exists()


def test_demo_adapter_cli_returns_actual_json(tmp_path, capsys):
    assert main(["demo", "--prepare-only", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["prepared"] is True
    assert main(["demo-adapter", "probe", "--cwd", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["charge_count"] == 2


def test_windows_reparse_destination_is_refused_without_starting_work(tmp_path, monkeypatch):
    from types import SimpleNamespace
    original = Path.lstat
    def attributes(path):
        if path == tmp_path:
            return SimpleNamespace(st_file_attributes=0x400, st_mode=0o40755)
        return original(path)
    monkeypatch.setattr(Path, "lstat", attributes)
    with pytest.raises(ValueError, match="symbolic links"):
        demo.prepare(tmp_path / "demo")
    assert not (tmp_path / "demo").exists()


@pytest.mark.parametrize("shell", ["posix", "powershell"])
def test_scripted_demo_runs_real_engine_tcp_questions_probes_repair_and_receipt(tmp_path, monkeypatch, shell):
    from scope.smoke import _ALLOW
    monkeypatch.setenv("CODEX_THREAD_ID", "real-parent-session")
    monkeypatch.setenv("SCOPE_LAUNCH_ID", "real-parent-launch")
    parent = paths.scope_home()
    parent.mkdir()
    protected = parent / "unchanged"
    protected.write_bytes(b"real parent must remain untouched")
    # The public demo must isolate user configuration itself: the installed
    # checker also isolates its caller, which would otherwise hide this bug.
    user_home = tmp_path / "parent-user"
    user_home.mkdir()
    excludes = user_home / ".gitignore_global"
    excludes.write_bytes(b"*.py\n")
    config = user_home / ".gitconfig"
    config.write_text("[core]\n\texcludesFile = " + json.dumps(excludes.as_posix(), ensure_ascii=False) + "\n",
                      encoding="utf-8")
    original_user_files = {path.name: path.read_bytes() for path in user_home.iterdir()}
    for name in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(name, str(user_home))
    report = demo.run_scripted(shell)

    assert report["session_id"].startswith("demo-") and report["session_id"] != "real-parent-session"
    assert report["shell"] == shell and report["verified"] is True
    assert report["provenance"] == "test_fixture"
    assert report["human_answers"] is report["host_execution"] is report["model_execution"] is False
    assert report["native_shell_execution"] is report["external_network_services"] is False
    assert report["temporary_files_removed"] is True and not Path(report["project"]).parent.exists()
    assert list(parent.iterdir()) == [protected]
    assert protected.read_bytes() == b"real parent must remain untouched"
    assert {path.name: path.read_bytes() for path in user_home.iterdir()} == original_user_files

    before, after = report["checks"]
    assert [entry["observation"]["status"] for entry in (before, after)] == ["mismatched", "matched"]
    assert [entry["observation"]["actual"] for entry in (before, after)] == [2, 1]
    assert [entry["prediction"]["value"] for entry in (before, after)] == [1, 1]
    assert before["references"][0]["sha256"] != after["references"][0]["sha256"]
    for entry, amount in ((before, 200), (after, 100)):
        assert entry["phase"] == "completed"
        assert entry["prediction"]["provenance"] == entry["approval"]["provenance"] == "test_fixture"
        assert entry["approval"]["approved"] is True
        execution = entry["execution"]
        assert execution["argv"] == ["scope", "demo-adapter", "probe"]
        assert execution["cwd"] == report["project"] and execution["exit_code"] == 0
        assert execution["timed_out"] is execution["stdout_truncated"] is execution["stderr_truncated"] is False
        assert execution["error"] is None
        observed = json.loads(execution["stdout"])
        assert observed["charged_cents"] == amount and observed["lost_acknowledgements"] == 1

    assert [(run["passed"], run["failed"]) for run in report["regressions"]] == [(2, 1), (3, 0)]
    assert [run["execution"]["exit_code"] for run in report["regressions"]] == [1, 0]
    assert report["patch"]["inbox_consumed"] is True and report["patch"]["coding_host_executed"] is False
    assert report["patch"]["handoff_id"] == report["handoff"]["handoff_id"]
    assert report["handoff"]["status"] == "dispatched" and report["handoff"]["provenance"] == "test_fixture"
    assert "fixture inbox" in report["delivery_target"]
    assert report["card"]["commands"] == ["scope demo-adapter probe"] and report["card"]["budget"] == 3
    assert [item["stdout"] for item in report["wire_results"]] == [_ALLOW, _ALLOW, "", ""]
    assert [item["execution_check_id"] for item in report["wire_results"][:2]] == [before["check_id"], after["check_id"]]
    assert all(item["exit_code"] == 0 for item in report["wire_results"])
    assert report["wire_results"][-1]["command"] == "git push origin main"
    assert report["unused_budget_before_revoke"] == 1 and report["revoked"] is True
    assert [item["kind"] for item in report["fixture_questions"]] == [
        "prediction", "probe_approval", "next_task", "prediction", "probe_approval"]
    assert all(item["route"] == "authenticated_tcp_watcher" for item in report["fixture_questions"])
    assert report["event_order"].count("session_end") == 1
    assert report["receipt"]["session_id"] == report["session_id"]
    assert report["receipt"]["counts"] == {"requests": 4, "auto_allowed": 2, "allowed_once": 0,
                                           "denied": 0, "hard_asks": 1, "scopes_granted": 1}
    understanding = report["receipt"]["understanding"]
    for name, count in {"tasks": 1, "predictions": 2, "executions": 2, "observations": 2,
                        "next_tasks": 1, "dispatches": 1, "revocations": 1}.items():
        assert len(understanding[name]) == count


def test_scripted_preview_has_no_process_files_or_new_session(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(demo.subprocess, "run", lambda *a, **kw: pytest.fail("preview launched a process"))
    assert main(["demo", "--scripted", "--dry-run", "--json", "--shell", "powershell"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["mode"] == "scripted_plan" and plan["performed"] is False
    assert "session_id" not in plan and not list(tmp_path.iterdir())


def test_scripted_environment_removes_parent_identity_and_git_selection(tmp_path, monkeypatch):
    for name in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_TASK_ID", "CLAUDECODE", "SCOPE_LAUNCH_ID",
                 "CLAUDE_SESSION_ID", "GIT_DIR", "GIT_WORK_TREE", "_SCOPE_SMOKE_ROOT", "PYTHONPATH"):
        monkeypatch.setenv(name, "parent-value")
    environment = demo._scripted_environment(tmp_path, "posix")
    assert not any(name in environment for name in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_TASK_ID",
                   "CLAUDECODE", "SCOPE_LAUNCH_ID", "CLAUDE_SESSION_ID", "GIT_DIR", "GIT_WORK_TREE",
                   "_SCOPE_SMOKE_ROOT", "PYTHONPATH"))
    assert environment["SCOPE_HOME"] == str(tmp_path / "scope")
    assert environment["CODEX_HOME"] == str(tmp_path / "codex")
    assert environment["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude")


@pytest.mark.parametrize("diagnostic, expected", [
    ("scope demo: isolated fixture verification failed [first_check]", "failed [first_check]"),
    ("secret command or path must not be propagated", "failed; no live host"),
])
def test_scripted_worker_failure_exposes_only_static_stage(tmp_path, monkeypatch, diagnostic, expected):
    from types import SimpleNamespace
    monkeypatch.setattr(demo.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout="", stderr=diagnostic))
    with pytest.raises(RuntimeError) as caught:
        demo.run_scripted()
    assert expected in str(caught.value) and "secret command" not in str(caught.value)


def test_fixture_repair_requires_actual_matching_inbox_delivery(tmp_path):
    demo.prepare(tmp_path)
    before = (tmp_path / "payment.py").read_bytes()
    agent = demo._FixtureAgent()
    with pytest.raises(RuntimeError, match="delivered instruction"):
        agent.repair(tmp_path, "missing")
    assert agent.deliver({"instruction": demo._REPAIR, "provenance": "human_ipc"}) is False
    assert agent.inbox == [] and (tmp_path / "payment.py").read_bytes() == before
    assert agent.deliver({"instruction": demo._REPAIR, "provenance": "test_fixture", "handoff_id": "fixture"}) is True
    patch = agent.repair(tmp_path, "fixture")
    assert patch["inbox_consumed"] is True and agent.inbox == []
    with pytest.raises(RuntimeError, match="delivered instruction"):
        agent.repair(tmp_path, "fixture")
