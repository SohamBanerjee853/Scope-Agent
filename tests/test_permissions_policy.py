"""Policy resource and fail-closed regressions, usable against an installed wheel."""

from importlib.resources import files
import json

import pytest
import yaml

from scope import tiers


@pytest.mark.parametrize("name", ["allow.yaml", "hard-ask.yaml"])
def test_bundled_rules_are_yaml_and_stdlib_readable(name):
    raw = files("scope").joinpath("rules", name).read_bytes()
    assert 0 < len(raw) < 65536
    assert yaml.safe_load(raw) == json.loads(raw)
    assert json.loads(raw)["version"] == 1


def test_unavailable_policy_cannot_allow(monkeypatch):
    def broken():
        raise OSError("synthetic unavailable policy")
    monkeypatch.setattr(tiers, "_rules", broken)
    assert tiers.classify("pytest -q", "/project", "posix").name == "T2"


@pytest.mark.parametrize("command", [None, 1, "", " ", "x" * (tiers.MAX_COMMAND + 1)])
def test_invalid_or_oversize_commands_cannot_allow(command):
    assert tiers.classify(command, "/project", "posix").name == "T2"


@pytest.mark.parametrize("shell", ["posix", "powershell"])
@pytest.mark.parametrize("command", ["rg --hidden token", "curl https://user:password@example.com"])
def test_hidden_search_and_embedded_credentials_require_hard_ask(shell, command):
    assert tiers.classify(command, "/project", shell).name == "T3"


@pytest.mark.parametrize("shell", ["posix", "powershell"])
def test_filenames_are_not_network_domains(shell):
    assert tiers.classify("cat README.md", "/project", shell).domains == ()
    assert tiers.classify("curl api.example.com", "/project", shell).domains == ("api.example.com",)
