"""Smoke rehearsals use synthetic identities and explicit fixture decisions."""

import json
import os
from pathlib import Path
import socket
import subprocess
import uuid

import pytest

from scope import smoke


ALLOW = '{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}'


@pytest.mark.parametrize("shell", ["posix", "powershell"])
@pytest.mark.parametrize("live", [False, True])
def test_dry_run_has_no_files_processes_network_or_host_probes(shell, live, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run must perform no work")

    monkeypatch.setattr(smoke.tempfile, "TemporaryDirectory", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    argv = ["--shell", shell, "--dry-run", "--json"] + (["--live"] if live else [])
    assert smoke.main(argv) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["mode"] == ("live_plan" if live else "offline_plan")
    assert report["performed"] is False
    assert report["shell"] == shell
    assert report["host_execution"] is report["requested_command_execution"] is False
    if live:
        assert report["status"] == "manual_setup_required"
        assert "on-request" in captured.out and "trust" in captured.out


@pytest.mark.parametrize("shell", ["posix", "powershell"])
def test_real_offline_smoke_exact_wire_revocation_receipt_and_isolation(shell, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "unrelated-real-session")
    monkeypatch.setenv("SCOPE_LAUNCH_ID", "unrelated-launch")
    monkeypatch.setenv("CLAUDECODE", "unrelated-nesting-marker")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "unrelated-claude-session")
    inherited = dict(os.environ)
    for name in smoke._HOMES:
        directory = Path(inherited[name])
        directory.mkdir()
        (directory / "preserve-me").write_text("unrelated user's configuration", encoding="utf-8")
    invocations = []
    original = subprocess.run

    def capture(command, **kwargs):
        invocations.append((command, kwargs))
        return original(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", capture)
    report = smoke.run(shell)
    assert os.environ == inherited
    assert report["mode"] == "scripted_fixture" and report["verified"] is True
    assert report["session_id"].startswith("demo-smoke-")
    uuid.UUID(report["session_id"].removeprefix("demo-smoke-"))
    assert report["session_id"] != inherited["CODEX_THREAD_ID"]
    assert report["temporary_files_removed"] is True
    assert report["requested_command_execution"] is report["host_execution"] is False
    assert report["native_shell_execution"] is report["external_network_services"] is False
    assert "SCRIPTED FIXTURE" in report["human_answers"]
    assert report["card"]["budget"] == 3 and report["unused_budget_before_revoke"] == 1
    assert report["revoked"] is True
    assert [result["stdout"] for result in report["wire_results"]] == [ALLOW, ALLOW, "", ""]
    assert [result["exit_code"] for result in report["wire_results"]] == [0, 0, 0, 0]
    assert [result["tier"] for result in report["wire_results"]] == ["T2", "T2", "T2", "T3"]
    assert [review["fixture_action"] for review in report["fixture_reviews"]] == ["grant", "abstain"]
    assert report["session_end_stdout"] == ""
    assert report["receipt"]["counts"] == {
        "requests": 4, "auto_allowed": 2, "allowed_once": 0,
        "denied": 0, "hard_asks": 1, "scopes_granted": 1,
    }
    assert len(invocations) == 1
    argv, options = invocations[0]
    assert argv[1:] == ["-c", smoke._WORKER_ENTRY]
    assert "shell" not in options
    assert "CODEX_THREAD_ID" not in options["env"]
    assert "SCOPE_LAUNCH_ID" not in options["env"]
    assert "CLAUDECODE" not in options["env"]
    assert "CLAUDE_SESSION_ID" not in options["env"]
    for name in smoke._HOMES:
        assert options["env"][name] != inherited[name]
        assert not Path(options["env"][name]).exists()
        assert list(Path(inherited[name]).iterdir()) == [Path(inherited[name]) / "preserve-me"]


def test_every_rehearsal_gets_a_fresh_session(monkeypatch):
    sessions = []

    def fixture(command, **options):
        session = options["env"]["_SCOPE_SMOKE_SESSION"]
        sessions.append(session)
        report = {"mode": "scripted_fixture", "verified": True, "session_id": session,
                  "shell": options["env"]["_SCOPE_SMOKE_SHELL"]}
        return subprocess.CompletedProcess(command, 0, json.dumps(report), "")

    monkeypatch.setattr(subprocess, "run", fixture)
    smoke.run("posix")
    smoke.run("posix")
    assert len(set(sessions)) == 2


@pytest.mark.parametrize("failure", ["timeout", "exit", "invalid-json", "wrong-session"])
def test_worker_failures_cannot_report_success(failure, monkeypatch, capsys):
    def broken(command, **options):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 40)
        output = "not JSON" if failure == "invalid-json" else json.dumps({"session_id": "unrelated"})
        return subprocess.CompletedProcess(command, 1 if failure == "exit" else 0, output, "private diagnostic")

    monkeypatch.setattr(subprocess, "run", broken)
    assert smoke.main(["--json"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "failed" in captured.err and "private diagnostic" not in captured.err


@pytest.mark.parametrize("shell", ["posix", "powershell"])
def test_live_mode_refuses_without_starting_any_process(shell, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("live mode must not launch a host")

    monkeypatch.setattr(subprocess, "run", forbidden)
    assert smoke.main(["--shell", shell, "--live"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "scope smoke --live --dry-run" in captured.err
    assert "No host was started" in captured.err


def test_worker_rejects_inherited_or_missing_fixture_context(monkeypatch, capsys):
    monkeypatch.delenv("_SCOPE_SMOKE_ROOT", raising=False)
    assert smoke._worker_main() == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "verification failed" in captured.err


@pytest.mark.parametrize("shell", ["cmd", "bash -c", "", "unknown"])
def test_public_helpers_reject_unsupported_shell(shell):
    with pytest.raises(ValueError):
        smoke.plan(shell)
    with pytest.raises(ValueError):
        smoke.run(shell)
