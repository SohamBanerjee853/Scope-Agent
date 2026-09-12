import pytest
import hashlib


def pytest_make_parametrize_id(config, val, argname):
    # Pytest exports each node ID in PYTEST_CURRENT_TEST. Raw adversarial payloads
    # can exceed Windows' 32,767-character environment-value limit before the
    # test runs. Keep identities distinct without copying input into the label.
    if isinstance(val, (str, bytes)) and len(val) > 128:
        data = val.encode("utf-8", errors="surrogatepass") if isinstance(val, str) else val
        return f"{argname}-{len(data)}bytes-{hashlib.sha256(data).hexdigest()[:12]}"
    return None


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path, monkeypatch):
    for name in ("SCOPE_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    for name in ("SCOPE_LAUNCH_ID", "SCOPE_HOST", "CODEX_THREAD_ID"):
        monkeypatch.delenv(name, raising=False)
