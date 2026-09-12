"""Presentation metadata stays bounded data while A2 answer types stay intact."""

from copy import deepcopy
import json

import pytest

from scope import review_presentation as presentation, ui


def request(kind="prediction", repo="/fixture project"):
    value = {"kind": kind, "repo": repo, "task_id": "task-fixture",
             "spec": {"question": "What will this result be?", "field": "answer",
                      "argv": ["/fixture/python", "probe.py", "a value; not shell syntax"],
                      "shell": "posix", "timeout": 5}, "references": [], "context": {}}
    if kind == "probe_approval":
        value.update(classification={"name": "T2", "reason": "Known fixture probe"},
                     prediction={"value": {"answer": [1, False, None]}, "reason": "Fixture reasoning",
                                 "assistance": "", "provenance": "test_fixture"})
    elif kind == "next_task":
        value["options"] = {"smaller": "Inspect the retry key", "larger": "Review both callers", "defer": None}
    return value


@pytest.mark.parametrize("kind", ["prediction", "probe_approval", "next_task"])
def test_engine_request_is_copied_to_stage_specific_form_without_answer_authority(kind):
    source = request(kind)
    original = deepcopy(source)
    result = presentation.from_request(source, source["repo"])
    assert result["version"] == 1 and result["kind"] == kind
    assert not result.keys() & {"answer", "approved", "default", "provenance"}
    assert source == original
    if kind == "next_task":
        assert result["options"] == source["options"]
        result["options"]["smaller"] = "changed copy"
    else:
        assert result["execution"]["cwd"] == source["repo"]
        assert result["execution"]["argv"] == source["spec"]["argv"]
        result["execution"]["argv"].append("changed copy")
    assert source == original
    if kind == "probe_approval":
        assert result["saved_prediction"] == {"value": {"answer": [1, False, None]},
                                               "reason": "Fixture reasoning", "assistance": ""}


@pytest.mark.parametrize("value", [None, False, 0, 0.0, "", [], {"nested": [True, 1]}])
def test_saved_prediction_retains_json_types(value):
    source = request("probe_approval")
    source["prediction"]["value"] = value
    result = presentation.from_request(source, source["repo"])["saved_prediction"]["value"]
    assert result == value and type(result) is type(value)


@pytest.mark.parametrize("key,value", [("version", True), ("version", 2), ("kind", "grant"),
                                      ("answer", "invented"), ("approved", True),
                                      ("provenance", "human_terminal"), ("default", "yes"),
                                      ("task_id", ""), ("details", "x" * (presentation.MAX_DETAILS + 1))])
def test_metadata_cannot_supply_defaults_answers_or_unknown_shape(key, value):
    source = request()
    result = presentation.from_request(source, source["repo"])
    result[key] = value
    with pytest.raises(ValueError):
        presentation.validate(result, source["repo"])


@pytest.mark.parametrize("key,value", [("cwd", "/another-project"), ("timeout", True), ("timeout", 0),
                                      ("timeout", float("inf")), ("shell", "cmd"),
                                      ("argv", []), ("argv", ["probe", "bad\0argument"]),
                                      ("argv", ["probe", 1])])
def test_exact_execution_must_match_outer_project_and_bounds(key, value):
    source = request()
    result = presentation.from_request(source, source["repo"])
    result["execution"][key] = value
    with pytest.raises(ValueError):
        presentation.validate(result, source["repo"])


def test_control_text_is_preserved_as_data_for_renderer_without_literal_loss():
    source = request(repo="C:\\fixture project")
    source["spec"].update(shell="powershell", argv=["C:\\Python\\python.exe", "literal\n[bold]\u001b[2J", "'quoted'"])
    result = presentation.from_request(source, source["repo"])
    assert result["execution"]["argv"] == source["spec"]["argv"]
    assert result["execution"]["shell"] == "powershell"


