"""Typed review data crosses the real authenticated question transport."""

import copy

import pytest

from scope import ipc, review_presentation
from scope.watch import Watcher


class FixtureUI:
    interactive = True

    def __init__(self):
        self.presentations = []

    def question(self, repo, context_text, prompt, context):
        self.presentations.append(context.presentation)
        return '{"value": 1, "reason": "Explicit test fixture"}'


def fixture_message(tmp_path):
    repo = str(tmp_path)
    request = {"kind": "prediction", "repo": repo, "task_id": "fixture-task",
               "spec": {"question": "What value will this fixture emit?", "field": "answer",
                        "argv": ["python", "probe.py"], "shell": "posix", "timeout": 5}}
    return {"kind": "question", "repo": repo, "context": "Fixture evidence",
            "prompt": "Save your prediction", "presentation": review_presentation.from_request(request, repo)}


def test_typed_question_and_legacy_question_share_the_existing_answer_contract(tmp_path):
    ui = FixtureUI()
    message = fixture_message(tmp_path)
    with Watcher(ui, human_timeout=2):
        assert ipc.exchange(message, timeout=2) == {"answer": '{"value": 1, "reason": "Explicit test fixture"}'}
        legacy = {key: value for key, value in message.items() if key != "presentation"}
        assert ipc.exchange(legacy, timeout=2) == {"answer": '{"value": 1, "reason": "Explicit test fixture"}'}
    assert ui.presentations == [message["presentation"], None]


@pytest.mark.parametrize("change", [
    lambda form: form.update(default_answer=True),
    lambda form: form.update(version=True),
    lambda form: form["execution"].update(cwd="/foreign-project"),
    lambda form: form.update(kind="allow"),
])
def test_invalid_presentation_is_refused_before_human_input(tmp_path, change):
    ui = FixtureUI()
    message = copy.deepcopy(fixture_message(tmp_path))
    change(message["presentation"])
    with Watcher(ui, human_timeout=2):
        response = ipc.exchange(message, timeout=2)
    assert response is not None and "error" in response
    assert ui.presentations == []
