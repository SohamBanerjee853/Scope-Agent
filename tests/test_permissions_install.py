"""Temporary-home installation tests; never install hooks into a real host."""

from importlib.resources import files
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shlex
import stat
import sysconfig

import pytest
import yaml

from scope import install
from scope.cli import main as scope_main
from scope.paths import codex_home


@pytest.fixture(autouse=True)
def isolated_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    monkeypatch.chdir(project)
    return project


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_entry_point_comes_from_current_environment_not_working_directory(tmp_path, monkeypatch):
    scripts = tmp_path / "installed environment" / "Scripts"
    scripts.mkdir(parents=True)
    executable = scripts / ("scope.exe" if os.name == "nt" else "scope")
    executable.write_bytes(b"fixture executable; never run")
    monkeypatch.setattr(sysconfig, "get_path", lambda key: str(scripts) if key == "scripts" else pytest.fail(key))
    assert install.console_script() == executable
    executable.unlink()
    with pytest.raises(ValueError, match="console script is missing"):
        install.console_script()


@pytest.mark.parametrize("shell, executable, expected", [
    ("posix", "/opt/Scope Agent's env/bin/scope", "'/opt/Scope Agent'\"'\"'s env/bin/scope' hook --abstain"),
    ("powershell", "C:\\Scope Agent's env\\Scripts\\scope.exe",
     "& 'C:\\Scope Agent''s env\\Scripts\\scope.exe' 'hook' '--abstain'"),
])
def test_shell_specific_paths_with_spaces_and_apostrophes(shell, executable, expected):
    command = install.hook_command(executable, "hook", shell=shell, abstain=True)
    assert command == expected
    if shell == "posix":
        assert shlex.split(command) == [executable, "hook", "--abstain"]


@pytest.mark.parametrize("native_path", [PurePosixPath, PureWindowsPath])
@pytest.mark.parametrize("shell", ["posix", "powershell"])
def test_hook_path_grammar_is_independent_of_native_os(native_path, shell, monkeypatch):
    monkeypatch.setattr(install, "Path", native_path)
    for executable in ("/opt/Scope env/scope", "C:\\Scope env\\scope.exe", "\\\\server\\share\\scope.exe"):
        command = install.hook_command(executable, "hook", shell=shell)
        if shell == "posix":
            assert shlex.split(command) == [executable, "hook"]
        else:
            assert command == f"& '{executable}' 'hook'"
    for executable in ("scope", "C:scope", "\\rooted\\scope.exe"):
        with pytest.raises(ValueError, match="absolute path"):
            install.hook_command(executable, "hook", shell=shell)


@pytest.mark.parametrize("shell", ["posix", "powershell"])
def test_shell_metacharacters_stay_inside_literal_executable(shell):
    executable = "/safe/a$(echo unexpected)`date`;name/scope"
    command = install.hook_command(executable, "session-end", shell=shell)
    if shell == "posix":
        assert shlex.split(command) == [executable, "session-end"]
    else:
        assert command == "& '/safe/a$(echo unexpected)`date`;name/scope' 'session-end'"


@pytest.mark.parametrize("path", ["scope", "./scope", "/tmp/scope\ncommand", "/tmp/\x00scope", "\\rooted", "C:scope"])
def test_ambiguous_or_control_character_executable_rejected(path):
    with pytest.raises(ValueError):
        install.hook_command(path, "hook")


def test_powershell_alternate_quote_path_does_not_escape_its_literal():
    with pytest.raises(ValueError, match="alternate quote"):
        install.hook_command("C:\\User’s Scope\\scope.exe", "hook", shell="powershell")


def test_config_has_only_expected_events_commands_matcher_and_deadlines():
    config = install.hook_config("/opt/scope")
    assert set(config) == {"hooks"}
    assert set(config["hooks"]) == {"PermissionRequest", "Stop", "SessionEnd"}
    for event, subcommand in (("PermissionRequest", "hook"), ("Stop", "stop"), ("SessionEnd", "session-end")):
        group = config["hooks"][event][0]
        handler = group["hooks"][0]
        assert handler["type"] == "command"
        assert shlex.split(handler["command"]) == ["/opt/scope", subcommand]
        assert handler["commandWindows"] == f"& '/opt/scope' '{subcommand}'"
        assert handler["timeout"] == (110 if event == "PermissionRequest" else 3)
        if event == "PermissionRequest":
            assert group["matcher"] == "^(Bash|PowerShell)$"
        else:
            assert "matcher" not in group


