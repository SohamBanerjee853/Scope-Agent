"""Install the manual Scope hooks or companion skill without changing host trust.

Hook discovery, commandWindows, timeouts, and duplicate decision resolution follow
https://learn.chatgpt.com/docs/hooks (checked September 12, 2026). Configuration
generation is not evidence that a native hook fired, including on Windows.
"""

import argparse
from contextlib import contextmanager
from copy import deepcopy
from importlib.resources import files
import json
import math
import ntpath
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import sysconfig
import tempfile
import tomllib
from uuid import uuid4

from .paths import codex_home


MAX_CONFIG_BYTES = 1024 * 1024
MAX_SKILL_BYTES = 128 * 1024
_SCOPE_COMMAND = re.compile(r"(?:^|[\s/\\'\"])scope(?:\.exe)?(?:[\s'\"]|$)", re.I)
_SCOPE_MODULE = re.compile(r"(?:^|\s)-m\s+scope(?:\.(?:hook|stop_hook|session_end_hook))?(?:\s|$)")
_TRUST_NOTE = (
    "Review and trust the exact hook definitions in Codex /hooks, then restart. "
    "All matching Bash hooks are combined: any deny wins; otherwise any allow "
    "permits the request. A Scope abstention cannot cancel another hook's allow. "
    "This checks visible user/project files, not every managed or plugin hook."
)


def console_script() -> Path:
    """Locate this Python environment's installed entry point, independent of cwd."""
    scripts = Path(sysconfig.get_path("scripts"))
    if not scripts.is_absolute():
        raise ValueError("Python environment scripts directory must be absolute")
    executable = scripts / ("scope.exe" if os.name == "nt" else "scope")
    if not executable.is_file():
        raise ValueError("scope console script is missing; install Scope in this Python environment first")
    return executable


def hook_command(executable: str | Path, subcommand: str, *, shell: str = "posix", abstain: bool = False) -> str:
    """Quote a fixed argv for POSIX or PowerShell without evaluating the path."""
    value = str(executable)
    drive, tail = ntpath.splitdrive(value)
    windows_absolute = bool(drive and ntpath.isabs(value))
    if drive.startswith("\\\\"):
        parts = drive[2:].replace("/", "\\").split("\\")
        windows_absolute = windows_absolute and len(parts) == 2 and all(parts)
    if (not value or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or not (Path(value).is_absolute() or windows_absolute)):
        raise ValueError("hook executable must be a literal absolute path")
    value.encode("utf-8", errors="strict")
    if subcommand not in {"hook", "stop", "session-end"}:
        raise ValueError("unsupported Scope hook command")
    argv = [value, subcommand]
    if abstain and subcommand == "hook":
        argv.append("--abstain")
    if shell == "posix":
        return shlex.join(argv)
    if shell == "powershell":
        if any(char in value for char in "‘’“”"):
            raise ValueError("PowerShell executable path contains unsupported alternate quote characters")
        return "& " + " ".join("'" + item.replace("'", "''") + "'" for item in argv)
    raise ValueError("shell must be posix or powershell")


def hook_config(executable: str | Path | None = None, *, abstain: bool = False) -> dict:
    """Generate documented native hook groups; never install smoke-only allow mode."""
    executable = console_script() if executable is None else executable
    groups = {}
    for event, command, timeout in (("PermissionRequest", "hook", 110),
                                     ("Stop", "stop", 3), ("SessionEnd", "session-end", 3)):
        group = {"hooks": [{
            "type": "command",
            "command": hook_command(executable, command, abstain=abstain),
            "commandWindows": hook_command(executable, command, shell="powershell", abstain=abstain),
            "timeout": timeout,
        }]}
        if event == "PermissionRequest":
            group["matcher"] = "^(Bash|PowerShell)$"
        groups[event] = [group]
    return {"hooks": groups}


def _absolute(path: str | Path) -> Path:
    return Path(path).expanduser().absolute()


