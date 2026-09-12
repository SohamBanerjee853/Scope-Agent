"""Actual A2 + CLI + authenticated watcher tests in disposable fixture sessions."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import io
import json
import re
import subprocess
import sys
import threading
import time

import pytest

from scope import experience, ipc, learning, learning_cli, log, ui, watch_ui
from scope.watch import Watcher


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("SCOPE_LAUNCH_ID", raising=False)
    project = (tmp_path / "fixture project with spaces").resolve()
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / "probe.py").write_text("import json\nprint(json.dumps({'charges': 2}))\n", encoding="utf-8")
    return project


@pytest.fixture
def started(project):
    return learning.start(project, "Labeled fixture: inspect duplicate charges", session_id="fixture-a3-engine")


@pytest.fixture
def spec_file(project, tmp_path):
    target = tmp_path / "probe-spec.json"
    target.write_text(json.dumps({"question": "How many charges, and why?", "citations": ["probe.py:2"],
                                  "field": "charges", "argv": [sys.executable, "-B", "probe.py"],
                                  "shell": "posix", "timeout": 5}), encoding="utf-8")
    return target


@pytest.fixture
def fixture_engine(monkeypatch):
    """Verify production provenance, then label automated engine evidence accurately."""
    for name in ("check", "choose_next"):
        engine = getattr(experience, name)

        def fixture(*args, _engine=engine, **kwargs):
            assert kwargs["provenance"] == "human_ipc"
            kwargs["provenance"] = "test_fixture"
            return _engine(*args, **kwargs)

        monkeypatch.setattr(experience, name, fixture)


class TaggedFixture(watch_ui.TerminalUI):
    """Scripted answers use the real watcher's per-question reply tags."""
    interactive = True

    def __init__(self, answers):
        super().__init__(io.StringIO(), io.StringIO())
        self.answers = deque(answers)
        self.questions = []

    def question(self, repo, context_text, prompt, context):
        self.questions.append((repo, context_text, prompt))
        return super().question(repo, context_text, prompt, context)

    def _line(self, context):
        if not self.answers:
            return None
        answer = self.answers.popleft()
        if callable(answer):
            answer = answer(context)
        tag = re.findall(r"Reply with (\d+-[0-9a-f]{8}) followed by", self.output.getvalue())[-1]
        return tag + " " + answer

    def _discard(self):
        pass


def check_args(project, started, spec_file, *, as_json=True):
    return ["check", started["task_id"], "--spec", str(spec_file), "-C", str(project),
            *(["--json"] if as_json else [])]


@pytest.mark.parametrize("predicted,status", [(2, "matched"), (1, "mismatched")])
def test_cli_records_real_caller_probe_after_two_distinct_watcher_answers(
        project, started, spec_file, fixture_engine, monkeypatch, capsys, predicted, status):
    terminal = TaggedFixture([json.dumps({"value": predicted, "reason": "Labeled fixture hypothesis"}), "true"])
    calls = []
    original_run = experience.runner.run

    def observed_run(*args, **kwargs):
        calls.append((threading.current_thread().name, args, kwargs))
        state = learning.read_task(project, started["task_id"])
        record = next(iter(state["checks"].values()))
        assert record["phase"] == "running"
        assert record["prediction"]["value"] == predicted
        assert record["approval"]["approved"] is True
        return original_run(*args, **kwargs)

    monkeypatch.setattr(experience.runner, "run", observed_run)
    monkeypatch.setenv("CODEX_THREAD_ID", "unrelated-parent-must-not-own-evidence")
    with Watcher(ui=terminal) as watcher:
        assert learning_cli.main(check_args(project, started, spec_file)) == 0
        assert watcher.store.sessions() == ()
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["phase"] == "completed"
    assert result["observation"]["status"] == status
    assert json.loads(result["execution"]["stdout"]) == {"charges": 2}
    assert result["execution"]["exit_code"] == 0
    assert result["session_id"] == started["session_id"]
    assert result["prediction"]["provenance"] == "test_fixture"
    assert len(calls) == 1 and calls[0][0] == threading.current_thread().name
    assert calls[0][1] == ([sys.executable, "-B", "probe.py"],)
    assert len(terminal.questions) == 2
    consent = terminal.questions[1][1]
    assert json.dumps(str(project)) in consent
    assert json.dumps([sys.executable, "-B", "probe.py"]) in consent
    assert "This separate consent" in consent
    events = log.read(started["session_id"])
    assert [item["event"] for item in events] == ["task_start", "prediction", "probe_approval", "execution", "observation"]
    assert log.read("unrelated-parent-must-not-own-evidence") == []
    assert "Observed status" in output.err


