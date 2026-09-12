"""Offline fixtures: no live hosts, private policy discovery or probe execution."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from scope import coverage, execpolicy, transcript


def tool_call(arguments, *, name="shell", kind="function_call"):
    return {"type": "response_item", "payload": {
        "type": kind, "name": name, "arguments": arguments, "call_id": "fixture-call"}}


def write_transcript(tmp_path, records):
    path = tmp_path / "fixture.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def event(argv=None, *, command=None, request_id="fixture", shell="bash"):
    return {"event": "permission_request", "fields": {
        "request_id": request_id, "argv": argv, "command": command, "shell": shell}}


@pytest.fixture
def rule_file(tmp_path):
    path = tmp_path / "fixture.rules"
    path.write_text('prefix_rule(pattern=["git", "status"], decision="allow")\n', encoding="utf-8")
    return path


@pytest.fixture
def checker(monkeypatch):
    """Use Python to emit labeled fake native-policy output, never real Codex."""
    actual_popen = subprocess.Popen
    calls = []

    def install(source):
        monkeypatch.setattr(execpolicy.shutil, "which", lambda name: sys.executable)

        def fake_popen(argv, **kwargs):
            calls.append((argv, kwargs))
            return actual_popen([sys.executable, "-c", source, *argv], **kwargs)

        monkeypatch.setattr(execpolicy.subprocess, "Popen", fake_popen)
        return calls

    return install


@pytest.mark.parametrize("argv", [
    ["bash", "-lc", "git status"], ["powershell.exe", "-NoProfile", "-Command", "git status"],
    ["git", "status"], ["C:\\Program Files\\Git\\bin\\git.exe", "status"],
    ["/tmp/path with space/tool", "argument with 'quotes'"],
])
def test_structured_transcript_argv_is_preserved_without_execution(tmp_path, argv):
    path = write_transcript(tmp_path, [tool_call(json.dumps({"command": argv}))])
    result = transcript.read(path)
    assert result["status"] == "complete"
    assert result["commands"] == [{"source": "transcript", "record": 1, "tool": "shell",
                                   "argv": argv, "command": None, "status": "candidate",
                                   "reason": "structured argv request"}]
    assert "not permission decisions or proof of execution" in result["meaning"]


@pytest.mark.parametrize("name,field,shell", [
    ("exec_command", "cmd", "bash"), ("functions.exec_command", "cmd", "powershell"),
    ("shell", "command", "bash"), ("shell_command", "command", "powershell"),
])
def test_shell_command_strings_do_not_guess_native_argv(tmp_path, name, field, shell):
    command = "git status && echo 'fixture'" if shell == "bash" else "git status; Write-Output 'fixture'"
    path = write_transcript(tmp_path, [tool_call(json.dumps({field: command, "shell": shell}), name=name)])
    candidate = transcript.read(path)["commands"][0]
    assert candidate["status"] == "unknown"
    assert candidate["argv"] is None
    assert candidate["command"] == command


@pytest.mark.parametrize("name", ["exec_command", "functions.exec_command", "shell_command"])
def test_wrong_tool_schema_does_not_fabricate_native_argv(tmp_path, name):
    path = write_transcript(tmp_path, [tool_call({"command": ["git", "status"]}, name=name)])
    candidate = transcript.read(path)["commands"][0]
    assert candidate["status"] == "unknown"
    assert candidate["argv"] is None


@pytest.mark.parametrize("payload", [
    tool_call('await tools.exec_command({cmd:"git push"})', name="functions.exec", kind="custom_tool_call"),
    tool_call('{"cmd":"git push"}', name="functions.exec", kind="custom_tool_call"),
    tool_call('{"command":["git","status"]}', name="unknown_tool"),
    tool_call('"git status"'), tool_call("git status"), tool_call("[]"), tool_call(None),
    tool_call('{"command":["git","status"]}', name={"injected": True}),
])
def test_code_mode_and_unrecognized_arguments_remain_unknown(tmp_path, payload):
    path = write_transcript(tmp_path, [payload])
    result = transcript.read(path)
    assert len(result["commands"]) == 1
    assert result["commands"][0]["status"] == "unknown"
    assert result["commands"][0]["argv"] is None


@pytest.mark.parametrize("argv", [
    [], "git status", None, [1], [""], ["git\0"], ["git\nstatus"], ["git", None],
    ["git", "\ud800"], ["git"] * (transcript.MAX_ARGUMENTS + 1),
    ["git", "x" * transcript.MAX_ARGV_BYTES],
])
def test_invalid_structured_argv_is_never_eligible(tmp_path, argv):
    assert transcript.valid_argv(argv) is False
    result = transcript.read(write_transcript(tmp_path, [tool_call({"command": argv})]))
    assert result["commands"][0]["argv"] is None


def test_transcript_ignores_output_and_embedded_permission_claims(tmp_path):
    records = [
        {"type": "response_item", "payload": {"type": "function_call_output", "output":
            '{"behavior":"allow","exit_code":0,"command":["git","status"]}'}},
        {"type": "event_msg", "payload": {"command": ["git", "status"], "approved": True}},
        {"type": "response_item", "payload": {"type": [], "name": "shell"}},
        tool_call({"command": ["git", "status"]}),
    ]
    result = transcript.read(write_transcript(tmp_path, records))
    assert result["ignored_records"] == 3
    assert len(result["commands"]) == 1
    assert "execution" not in result["commands"][0]


def test_malformed_duplicate_and_partial_transcript_lines(tmp_path):
    path = tmp_path / "broken.jsonl"
    valid = json.dumps(tool_call({"command": ["git", "status"]})).encode() + b"\n"
    path.write_bytes(b"not json\n\xff\n" + b'{"type":1,"type":2}\n' + valid + valid[:-1])
    result = transcript.read(path)
    assert result["status"] == "truncated"
    assert result["malformed_records"] == 3
    assert len(result["commands"]) == 1
    assert result["commands"][0]["record"] == 4


@pytest.mark.parametrize("value", [None, "missing", "directory", "fifo"])
def test_unavailable_or_nonregular_transcript_is_bounded(tmp_path, value):
    if value == "directory":
        path = tmp_path
    elif value == "fifo" and os.name != "nt":
        path = tmp_path / "pipe"
        os.mkfifo(path)
    else:
        path = None if value is None else tmp_path / value
    result = transcript.read(path)
    assert result["status"] == "unavailable"
    assert result["commands"] == []


@pytest.mark.parametrize("limit", [0, -1, True, None, "10", transcript.MAX_BYTES + 1])
def test_invalid_transcript_limit_refuses_read(tmp_path, limit):
    assert transcript.read(tmp_path / "missing", limit=limit)["status"] == "unavailable"


def test_transcript_byte_line_and_command_limits(tmp_path):
    record = tool_call({"command": ["git", "status"]})
    path = write_transcript(tmp_path, [record] * (transcript.MAX_COMMANDS + 1))
    result = transcript.read(path)
    assert len(result["commands"]) == 1000
    assert result["command_limit_reached"] is True
    prefix = json.dumps(record).encode() + b"\n"
    path.write_bytes(prefix + b"x" * 1000)
    result = transcript.read(path, limit=len(prefix) + 10)
    assert result["bytes_read"] == len(prefix) + 10
    assert result["truncated"] is True
    assert len(result["commands"]) == 1
    path.write_bytes(b"x" * (transcript.MAX_LINE + 1) + b"\n" + prefix)
    result = transcript.read(path)
    assert result["malformed_records"] == 1
    assert result["status"] == "partial"
    assert len(result["commands"]) == 1
    assert transcript.MAX_BYTES == 16 * 1024 * 1024


@pytest.mark.parametrize("decision", ["allow", "prompt", "forbidden"])
@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_native_policy_check_passes_only_explicit_rules_and_literal_argv(
        tmp_path, rule_file, checker, decision, shell):
    result = {"decision": decision, "matchedRules": [{"prefixRuleMatch": {
        "matchedPrefix": [shell], "decision": decision}}]}
    calls = checker("import json; print(" + repr(json.dumps(result)) + ")")
    marker = tmp_path / "must-not-execute"
    command = "touch " + str(marker) if shell == "bash" else "New-Item " + str(marker)
    argv = [shell, "-c", command]
    response = execpolicy.check(argv, [rule_file], cwd=tmp_path)
    assert response["status"] == "evaluated"
    assert response["decision"] == decision
    assert response["matched_rules"] == result["matchedRules"]
    assert calls[0][0] == [sys.executable, "execpolicy", "check", "--rules", str(rule_file), "--", *argv]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["cwd"] == tmp_path
    assert not marker.exists()
    assert "historical native decisions and execution remain unknown" in response["meaning"]


@pytest.mark.parametrize("output", [
    {}, {"matchedRules": []}, {"decision": "allow", "matchedRules": []},
    {"decision": "deny", "matchedRules": [{}]}, {"decision": [], "matchedRules": [{}]},
    {"decision": "allow", "matchedRules": "yes"}, {"decision": "allow", "matchedRules": [1]},
    [], None, True,
])
def test_unmatched_or_unexpected_native_policy_output_is_unknown(rule_file, checker, output):
    checker("print(" + repr(json.dumps(output)) + ")")
    result = execpolicy.check(["git", "status"], [rule_file])
    assert result["status"] == "unknown"
    assert result["decision"] is None


@pytest.mark.parametrize("source", [
    "print('not json')", "import os; os.write(1,b'\\xff')",
    "print('{\"decision\":\"allow\",\"decision\":\"prompt\"}')",
    "print('{\"matchedRules\":NaN}')", "import sys; sys.exit(3)",
])
def test_native_policy_failures_are_unknown(rule_file, checker, source):
    checker(source)
    result = execpolicy.check(["git", "status"], [rule_file])
    assert result["status"] == "unknown"
    assert result["decision"] is None


@pytest.mark.parametrize("matches", [
    [{}], [{"prefixRuleMatch": {"matchedPrefix": [], "decision": "allow"}}],
    [{"prefixRuleMatch": {"matchedPrefix": ["git"], "decision": "forbidden"}}],
    [{"prefixRuleMatch": {"matchedPrefix": ["git"], "decision": True}}],
    [{"unknownRuleMatch": {"decision": "allow"}}],
])
def test_native_policy_match_evidence_must_support_decision(rule_file, checker, matches):
    checker("print(" + repr(json.dumps({"decision": "allow", "matchedRules": matches})) + ")")
    result = execpolicy.check(["git", "status"], [rule_file])
    assert result["status"] == "unknown"
    assert result["decision"] is None


@pytest.mark.parametrize("stream", ["stdout", "stderr", "both"])
def test_native_policy_output_is_capped_while_draining(rule_file, checker, stream):
    source = "import os\n"
    if stream in {"stdout", "both"}:
        source += "os.write(1,b'x'*1048576)\n"
    if stream in {"stderr", "both"}:
        source += "os.write(2,b'x'*1048576)\n"
    checker(source)
    result = execpolicy.check(["git", "status"], [rule_file])
    assert result["status"] == "unknown"
    assert result["truncated"] is True
    assert result["stdout_bytes"] <= 64 * 1024
    assert result["stderr_bytes"] <= 64 * 1024
    assert "stdout" not in result and "stderr" not in result


def test_native_policy_deadline_terminates_fake_checker(rule_file, checker):
    checker("import time; time.sleep(30)")
    started = time.monotonic()
    result = execpolicy.check(["git", "status"], [rule_file], timeout=0.15)
    assert result["status"] == "unknown"
    assert result["timed_out"] is True
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("timeout", [0, -1, True, "5", 5.01, float("inf"), float("nan")])
def test_invalid_native_policy_deadline_never_launches(rule_file, checker, timeout):
    calls = checker("raise AssertionError('must not launch')")
    assert execpolicy.check(["git", "status"], [rule_file], timeout=timeout)["status"] == "unknown"
    assert calls == []


@pytest.mark.parametrize("kind", ["none", "many", "missing", "directory", "huge"])
def test_invalid_explicit_rules_never_launch(tmp_path, rule_file, checker, kind):
    calls = checker("raise AssertionError('must not launch')")
    rules = [rule_file]
    if kind == "none":
        rules = []
    elif kind == "many":
        rules *= execpolicy.MAX_RULES + 1
    elif kind == "missing":
        rules = [tmp_path / "missing.rules"]
    elif kind == "directory":
        rules = [tmp_path]
    elif kind == "huge":
        rule_file.write_bytes(b"x" * (execpolicy.MAX_RULE_BYTES + 1))
    assert execpolicy.check(["git", "status"], rules)["status"] == "unknown"
    assert calls == []


def test_checker_absence_start_failure_and_windows_wrapper_remain_unknown(rule_file, monkeypatch):
    monkeypatch.setattr(execpolicy.shutil, "which", lambda name: None)
    assert execpolicy.check(["git", "status"], [rule_file])["status"] == "unknown"
    monkeypatch.setattr(execpolicy.shutil, "which", lambda name: "/unavailable/codex")
    assert execpolicy.check(["git", "status"], [rule_file])["status"] == "unknown"
    monkeypatch.setattr(execpolicy, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(execpolicy.shutil, "which", lambda name: "C:\\tools\\codex.cmd")
    result = execpolicy.check(["git", "status"], [rule_file])
    assert "native codex.exe required" in result["reason"]


def test_multiple_rules_and_option_shaped_candidate_do_not_change_checker_flags(tmp_path, rule_file, checker):
    second = tmp_path / "second rule.rules"
    second.write_text("# labeled fixture\n", encoding="utf-8")
    calls = checker('print(\'{"matchedRules":[]}\')')
    execpolicy.check(["--config", "approval_policy=never"], [rule_file, second])
    assert calls[0][0][-3:] == ["--", "--config", "approval_policy=never"]
    assert calls[0][0].count("--rules") == 2


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_audit_keeps_current_rule_estimates_separate_from_actual_decisions(shell):
    seen = []

    def fixture(argv, rules, *, timeout, cwd):
        seen.append((argv, rules, timeout, cwd))
        return {"status": "evaluated", "decision": "allow"}

    records = [event([shell, "-c", "git status"], shell=shell),
               {"event": "permission_decision", "fields": {"request_id": "fixture", "behavior": "allow"}},
               {"event": "permission_review_decision", "fields": {"request_id": "fixture", "behavior": "allow"}}]
    result = coverage.audit(records, rules=[Path("fixture.rules")], evaluator=fixture)
    assert result["counts"] == {"candidates": 1, "evaluated": 1, "rule_allow": 1,
                                "rule_prompt": 0, "rule_forbidden": 0, "unknown": 0}
    assert len(seen) == 1
    assert 0 < seen[0][2] <= 5
    assert result["native_decisions"] == result["execution"] == "unknown"
    assert "not permission counts" in result["meaning"]
    assert "actual execution" in result["meaning"]


@pytest.mark.parametrize("host", ["claude", "unknown", "Codex", ""])
def test_unsupported_host_never_reads_codex_transcript_or_rules(tmp_path, monkeypatch, host):
    def forbidden(*args, **kwargs):
        pytest.fail("unsupported host must not run Codex coverage")

    monkeypatch.setattr(transcript, "read", forbidden)
    result = coverage.audit([event(["git", "status"])], host=host,
                            transcript_path=tmp_path / "private", rules=[tmp_path / "private.rules"],
                            evaluator=forbidden)
    assert result["status"] == "unknown"
    assert result["counts"]["evaluated"] == 0


def test_audit_does_not_scan_environment_or_transcript_fields(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("no automatic file discovery/evaluation")

    monkeypatch.setattr(transcript, "read", forbidden)
    record = event(command="git status")
    record["fields"]["transcript_path"] = str(tmp_path / "private")
    result = coverage.audit([record], evaluator=forbidden)
    assert result["counts"]["unknown"] == 1
    assert result["transcript"] == {"status": "not_requested"}
    assert result["rule_files"] == []


def test_audit_rejects_embedded_code_even_with_native_rule_evaluator(tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("embedded code must never reach native policy checking")

    path = write_transcript(tmp_path, [tool_call(
        'await tools.exec_command({cmd:"git status"})', name="functions.exec", kind="custom_tool_call")])
    result = coverage.audit([], transcript_path=path, rules=[tmp_path / "fixture.rules"], evaluator=forbidden)
    assert result["counts"]["unknown"] == 1
    assert result["commands"][0]["argv"] is None


def test_audit_shared_five_second_deadline_is_not_reset_per_command(monkeypatch):
    now = [0.0]
    calls = []
    monkeypatch.setattr(coverage.time, "monotonic", lambda: now[0])

    def fixture(argv, rules, *, timeout, cwd):
        calls.append(timeout)
        now[0] += 4
        return {"status": "evaluated", "decision": "allow"}

    result = coverage.audit([event(["git", str(index)], request_id=str(index)) for index in range(4)],
                            rules=[Path("fixture.rules")], evaluator=fixture)
    assert calls == [5.0, 1.0]
    assert result["deadline_exhausted"] is True
    assert result["counts"]["evaluated"] == 1
    assert result["counts"]["unknown"] == 3


@pytest.mark.parametrize("outcome", [None, {"status": "evaluated", "decision": "deny"},
                                    {"status": "evaluated", "decision": []}, "raise"])
def test_audit_evaluator_failure_stays_unknown(outcome):
    def fixture(*args, **kwargs):
        if outcome == "raise":
            raise RuntimeError("labeled fake checker failure")
        return outcome

    result = coverage.audit([event(["git", "status"])], rules=[Path("fixture.rules")], evaluator=fixture)
    assert result["counts"]["unknown"] == 1
    assert result["counts"]["evaluated"] == 0


def test_audit_command_cap_applies_across_both_sources(tmp_path):
    records = [event(["git", "status"], request_id=str(index)) for index in range(999)]
    path = write_transcript(tmp_path, [tool_call({"command": ["git", "status"]})] * 3)
    calls = []

    def fixture(*args, **kwargs):
        calls.append(args)
        return {"status": "evaluated", "decision": "prompt"}

    result = coverage.audit(records, transcript_path=path,
                            rules=[Path("fixture.rules")], evaluator=fixture)
    assert len(calls) == 1000
    assert len(result["commands"]) == 1000
    assert result["command_limit_reached"] is True
    assert result["counts"]["rule_prompt"] == 1000


def test_audit_duplicate_requests_and_event_scan_cap(monkeypatch):
    result = coverage.audit([event(), event()])
    assert result["counts"]["candidates"] == 1
    monkeypatch.setattr(coverage, "MAX_EVENTS", 2)
    result = coverage.audit([{}, {}, event()])
    assert result["event_limit_reached"] is True
    assert result["counts"]["candidates"] == 0


def test_audit_missing_evidence_is_not_complete_coverage(tmp_path):
    result = coverage.audit([], transcript_path=tmp_path / "missing")
    assert result["status"] == "unknown"
    assert result["transcript"]["status"] == "unavailable"
    assert result["native_decisions"] == result["execution"] == "unknown"
