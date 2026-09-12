"""A1 debugging primitives across real process restarts; no simulated human."""

import json
import subprocess
import sys
import textwrap

import pytest


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path, monkeypatch):
    for name in ("SCOPE_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.delenv("SCOPE_LAUNCH_ID", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "unrelated-parent-fixture")


def python_call(root, code, *args):
    result = subprocess.run([sys.executable, "-c", textwrap.dedent(code), *args],
                            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_debugging_primitives_recover_context_and_verify_a_retry_fix(tmp_path):
    root = tmp_path / "debug project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    buggy = textwrap.dedent('''\
        import json
        def charge_order(order_id):
            seen = set()
            charges = 0
            for attempt in range(2):
                key = f"{order_id}:{attempt}"
                if key not in seen:
                    seen.add(key)
                    charges += 1
            return charges
        if __name__ == "__main__":
            print(json.dumps({"charges": charge_order("fixture-order")}))
        ''')
    (root / "checkout.py").write_text(buggy, encoding="utf-8")
    (root / ".env").write_text("SYNTHETIC_PRIVATE_MARKER", encoding="utf-8")
    first = python_call(root, '''
        import json, sys
        from pathlib import Path
        from scope import learning, repository, runner, storage
        task = learning.start(Path.cwd(), "Find why a retry charges twice", session_id="synthetic-debug-journey")
        reference = repository.validate_citation(task["source"], "checkout.py:6")
        probe = runner.run([sys.executable, "checkout.py"], cwd=Path.cwd())
        assert probe.succeeded
        actual = json.loads(probe.stdout)["charges"]
        observation = {"status": "not_verified", "actual": actual, "references": [reference],
                       "provenance": "test_fixture", "reason": "Automated probe; no human prediction collected"}
        def record(state):
            state["tasks"][task["task_id"]]["observations"].append(observation)
            return state
        storage.update(Path.cwd(), record)
        print(json.dumps({"task_id": task["task_id"], "session": task["session_id"],
                          "actual": actual, "source": task["source"]}))
        ''')
    assert first["actual"] == 2
    assert first["session"] == "synthetic-debug-journey"
    assert "SYNTHETIC_PRIVATE_MARKER" not in json.dumps(first)
    assert ".env" in first["source"]["skipped"]

    # A fresh interpreter retrieves the recorded context, rather than relying on
    # the prior agent/process remembering its variables or conversation.
    recovered = python_call(root, '''
        import json, sys
        from pathlib import Path
        from scope import learning
        print(json.dumps(learning.knowledge(Path.cwd(), sys.argv[1])))
        ''', first["task_id"])
    assert recovered["observations"][0]["actual"] == 2
    assert recovered["observations"][0]["source_status"]["status"] == "current"

    (root / "checkout.py").write_text(buggy.replace('key = f"{order_id}:{attempt}"', "key = order_id"), encoding="utf-8")
    fixed = python_call(root, '''
        import json, sys
        from pathlib import Path
        from scope import learning, log, runner, understanding_summary
        task_id = sys.argv[1]
        checkpoint = learning.checkpoint(Path.cwd(), task_id, note="Use the same key for the same order retry")
        knowledge = learning.knowledge(Path.cwd(), task_id)
        probe = runner.run([sys.executable, "checkout.py"], cwd=Path.cwd())
        regression = runner.run([sys.executable, "-c", "from checkout import charge_order; assert charge_order('fixture-order') == 1"], cwd=Path.cwd())
        assert probe.succeeded and regression.succeeded
        summary = understanding_summary.summarize(log.read("synthetic-debug-journey"))
        print(json.dumps({"checkpoint": checkpoint, "knowledge": knowledge,
                          "actual": json.loads(probe.stdout)["charges"],
                          "regression_exit": regression.exit_code, "summary": summary,
                          "parent_events": log.read("unrelated-parent-fixture")}))
        ''', first["task_id"])
    assert fixed["actual"] == 1
    assert fixed["regression_exit"] == 0
    assert fixed["checkpoint"]["changes"]["modified"] == ["checkout.py"]
    assert "key = order_id" in fixed["checkpoint"]["changes"]["diff"]
    assert fixed["knowledge"]["observations"][0]["source_status"]["status"] == "stale"
    assert fixed["knowledge"]["observations"][0]["current_status"] == "not_verified"
    assert len(fixed["summary"]["tasks"]) == 1
    assert fixed["summary"]["predictions"] == []  # No invented person or consent workflow.
    assert fixed["parent_events"] == []