@pytest.mark.parametrize("answer,reason", [("false", "declined"), ("yes", "missing or invalid consent"),
                                          ("1", "missing or invalid consent"),
                                          ('{"approved": true}', "missing or invalid consent")])
def test_cli_refusal_and_invalid_consent_save_prediction_but_never_run(
        project, started, spec_file, fixture_engine, monkeypatch, capsys, answer, reason):
    terminal = TaggedFixture(['{"value": 2, "reason": "Fixture hypothesis"}', answer])
    monkeypatch.setattr(experience.runner, "run", lambda *args, **kwargs: pytest.fail("no consent, no probe"))
    with Watcher(ui=terminal):
        assert learning_cli.main(check_args(project, started, spec_file)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["phase"] == "skipped" and result["stage"] == "consent"
    assert result["prediction"]["value"] == 2
    assert result["approval"]["approved"] is False
    assert result["reason"] == reason
    assert "execution" not in result
    assert "execution" not in [item["event"] for item in log.read(started["session_id"])]


@pytest.mark.parametrize("stage", ["prediction", "consent"])
def test_cli_shared_revoke_cancels_pending_answer_and_never_runs(
        project, started, spec_file, fixture_engine, monkeypatch, capsys, stage):
    entered = threading.Event()

    def delayed(context):
        entered.set()
        while context.active():
            time.sleep(0.005)
        return '{"value": 2, "reason": "Late fixture"}' if stage == "prediction" else "true"

    answers = [delayed] if stage == "prediction" else ['{"value": 2, "reason": "Fixture"}', delayed]
    terminal = TaggedFixture(answers)
    monkeypatch.setattr(experience.runner, "run", lambda *args, **kwargs: pytest.fail("cancelled check cannot run"))
    with Watcher(ui=terminal) as watcher, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(learning_cli.main, check_args(project, started, spec_file))
        assert entered.wait(3)
        assert ipc.exchange({"kind": "revoke"}, timeout=2) == {"revoked": True}
        assert future.result(timeout=3) == 1
        assert watcher.store.sessions() == ()
    result = json.loads(capsys.readouterr().out)
    assert result["phase"] == "skipped" and result["stage"] == stage
    assert "execution" not in result


def test_cli_missing_watcher_returns_saved_skip_with_actionable_error(project, started, spec_file, capsys):
    assert learning_cli.main(check_args(project, started, spec_file)) == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["phase"] == "skipped" and result["stage"] == "prediction"
    assert "Start scope watch" in output.err


def test_cli_t3_skips_before_any_question(project, started, spec_file, monkeypatch, capsys):
    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    spec["argv"] = ["git", "push"]
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setattr(ui, "ask", lambda *args, **kwargs: pytest.fail("T3 must never ask"))
    assert learning_cli.main(check_args(project, started, spec_file)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["phase"] == "skipped" and result["stage"] == "classification"
    assert "T3" in result["reason"] and "execution" not in result


def test_cli_not_verified_execution_reports_nonzero_without_claiming_repair(
        project, started, spec_file, fixture_engine, capsys):
    (project / "probe.py").write_text('print("not JSON")\n', encoding="utf-8")
    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    spec["citations"] = ["probe.py:1"]
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    with Watcher(ui=TaggedFixture(['{"value": 2, "reason": "Fixture"}', "true"])):
        assert learning_cli.main(check_args(project, started, spec_file, as_json=False)) == 1
    output = capsys.readouterr().out
    assert "completed" in output and "not_verified" in output
    assert "not proof of repair" in output
    saved = next(iter(learning.read_task(project, started["task_id"])["checks"].values()))
    assert saved["execution"]["exit_code"] == 0
    assert saved["observation"]["status"] == "not_verified"


@pytest.mark.parametrize("choice,larger,status", [("smaller", None, "selected"),
                                                 ("larger", "Audit all entry points", "selected"),
                                                 ("defer", None, "deferred"),
                                                 ("larger", None, "deferred"),
                                                 ("SMALLER", None, "deferred")])
def test_cli_next_records_exact_choice_without_acknowledging_printed_delivery(
        project, started, fixture_engine, capsys, choice, larger, status):
    args = ["next", "--task", started["task_id"], "--smaller", "Inspect per-order retry key", "-C", str(project), "--json"]
    if larger is not None:
        args.extend(["--larger", larger])
    terminal = TaggedFixture([choice])
    with Watcher(ui=terminal) as watcher:
        assert learning_cli.main(args) == 0
        assert watcher.store.sessions() == ()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status
    assert result["session_id"] == started["session_id"]
    assert result["delivery_status"] == "not_attempted"
    events = log.read(started["session_id"])
    assert "dispatch" not in [item["event"] for item in events]
    handoffs = learning.read_task(project, started["task_id"])["handoffs"]
    if status == "selected":
        assert handoffs[result["handoff_id"]]["status"] == "selected"
        assert result["instruction"] in terminal.questions[0][1]
    else:
        assert handoffs == {}


def test_cli_next_human_output_explains_undelivered_selection(project, started, fixture_engine, capsys):
    with Watcher(ui=TaggedFixture(["smaller"])):
        assert learning_cli.main(["next", started["task_id"], "--smaller", "Inspect the retry key", "-C", str(project)]) == 0
    output = capsys.readouterr().out
    assert "Next task: selected" in output
    assert "Printing an instruction is not acknowledged agent delivery" in output


@pytest.mark.parametrize("payload", [
    '{"value": 1, "value": 2, "reason": "fixture"}',
    '{"value": {"x": 1, "\\u0078": 2}, "reason": "fixture"}',
    '{"value": NaN, "reason": "fixture"}', '{"value": 1e999, "reason": "fixture"}',
    '{"value": 1, "reason": ""}', '{"value": 1, "reason": "   "}',
    '{"value": 1, "reason": "fixture", "approved": true}',
    '{"value": "\\ud800", "reason": "fixture"}',
    '{"reason": "fixture"}', 'null', 'false', '{"value": 1, "reason": "fixture"} trailing',
    '{"value": ' + '[' * 33 + '0' + ']' * 33 + ', "reason": "fixture"}',
])
def test_prediction_adapter_rejects_ambiguous_json_without_default_answers(payload, monkeypatch):
    monkeypatch.setattr(ui, "ask", lambda *args, **kwargs: {"answer": payload, "provenance": "human_ipc"})
    assert ui.answer_request(prediction_request()) is None


def prediction_request():
    return {"kind": "prediction", "repo": "/fixture", "task_id": "fixture-task",
            "spec": {"question": "What result?", "field": "field", "argv": ["probe", "argument"],
                     "shell": "posix", "timeout": 5}, "references": [], "context": {}}


@pytest.mark.parametrize("value", [None, False, 0, 0.0, "", {"a": [1, False, None]}])
def test_prediction_adapter_preserves_real_typed_values(value, monkeypatch):
    answer = {"value": value, "reason": "Explicit fixture reason", "assistance": ""}
    monkeypatch.setattr(ui, "ask", lambda *args, **kwargs: {"answer": json.dumps(answer), "provenance": "human_ipc"})
    result = ui.answer_request(prediction_request())
    assert result == answer and type(result["value"]) is type(value)


@pytest.mark.parametrize("reply", [None, {"error": "cancelled"}, {"answer": "true"},
                                   {"answer": "true", "provenance": "human_terminal"},
                                   {"answer": "true", "provenance": "human_ipc", "approved": True}])
def test_stage_adapter_never_converts_transport_success_into_consent(reply, monkeypatch):
    request = prediction_request()
    request.update(kind="probe_approval", classification={"name": "T2", "reason": "Fixture local probe"},
                   prediction={"value": 2, "reason": "Fixture prediction", "assistance": ""})
    monkeypatch.setattr(ui, "ask", lambda *args, **kwargs: reply)
    assert ui.answer_request(request) is None


def test_parser_bounds_nodes_bytes_and_nested_json():
    for payload, maximum in [("[" + "0," * 10000 + "0]", 128 * 1024),
                             (json.dumps("a" * 16385), 16384),
                             ("[" * 2000 + "0" + "]" * 2000, 16384)]:
        with pytest.raises(ValueError):
            ui.parse_json(payload, maximum=maximum)


def test_large_context_is_summarized_controls_are_escaped_and_command_stays_exact(monkeypatch, capsys):
    request = prediction_request()
    request["spec"]["argv"] = ["probe", "argument\nwith\x1b[2J controls"]
    request["context"] = {"description": "fixture\x1b[31m", "source": {"files": {}},
                          "checkpoints": [{"note": "fixture", "changes": {"diff": "x" * (512 * 1024)}}],
                          "observations": [{"current_status": "not_verified", "actual": "z" * 64000}]}
    rendered = ui.request_text(request)
    assert len(rendered) <= ui.MAX_TEXT
    assert "excerpt truncated" in rendered
    assert json.dumps(request["spec"]["argv"]) in rendered
    assert "not_verified" in rendered
    ui.show_request(request)
    assert "\x1b" not in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["probe_approval", "next_task"])
def test_command_and_instruction_that_cannot_fit_never_ask_for_a_truncated_choice(kind, monkeypatch):
    request = prediction_request()
    request["kind"] = kind
    if kind == "probe_approval":
        request.update(classification={"name": "T2", "reason": "Fixture local probe"},
                       prediction={"value": 2, "reason": "Fixture prediction", "assistance": ""})
        request["spec"]["argv"] = ["probe", "x" * 17000]
    else:
        request["options"] = {"smaller": "理解" * 2000, "defer": None}
    monkeypatch.setattr(ui, "ask", lambda *args, **kwargs: pytest.fail("must not ask with truncated consent"))
    with pytest.raises(ValueError, match="display limit"):
        ui.answer_request(request)


@pytest.mark.parametrize("payload", ['{"question": 1, "question": 2}', '{"timeout": NaN}', "[1]", "{}", "x" * 131073])
def test_invalid_spec_cannot_ask_or_create_checks(project, started, spec_file, monkeypatch, capsys, payload):
    spec_file.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(ui, "ask", lambda *args, **kwargs: pytest.fail("invalid spec cannot ask"))
    assert learning_cli.main(check_args(project, started, spec_file)) == 1
    assert capsys.readouterr().out == ""
    assert learning.read_task(project, started["task_id"])["checks"] == {}


@pytest.mark.parametrize("command,flags", [("check", ["--spec", "unused.json"]), ("next", ["--smaller", "Inspect source"])])
def test_engine_cli_requires_exactly_one_task(command, flags):
    for args in ([], ["first", "--task", "second"]):
        with pytest.raises(SystemExit) as error:
            learning_cli.main([command, *args, *flags])
        assert error.value.code == 2
