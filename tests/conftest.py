import pytest


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path, monkeypatch):
    for name in ("SCOPE_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
