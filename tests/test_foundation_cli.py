import subprocess
import sys
from types import SimpleNamespace

import pytest

from scope import cli


COMMANDS = (
    "hook stop session-end install watch propose install-skill receipt smoke "
    "start checkpoint check knowledge next revoke skill demo demo-adapter "
    "codex claude ready launch-agent host-hook"
).split()


def test_help_lists_frozen_commands_without_feature_imports():
    code = (
        "import sys; from scope.cli import main; "
        "main([]); "
        "assert not any(x in sys.modules for x in "
        "['rich', 'yaml', 'scope.hook', 'scope.learning_cli', 'scope.launch'])"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for command in COMMANDS:
        assert command in result.stdout


@pytest.mark.parametrize("command", COMMANDS)
def test_absent_commands_fail_clearly(command, capsys):
    assert cli.main([command]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert f"scope {command}: not implemented" in output.err


def test_help_and_unknown_command(capsys):
    with pytest.raises(SystemExit) as help_exit:
        cli.main(["--help"])
    assert help_exit.value.code == 0
    with pytest.raises(SystemExit) as unknown_exit:
        cli.main(["nonexistent"])
    assert unknown_exit.value.code == 2


@pytest.mark.parametrize("argv,module,forwarded", [
    (["hook", "--abstain"], "scope.hook", ["--abstain"]),
    (["start", "--help"], "scope.learning_cli", ["start", "--help"]),
    (["codex", "task"], "scope.launch", ["codex", "task"]),
    (["install-skill", "--dry-run"], "scope.install", ["install-skill", "--dry-run"]),
])
def test_dispatch_contract(argv, module, forwarded, monkeypatch):
    calls = []
    def importer(name):
        assert name == module
        return SimpleNamespace(main=lambda args: calls.append(args) or 7)
    monkeypatch.setattr(cli.importlib, "import_module", importer)
    assert cli.main(argv) == 7
    assert calls == [forwarded]


def test_broken_feature_dependency_is_not_hidden(monkeypatch):
    def importer(name):
        raise ModuleNotFoundError("missing dependency", name="other_dependency")
    monkeypatch.setattr(cli.importlib, "import_module", importer)
    with pytest.raises(ModuleNotFoundError, match="missing dependency"):
        cli.main(["hook"])