@pytest.mark.parametrize("project_mode", [False, True])
def test_dry_run_is_read_only_and_project_or_home_location(isolated_project, project_mode):
    before = sorted(Path.cwd().parent.rglob("*"))
    report = install.install_hooks(project=isolated_project if project_mode else None, dry_run=True)
    expected = ((isolated_project / ".codex") if project_mode else codex_home()) / "hooks.json"
    assert report["target"] == str(expected)
    assert report["changed"] is True and report["dry_run"] is True
    assert report["backup"] is None and not expected.exists()
    assert sorted(Path.cwd().parent.rglob("*")) == before


def test_existing_unrelated_hooks_preserved_with_exact_backup_and_idempotency():
    target = codex_home() / "hooks.json"
    existing = {
        "description": "My own hooks",
        "hooks": {
            "PermissionRequest": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "custom-review --strict", "timeout": 15}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "scope-report --summary"}]}],
            "PostToolUse": [{"matcher": "apply_patch", "hooks": [{"type": "mcp_tool", "server": "audit", "tool": "record"}]}],
        },
    }
    write_json(target, existing)
    original = target.read_bytes()
    first = install.install_hooks()
    assert Path(first["backup"]).read_bytes() == original
    merged = json.loads(target.read_bytes())
    assert merged["description"] == existing["description"]
    for event, groups in existing["hooks"].items():
        assert merged["hooks"][event][:len(groups)] == groups
    assert len(merged["hooks"]["PermissionRequest"]) == 2
    saved = target.read_bytes()
    backups = list(target.parent.glob("hooks.json.bak-*"))
    second = install.install_hooks()
    assert second["changed"] is False and second["backup"] is None
    assert target.read_bytes() == saved and list(target.parent.glob("hooks.json.bak-*")) == backups
    assert not list(target.parent.glob("*.scope-install.lock"))


def test_explicit_passive_update_replaces_only_owned_group_with_backup():
    first = install.install_hooks()
    target = Path(first["target"])
    original = target.read_bytes()
    passive = install.install_hooks(abstain=True)
    assert passive["changed"] is True
    assert Path(passive["backup"]).read_bytes() == original
    config = passive["config"]["hooks"]
    assert len(config["PermissionRequest"]) == 1
    for event, groups in config.items():
        for command in (groups[0]["hooks"][0]["command"], groups[0]["hooks"][0]["commandWindows"]):
            assert ("--abstain" in command) == (event == "PermissionRequest")
    assert install.install_hooks(abstain=True)["changed"] is False


@pytest.mark.parametrize("mutation", ["duplicate", "edited", "different_executable", "mixed_handler_group"])
def test_existing_ambiguous_scope_configuration_preserved(mutation):
    target = codex_home() / "hooks.json"
    config = install.hook_config()
    group = config["hooks"]["PermissionRequest"][0]
    if mutation == "duplicate":
        config["hooks"]["PermissionRequest"].append(group.copy())
    elif mutation == "edited":
        group["hooks"][0]["timeout"] = 1000
    elif mutation == "different_executable":
        group["hooks"][0]["command"] = "/old/environment/scope hook"
    else:
        group["hooks"].append({"type": "command", "command": "other-review"})
    write_json(target, config)
    original = target.read_bytes()
    with pytest.raises(ValueError, match="migration"):
        install.install_hooks()
    assert target.read_bytes() == original
    assert sorted(path.name for path in target.parent.iterdir()) == ["hooks.json"]


@pytest.mark.parametrize("source", ["user_json", "project_json", "user_toml", "project_toml"])
def test_other_visible_hook_layers_cannot_silently_duplicate_scope(isolated_project, source):
    project_mode = source.startswith("user")
    directory = codex_home() if project_mode else isolated_project / ".codex"
    directory.mkdir(parents=True)
    if source.endswith("json"):
        path = directory / "hooks.json"
        write_json(path, {"hooks": {"PermissionRequest": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "uv run scope hook"}]}]}})
    else:
        path = directory / "config.toml"
        path.write_text('[[hooks.PermissionRequest]]\nmatcher = "Bash"\n[[hooks.PermissionRequest.hooks]]\ntype = "command"\ncommand = "python -m scope.hook"\n', encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="already exist"):
        install.install_hooks(project=isolated_project if project_mode else None)
    assert path.read_bytes() == original
    target = (isolated_project / ".codex" if project_mode else codex_home()) / "hooks.json"
    assert not target.exists()


@pytest.mark.parametrize("data", [b"not json", b"[]", b'{"hooks":null}', b'{"hooks":{"Stop":{}}}',
                                 b'{"hooks":{"Stop":[{"hooks":"x"}]}}', b'{"hooks":{},"hooks":{}}',
                                 b'{"hooks":{},"number":NaN}', b'{"hooks":{},"number":1e999}', b"\xff"])
