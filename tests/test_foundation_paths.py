from pathlib import Path

import pytest

from scope import paths


def test_overrides_create_nothing(tmp_path, monkeypatch):
    root = tmp_path / "space and 'quote'" / "scope"
    monkeypatch.setenv("SCOPE_HOME", str(root))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    assert paths.scope_home() == root
    assert paths.codex_home() == tmp_path / "codex"
    assert paths.sessions_dir() == root / "sessions"
    assert paths.session_log("abc-123").parent == paths.sessions_dir()
    assert not root.exists()


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("SCOPE_HOME")
    monkeypatch.delenv("CODEX_HOME")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.scope_home() == tmp_path / ".scope"
    assert paths.codex_home() == tmp_path / ".codex"


@pytest.mark.parametrize("session", [
    "../escape", "/tmp/escape", r"..\escape", r"C:\temp\escape", "CON", "aux",
    "NUL.txt", "foo:bar", "abc.", "abc ", "a/b", "a\\b", "é", "\x00", "a" * 1000,
])
def test_untrusted_session_ids_stay_in_directory(session):
    result = paths.session_log(session)
    assert result.parent == paths.sessions_dir()
    assert result.suffix == ".jsonl"
    assert len(result.name) < 150
    assert not any(char in result.name for char in '/\\:\x00')
    assert result == paths.session_log(session)


def test_distinct_ids_do_not_alias():
    ids = ["a/b", "a\\b", "a_b", "ABC", "abc", "abc.", "abc ", "CON", "con"]
    assert len({paths.session_log(s).name.lower() for s in ids}) == len(ids)


@pytest.mark.parametrize("session", ["", None, 123])
def test_invalid_session_ids(session):
    with pytest.raises(ValueError):
        paths.session_log(session)
