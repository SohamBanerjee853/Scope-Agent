"""Disposable preparation and narrow probe; A2-dependent rehearsal stays pending."""

import json
from pathlib import Path
import sys

import pytest

from scope import demo, demo_agent, paths
from scope.cli import main


def test_prepare_only_is_isolated_from_parent_and_installs_project_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "real-parent")
    root = tmp_path / "disposable"
    result = demo.prepare(root)
    assert result["session_id"].startswith("demo-") and result["session_id"] != "real-parent"
    assert (root / ".git").is_dir()
    assert (root / ".agents/skills/scope-understand/SKILL.md").is_file()
    assert not paths.scope_home().exists()
    assert demo_agent.probe(root)["charge_count"] == 2


def test_fresh_demo_ids_do_not_repeat_when_parent_identity_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "real-parent")
    assert demo.prepare(tmp_path / "one")["session_id"] != demo.prepare(tmp_path / "two")["session_id"]


def test_dry_run_creates_nothing_and_starts_no_process(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("preview started a process")
    monkeypatch.setattr(demo.runner, "run", forbidden)
    assert demo.prepare(tmp_path / "preview", dry_run=True)["dry_run"] is True
    assert not (tmp_path / "preview").exists()


def test_nonempty_directory_and_repeat_preparation_preserve_files(tmp_path):
    root = tmp_path / "existing"
    root.mkdir()
    original = root / "important.txt"
    original.write_text("existing work")
    with pytest.raises(ValueError, match="new or empty"):
        demo.prepare(root)
    assert original.read_text() == "existing work"
    fresh = tmp_path / "fixture"
    demo.prepare(fresh)
    marker = (fresh / demo.MARKER).read_bytes()
    with pytest.raises(ValueError):
        demo.prepare(fresh)
    assert (fresh / demo.MARKER).read_bytes() == marker


def test_adapter_accepts_only_the_known_stable_key_repair(tmp_path):
    demo.prepare(tmp_path)
    source = tmp_path / "payment.py"
    source.write_text(source.read_text().replace(demo_agent.BUGGY_KEY, demo_agent.FIXED_KEY))
    observed = demo_agent.probe(tmp_path)
    assert observed["charge_count"] == 1 and observed["charged_cents"] == 100
    source.write_text("raise AssertionError('must never execute')\n")
    with pytest.raises(ValueError, match="only the bundled"):
        demo_agent.probe(tmp_path)


def test_adapter_isolates_python_from_project_module_injection(tmp_path):
    demo.prepare(tmp_path)
    (tmp_path / "json.py").write_text("raise AssertionError('project import must not execute')\n")
    assert demo_agent.probe(tmp_path)["charge_count"] == 2


@pytest.mark.parametrize("invalid", [[], {"schema_version": True, "fixture": "scope-payment-retry", "session_id": "demo-123"}])
def test_invalid_marker_never_launches_probe(tmp_path, monkeypatch, invalid):
    demo.prepare(tmp_path)
    (tmp_path / demo.MARKER).write_text(json.dumps(invalid))
    monkeypatch.setattr(demo_agent.runner, "run", lambda *a, **kw: pytest.fail("invalid marker launched"))
    with pytest.raises(ValueError, match="marker"):
        demo_agent.probe(tmp_path)


def test_scripted_flow_is_explicitly_pending_without_running_fixtures(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(demo.runner, "run", lambda *a, **kw: pytest.fail("pending A2 flow ran"))
    assert main(["demo", "--scripted", str(tmp_path / "never")]) == 2
    assert "awaits Arjun" in capsys.readouterr().err
    assert not (tmp_path / "never").exists()


def test_demo_adapter_cli_returns_actual_json(tmp_path, capsys):
    assert main(["demo", "--prepare-only", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["prepared"] is True
    assert main(["demo-adapter", "probe", "--cwd", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["charge_count"] == 2


def test_windows_reparse_destination_is_refused_without_starting_work(tmp_path, monkeypatch):
    from types import SimpleNamespace
    original = Path.lstat
    def attributes(path):
        if path == tmp_path:
            return SimpleNamespace(st_file_attributes=0x400, st_mode=0o40755)
        return original(path)
    monkeypatch.setattr(Path, "lstat", attributes)
    with pytest.raises(ValueError, match="symbolic links"):
        demo.prepare(tmp_path / "demo")
    assert not (tmp_path / "demo").exists()