def _read(path: Path, limit: int) -> bytes | None:
    if path.is_symlink():
        raise ValueError(f"refusing a symlinked configuration or skill: {path}")
    try:
        initial = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(initial.st_mode):
        raise ValueError(f"configuration or skill must be a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        current = path.lstat()
        identity = (initial.st_dev, initial.st_ino)
        if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(current.st_mode)
                or (opened.st_dev, opened.st_ino) != identity
                or (current.st_dev, current.st_ino) != identity):
            raise ValueError(f"configuration or skill changed while opening: {path}")
        if opened.st_size > limit:
            raise ValueError(f"existing file is too large: {path}")
        try:
            data = stream.read(limit + 1)
        except BlockingIOError as exc:
            raise ValueError(f"configuration or skill could not be read safely: {path}") from exc
    if len(data) > limit:
        raise ValueError(f"existing file is too large: {path}")
    return data


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON configuration key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("nonfinite JSON configuration number")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite JSON configuration number")
    return result


def _decode(data: bytes | None) -> dict:
    if data is None:
        return {}
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_object,
                       parse_constant=_constant, parse_float=_float)
    if not isinstance(value, dict) or not isinstance(value.get("hooks", {}), dict):
        raise ValueError("hooks.json must contain an object with an object-valued hooks field")
    for groups in value.get("hooks", {}).values():
        if not isinstance(groups, list):
            raise ValueError("hook events must contain matcher-group lists")
        for group in groups:
            if (not isinstance(group, dict) or not isinstance(group.get("hooks"), list)
                    or not all(isinstance(handler, dict) for handler in group["hooks"])):
                raise ValueError("malformed existing hook group")
    return value


def _scope_command(value) -> bool:
    return isinstance(value, str) and bool(_SCOPE_COMMAND.search(value) or _SCOPE_MODULE.search(value))


def _contains_scope(value) -> bool:
    if isinstance(value, dict):
        return any(_scope_command(item) if key in {"command", "commandWindows", "command_windows"}
                   else _contains_scope(item) for key, item in value.items())
    return isinstance(value, list) and any(map(_contains_scope, value))


def _config_layers(project: Path | None, target: Path) -> tuple[Path, ...]:
    candidates = {codex_home().absolute() / "hooks.json", codex_home().absolute() / "config.toml",
                  target.with_name("config.toml")}
    current = project if project is not None else Path.cwd()
    for folder in (current, *current.parents):
        candidates.update({folder / ".codex" / "hooks.json", folder / ".codex" / "config.toml"})
        if (folder / ".git").exists():
            break
    candidates.discard(target)
    return tuple(sorted(candidates))


def _check_other_layers(project: Path | None, target: Path) -> None:
    for path in _config_layers(project, target):
        data = _read(path, MAX_CONFIG_BYTES)
        if data is None:
            continue
        try:
            value = tomllib.loads(data.decode("utf-8")) if path.suffix == ".toml" else _decode(data)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"cannot inspect existing hook configuration: {path}") from exc
        if _contains_scope(value.get("hooks", {})):
            raise ValueError(f"Scope hooks already exist in {path}; review/migrate that source before installing another")


def _merge(existing: dict, desired: dict, executable: Path) -> dict:
    result = deepcopy(existing)
    events = result.setdefault("hooks", {})
    known = [hook_config(executable, abstain=mode)["hooks"] for mode in (False, True)]
    for event, groups in tuple(events.items()):
        found = [group for group in groups if _contains_scope(group)]
        if not found:
            continue
        if (event not in desired["hooks"] or len(found) != 1
                or not any(found[0] in configuration[event] for configuration in known)):
            raise ValueError("edited, older, or duplicate Scope hooks require explicit migration; existing hooks preserved")
        groups[groups.index(found[0])] = deepcopy(desired["hooks"][event][0])
    for event, groups in desired["hooks"].items():
        current = events.setdefault(event, [])
        if not any(_contains_scope(group) for group in current):
            current.extend(deepcopy(groups))
    return result


@contextmanager
def _write_lock(target: Path):
    if target.parent.is_symlink():
        raise ValueError(f"refusing a symlinked destination directory: {target.parent}")
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / ("." + target.name + ".scope-install.lock")
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError(f"another installer owns {lock}; inspect a stale lock before retrying") from exc
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink()