@pytest.mark.parametrize("kind", ["prediction", "probe_approval", "next_task"])
def test_large_source_history_is_optional_but_exact_actions_are_not_truncated(kind):
    source = request(kind)
    source["context"] = {"description": "x" * 20000, "source": {"files": {}, "skipped": {}},
                         "checkpoints": [{"note": "fixture", "changes": {"diff": "a" * 64000}}] * 3,
                         "observations": [{"status": "matched", "current_status": "not_verified",
                                           "actual": "x" * 64000}] * 4}
    result = presentation.from_request(source, source["repo"])
    assert len(result["details"]) <= presentation.MAX_DETAILS
    assert "excerpt truncated" in result["details"] and "not_verified" in result["details"]
    if kind == "next_task":
        assert result["options"] == source["options"]
        source["options"]["smaller"] = "x" * 17000
    else:
        assert result["execution"]["argv"] == source["spec"]["argv"]
        source["spec"]["argv"] = ["probe", "x" * 17000]
    with pytest.raises(ValueError):
        presentation.from_request(source, source["repo"])


@pytest.mark.parametrize("options", [{"smaller": "Inspect", "defer": "auto"}, {"larger": "Inspect", "defer": None},
                                    {"smaller": "Inspect", "defer": None, "run": "push"}])
def test_next_form_cannot_invent_an_unoffered_choice(options):
    source = request("next_task")
    source["options"] = options
    with pytest.raises(ValueError):
        presentation.from_request(source, source["repo"])


def test_t3_and_invalid_saved_prediction_cannot_be_presented_as_execution_consent():
    source = request("probe_approval")
    source["classification"]["name"] = "T3"
    with pytest.raises(ValueError):
        presentation.from_request(source, source["repo"])
    source["classification"]["name"] = "T2"
    source["prediction"]["value"] = float("nan")
    with pytest.raises(ValueError):
        presentation.from_request(source, source["repo"])


def test_ui_sends_metadata_with_unchanged_canonical_answer_reply():
    source = request()
    form = presentation.from_request(source, source["repo"])
    calls = []

    def exchange(message, **kwargs):
        calls.append(message)
        return {"answer": '{"value": 1, "reason": "Fixture"}'}

    answer = ui.ask("Predict", repo=source["repo"], presentation=form, exchange=exchange)
    assert set(calls[0]) == {"kind", "repo", "context", "prompt", "presentation"}
    assert calls[0]["presentation"] == form and calls[0]["presentation"] is not form
    assert answer == {"answer": '{"value": 1, "reason": "Fixture"}', "provenance": "human_ipc"}


def test_invalid_metadata_never_reaches_transport():
    source = request()
    form = presentation.from_request(source, source["repo"])
    form["execution"]["cwd"] = "/elsewhere"
    result = ui.ask("Predict", repo=source["repo"], presentation=form,
                    exchange=lambda *args, **kwargs: pytest.fail("invalid metadata must not send"))
    assert "error" in result and "answer" not in result


def test_stage_adapter_builds_form_without_changing_engine_reply(monkeypatch):
    source = request()
    captured = []

    def ask(prompt, **kwargs):
        captured.append(kwargs)
        return {"answer": '{"value": false, "reason": "Fixture reason"}', "provenance": "human_ipc"}

    monkeypatch.setattr(ui, "ask", ask)
    assert ui.answer_request(source) == {"value": False, "reason": "Fixture reason"}
    assert captured[0]["presentation"]["kind"] == "prediction"
    assert json.dumps(source["spec"]["argv"]) in captured[0]["context"]


def test_caller_pane_shows_concise_status_and_readable_factual_observation(capsys):
    source = request()
    ui.show_request(source)
    ui.show_request({"kind": "observation", "observation": {
        "status": "matched", "field": "answer", "actual": {"value": 1}, "reason": "Fixed fixture output matches."}})
    output = capsys.readouterr()
    assert output.out == ""
    assert "review pane" in output.err
    assert source["repo"] not in output.err and "Exact argv" not in output.err
    assert 'answer = {"value": 1}' in output.err
    assert r'{\"value\"' not in output.err


def test_observation_control_characters_are_never_terminal_instructions(capsys):
    ui.show_request({"kind": "observation", "observation": {
        "status": "not_verified", "field": "\x1b[2J", "actual": "\x1b[31m", "reason": "\x1b[0m"}})
    output = capsys.readouterr().err
    assert "\x1b" not in output and "\\u001b" in output
