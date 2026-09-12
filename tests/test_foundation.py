import importlib
import sys

import pytest

from scope import cli, log, paths


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path, monkeypatch):
    for name in ("SCOPE_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))


@pytest.mark.parametrize("session", ["../../outside", "..\\outside", "CON", "NUL", "a/b", "a_b", "理解"])
def test_session_paths_are_portable_and_contained(session):
    path = paths.session_log(session)
    assert path.parent == paths.sessions_dir()
    assert not path.parent.exists()
    assert path.stem.isalnum()


def test_session_paths_do_not_collide():
    assert paths.session_log("a/b") != paths.session_log("a_b")


@pytest.mark.parametrize("session", ["", " ", None, 3, "x" * 513])
def test_invalid_session(session):
    with pytest.raises(ValueError):
        paths.session_log(session)


def test_logs_utf8_and_incomplete_records():
    assert log.read("test") == []
    record = log.append("test", "task_start", description="理解", task_id="id")
    with paths.session_log("test").open("ab") as stream:
        stream.write(b'broken\n[]\n{"event":"fake"}\n{"ts":"x","event":"partial"}')
    assert log.read("test") == [record]


def test_event_validation():
    with pytest.raises(ValueError):
        log.append("test", "bad", ts="fabricated")
    with pytest.raises(ValueError):
        log.append("test", "bad", value=float("nan"))


def test_help_is_lazy(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert all(command in output for command in cli.ROUTES)
    assert "scope.watch" not in sys.modules


@pytest.mark.parametrize("command", sorted(cli.ROUTES))
def test_unimplemented_routes_fail_honestly(command, capsys, monkeypatch):
    def absent(name):
        raise ModuleNotFoundError(name=name)
    monkeypatch.setattr(importlib, "import_module", absent)
    assert cli.main([command]) == 2
    assert "not implemented" in capsys.readouterr().err
