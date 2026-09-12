"""Source identity and citation regressions using disposable Git repositories."""

import os
import subprocess

import pytest

from scope import repository


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path, monkeypatch):
    for name in ("SCOPE_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))


def make_repo(path, filename):
    path.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
    (path / filename).write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", filename], check=True, env=env)
    return path.resolve()


@pytest.mark.parametrize("selector", ["environment", "config_environment"])
def test_requested_repository_is_not_overridden_by_inherited_git_settings(tmp_path, monkeypatch, selector):
    requested = make_repo(tmp_path / "requested", "requested.py")
    foreign = make_repo(tmp_path / "foreign", "foreign.py")
    if selector == "environment":
        monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    else:
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(foreign))
    prior = dict(os.environ)
    source = repository.snapshot(requested)
    assert source["root"] == str(requested)
    assert "requested.py" in source["files"]
    assert "foreign.py" not in source["files"]
    assert dict(os.environ) == prior  # Other threads/calling tools keep their environment.


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085", "\v", "\f"])
def test_citations_follow_physical_source_lines(tmp_path, separator):
    root = make_repo(tmp_path / "repo", "main.py")
    (root / "main.py").write_text(f"# first{separator}same line\nvalue = 1\n", encoding="utf-8")
    source = repository.snapshot(root)
    assert source["files"]["main.py"]["line_count"] == 2
    assert repository.validate_citation(source, "main.py:2")["start_line"] == 2
    with pytest.raises(ValueError):
        repository.validate_citation(source, "main.py:3")


@pytest.mark.parametrize("citation", [None, "other.py:1", "main.py:2", "main.py:1-2"])
def test_reference_text_must_match_its_line_and_path_fields(tmp_path, citation):
    root = make_repo(tmp_path / "repo", "main.py")
    (root / "main.py").write_text("first = 1\nsecond = 2\n", encoding="utf-8")
    source = repository.snapshot(root)
    reference = repository.validate_citation(source, "main.py:1")
    reference["citation"] = citation
    assert repository.evidence_status([reference], source)["status"] == "unverified"


@pytest.mark.parametrize("name", [".", "./", "", None])
def test_empty_or_dot_path_is_rejected_without_crashing(name):
    assert repository._path_reason(name) == "unsafe_path"


def test_diff_line_numbers_match_citations_with_unicode_separators(tmp_path):
    root = make_repo(tmp_path / "repo", "main.py")
    (root / "main.py").write_text("# first\u2028same line\nvalue = 1\n", encoding="utf-8")
    before = repository.snapshot(root)
    (root / "main.py").write_text("# first\u2028same line\nvalue = 2\n", encoding="utf-8")
    diff = repository.changes(before, repository.snapshot(root))["diff"]
    assert "@@ -1,2 +1,2 @@" in diff
