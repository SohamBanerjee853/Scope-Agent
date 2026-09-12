"""Offline regressions using disposable Python children and synthetic output."""

import json
import os
import sys
import time

import pytest

from scope import runner


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path, monkeypatch):
    for name in ("SCOPE_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))
    for name in ("CODEX_THREAD_ID", "SCOPE_LAUNCH_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("descriptor", [1, 2], ids=["stdout", "stderr"])
@pytest.mark.parametrize("payload", [b'{"value":"\xff"}', b"\xe2\x82", b"\xc0\xaf"])
def test_invalid_utf8_cannot_be_a_successful_observation(tmp_path, descriptor, payload):
    result = runner.run(
        [sys.executable, "-c", f"import os; os.write({descriptor}, {payload!r})"],
        cwd=tmp_path,
    )
    assert result.exit_code == 0
    assert not result.stdout_truncated and not result.stderr_truncated
    assert "\ufffd" in (result.stdout if descriptor == 1 else result.stderr)
    assert result.error and "UTF-8" in result.error
    assert not result.succeeded
    # The damaged bytes can become parseable JSON; success must still be false.
    if descriptor == 1 and payload.startswith(b"{"):
        assert json.loads(result.stdout) == {"value": "\ufffd"}


@pytest.mark.parametrize("descriptor", [1, 2], ids=["stdout", "stderr"])
def test_genuine_unicode_replacement_character_remains_valid(tmp_path, descriptor):
    payload = '{"value":"\ufffd理解"}'.encode("utf-8")
    result = runner.run(
        [sys.executable, "-c", f"import os; os.write({descriptor}, {payload!r})"],
        cwd=tmp_path,
    )
    assert result.succeeded
    assert json.loads(result.stdout if descriptor == 1 else result.stderr) == {"value": "\ufffd理解"}


def test_forced_descendant_cleanup_cannot_be_a_successful_observation(tmp_path):
    child_code = """
from pathlib import Path
import time
Path('child-started').touch()
deadline = time.monotonic() + 5
while not Path('stop-child').exists() and time.monotonic() < deadline:
    time.sleep(0.01)
if not Path('stop-child').exists():
    print('late output invalidates the parent JSON', flush=True)
"""
    parent_code = """
from pathlib import Path
import subprocess, sys, time
subprocess.Popen([sys.executable, '-c', sys.argv[1]])
deadline = time.monotonic() + 2
while not Path('child-started').exists() and time.monotonic() < deadline:
    time.sleep(0.01)
assert Path('child-started').exists()
print('{"value":1}', flush=True)
"""
    started = time.monotonic()
    try:
        result = runner.run(
            [sys.executable, "-c", parent_code, child_code], cwd=tmp_path, timeout=10,
        )
        assert time.monotonic() - started < 7
        assert result.exit_code == 0 and not result.timed_out
        assert json.loads(result.stdout) == {"value": 1}
        assert result.error and "observation is incomplete" in result.error
        assert not result.succeeded
        if os.name != "nt":
            assert not result.cleanup_incomplete
    finally:
        # A lost parent can prevent native Windows tree cleanup. Release this
        # fixture's child explicitly; its own deadline is another bounded exit.
        (tmp_path / "stop-child").touch()


def test_environment_override_is_child_only_and_defaults_to_inheritance(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOPE_RUNNER_ENV_FIXTURE", "caller")
    monkeypatch.setenv("SCOPE_RUNNER_REMOVED_FIXTURE", "caller-only")
    argv = [sys.executable, "-c", "import json,os; print(json.dumps(["
            "os.environ.get('SCOPE_RUNNER_ENV_FIXTURE'),"
            "os.environ.get('SCOPE_RUNNER_REMOVED_FIXTURE')]))"]
    inherited = runner.run(argv, cwd=tmp_path)
    assert inherited.succeeded
    assert json.loads(inherited.stdout) == ["caller", "caller-only"]
    child_env = dict(os.environ)
    child_env["SCOPE_RUNNER_ENV_FIXTURE"] = "child"
    del child_env["SCOPE_RUNNER_REMOVED_FIXTURE"]
    expected_env = dict(child_env)
    overridden = runner.run(argv, cwd=tmp_path, env=child_env)
    assert overridden.succeeded
    assert json.loads(overridden.stdout) == ["child", None]
    assert os.environ["SCOPE_RUNNER_ENV_FIXTURE"] == "caller"
    assert os.environ["SCOPE_RUNNER_REMOVED_FIXTURE"] == "caller-only"
    assert child_env == expected_env