def _replace(target: Path, payload: bytes, previous: bytes | None) -> Path | None:
    """Preserve exact prior bytes and replace atomically in the same directory."""
    if _read(target, MAX_CONFIG_BYTES) != previous:
        raise ValueError("destination changed during installation; retry after reviewing it")
    backup = None
    if previous is not None:
        backup = target.with_name(target.name + ".bak-" + uuid4().hex)
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(previous)
            stream.flush()
            os.fsync(stream.fileno())
    descriptor, name = tempfile.mkstemp(prefix="." + target.name + ".", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return backup


def install_hooks(*, project: str | Path | None = None, dry_run: bool = False, abstain: bool = False) -> dict:
    project_path = _absolute(project) if project is not None else None
    if project_path is not None and not project_path.is_dir():
        raise ValueError("--project must name an existing project directory")
    target = (project_path / ".codex" if project_path is not None else _absolute(codex_home())) / "hooks.json"
    executable = console_script()
    desired = hook_config(executable, abstain=abstain)

    def prepare():
        _check_other_layers(project_path, target)
        previous = _read(target, MAX_CONFIG_BYTES)
        current = _decode(previous)
        merged = _merge(current, desired, executable)
        report = {"target": str(target), "changed": current != merged, "dry_run": dry_run,
                  "backup": None, "config": merged, "notice": _TRUST_NOTE}
        return previous, report

    previous, report = prepare()
    if dry_run or not report["changed"]:
        return report
    with _write_lock(target):
        previous, report = prepare()
        if report["changed"]:
            payload = (json.dumps(report["config"], indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            if len(payload) > MAX_CONFIG_BYTES:
                raise ValueError("merged hook configuration is too large")
            backup = _replace(target, payload, previous)
            report["backup"] = str(backup) if backup else None
        return report


def install_skill(*, project: str | Path | None = None, dry_run: bool = False) -> dict:
    project_path = _absolute(project) if project is not None else None
    if project_path is not None and not project_path.is_dir():
        raise ValueError("--project must name an existing project directory")
    directory = project_path / ".agents" / "skills" if project_path is not None else _absolute(codex_home()) / "skills"
    target = directory / "scope-permissions" / "SKILL.md"
    payload = files("scope").joinpath("skills", "scope-permissions", "SKILL.md").read_bytes()
    if len(payload) > MAX_SKILL_BYTES:
        raise ValueError("packaged permission skill is too large")

    def prepare():
        previous = _read(target, MAX_SKILL_BYTES)
        if previous is not None and previous != payload:
            raise ValueError(f"existing skill has local edits; preserved {target}")
        return {"target": str(target), "changed": previous is None, "dry_run": dry_run,
                "notice": "Companion instructions do not enforce permission boundaries. Check discovery with Codex /skills."}

    report = prepare()
    if dry_run or not report["changed"]:
        return report
    with _write_lock(target):
        report = prepare()
        if report["changed"]:
            _replace(target, payload, None)
        return report


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    skill = bool(argv and argv[0] == "install-skill")
    if skill:
        argv.pop(0)
    parser = argparse.ArgumentParser(prog="scope install-skill" if skill else "scope install", allow_abbrev=False,
                                     description="Install the optional manual permission skill." if skill else __doc__)
    parser.add_argument("--project", type=Path, help="existing project directory; default is CODEX_HOME")
    parser.add_argument("--dry-run", action="store_true", help="review the proposed configuration without writing files")
    if not skill:
        parser.add_argument("--abstain", action="store_true", help="install a passive permission hook")
        parser.add_argument("--always-allow", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not skill and args.always_allow:
        parser.error("--always-allow is smoke-only and cannot be installed persistently")
    try:
        report = (install_skill(project=args.project, dry_run=args.dry_run) if skill
                  else install_hooks(project=args.project, dry_run=args.dry_run, abstain=args.abstain))
        print(json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"scope {'install-skill' if skill else 'install'}: {exc}", file=sys.stderr)
        return 1