def test_malformed_existing_configuration_is_not_overwritten(data):
    target = codex_home() / "hooks.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    with pytest.raises((ValueError, UnicodeError)):
        install.install_hooks()
    assert target.read_bytes() == data


def test_oversize_existing_config_and_symlinks_refused(tmp_path):
    target = codex_home() / "hooks.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b" " * (install.MAX_CONFIG_BYTES + 1))
    with pytest.raises(ValueError, match="too large"):
        install.install_hooks()
    target.unlink()
    other = tmp_path / "outside.json"
    other.write_text("{}", encoding="utf-8")
    # Path.is_symlink is injected so this safety contract also runs on Windows
    # without requiring a machine's symlink privilege.
    original = Path.is_symlink
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "is_symlink", lambda path: path == target or original(path))
        with pytest.raises(ValueError, match="symlinked"):
            install.install_hooks()
    assert other.read_text(encoding="utf-8") == "{}"


def test_existing_installer_lock_refused_without_configuration_write():
    target = codex_home() / "hooks.json"
    target.parent.mkdir(parents=True)
    lock = target.parent / ".hooks.json.scope-install.lock"
    lock.write_text("fixture: an in-progress install", encoding="utf-8")
    with pytest.raises(ValueError, match="another installer"):
        install.install_hooks()
    assert not target.exists() and lock.exists()


def test_directory_configuration_is_refused_without_read_or_write():
    target = codex_home() / "hooks.json"
    target.mkdir(parents=True)
    with pytest.raises(ValueError, match="regular file"):
        install.install_hooks()
    assert target.is_dir() and not list(target.iterdir())


@pytest.mark.parametrize("kind", [stat.S_IFIFO, stat.S_IFSOCK])
def test_nonregular_configuration_is_rejected_before_open_on_every_os(tmp_path, monkeypatch, kind):
    target = tmp_path / "nonregular.fixture"
    target.write_bytes(b"fixture")
    original_lstat = Path.lstat
    original_open = os.open
    fields = list(target.lstat())
    fields[0] = kind | 0o600
    monkeypatch.setattr(Path, "lstat", lambda path: os.stat_result(fields) if path == target else original_lstat(path))

    def no_open(path, *args, **kwargs):
        if Path(path) == target:
            pytest.fail("a FIFO or socket must be refused before opening")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", no_open)
    with pytest.raises(ValueError, match="regular file"):
        install._read(target, 1024)


def test_changed_file_identity_is_rejected_before_read(tmp_path, monkeypatch):
    target = tmp_path / "raced.fixture"
    target.write_bytes(b"fixture")
    original = os.fstat

    def replaced_identity(descriptor):
        fields = list(original(descriptor))
        fields[1] += 1
        return os.stat_result(fields)

    monkeypatch.setattr(os, "fstat", replaced_identity)
    with pytest.raises(ValueError, match="changed while opening"):
        install._read(target, 1024)


@pytest.mark.parametrize("project_mode", [False, True])
def test_packaged_skill_install_dry_run_idempotency_and_preserved_local_edits(isolated_project, project_mode):
    project = isolated_project if project_mode else None
    expected = (isolated_project / ".agents" / "skills" if project_mode else codex_home() / "skills") / "scope-permissions" / "SKILL.md"
    dry = install.install_skill(project=project, dry_run=True)
    assert dry["changed"] and not expected.parent.exists()
    first = install.install_skill(project=project)
    assert first["target"] == str(expected) and first["changed"]
    resource = files("scope").joinpath("skills", "scope-permissions", "SKILL.md").read_bytes()
    assert expected.read_bytes() == resource
    frontmatter = yaml.safe_load(resource.decode("utf-8").split("---", 2)[1])
    assert frontmatter["name"] == "scope-permissions" and frontmatter["description"]
    assert install.install_skill(project=project)["changed"] is False
    local = resource + b"\nA user's local instruction.\n"
    expected.write_bytes(local)
    with pytest.raises(ValueError, match="local edits"):
        install.install_skill(project=project)
    assert expected.read_bytes() == local


def test_lazy_cli_routes_install_and_install_skill(capsys):
    assert scope_main(["install", "--dry-run", "--abstain"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["dry_run"] and "--abstain" in first["config"]["hooks"]["PermissionRequest"][0]["hooks"][0]["command"]
    assert scope_main(["install-skill", "--dry-run"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["target"].endswith(str(Path("scope-permissions") / "SKILL.md"))
    assert not codex_home().exists()


def test_always_allow_is_rejected_even_for_install_dry_run(capsys):
    with pytest.raises(SystemExit) as failure:
        install.main(["--always-allow", "--dry-run"])
    assert failure.value.code == 2
    assert "smoke-only" in capsys.readouterr().err
    assert not codex_home().exists()
