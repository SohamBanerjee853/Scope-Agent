"""Invocation-local host launchers; panes and configuration do not prove hook interception."""

import argparse
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
import uuid

from . import install, paths

MAX_RUN_FILE = 1024 * 1024
STARTUP_TIMEOUT = 30
_PARENT_MARKERS = frozenset({"CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_TASK_ID", "CLAUDECODE",
                             "CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT"})
_EVENTS = ("SessionStart", "PermissionRequest", "Stop", "SessionEnd")


def _text(value, maximum=32768):
    if not isinstance(value, str) or not value or len(value) > maximum or not value.isprintable():
        raise ValueError("launcher arguments must be bounded literal text without control characters")
    value.encode("utf-8")
    return value


def _quote(argv, *, windows=False):
    argv = [_text(str(item)) for item in argv]
    if windows:
        if any(any(char in item for char in "‘’“”") for item in argv):
            raise ValueError("PowerShell argument contains unsupported alternate quotes")
        return "& " + " ".join("'" + item.replace("'", "''") + "'" for item in argv)
    return shlex.join(argv)


def _literal_display(value):
    """Keep printable path text exact; make terminal controls visible."""
    return "".join(char if char.isprintable() else json.dumps(char, ensure_ascii=True)[1:-1]
                   for char in str(value))


def hook_groups(host, executable, *, permissions=True, windows=None):
    """Only Scope additions; both hosts natively merge other hook sources."""
    if host not in {"codex", "claude"} or not Path(executable).is_absolute():
        raise ValueError("hooks require a supported host and absolute installed Scope path")
    windows = os.name == "nt" if windows is None else windows
    command = [str(executable), "host-hook", "--host", host]
    groups = {}
    for event in _EVENTS:
        if event == "PermissionRequest" and not permissions:
            continue
        entry = {"type": "command", "command": _quote(command), "timeout": 110 if event == "PermissionRequest" else 5}
        if host == "codex":
            entry["commandWindows"] = _quote(command, windows=True)
            if event == "SessionStart":
                entry["additionalContextLimit"] = 20000
        else:
            entry.update(command=_quote(command, windows=windows), shell="powershell" if windows else "bash")
        group = {"hooks": [entry]}
        if event == "PermissionRequest":
            group["matcher"] = "^(Bash|PowerShell)$"
        groups[event] = [group]
    return groups


def _toml(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(json.dumps(key) + " = " + _toml(item) for key, item in value.items()) + "}"
    raise ValueError("unsupported invocation configuration value")


def _visible_settings(host, cwd):
    if host == "codex":
        candidates = {paths.codex_home().absolute() / "config.toml", paths.codex_home().absolute() / "hooks.json"}
        directory, names = ".codex", ("config.toml", "hooks.json")
    else:
        user = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser().absolute()
        candidates = {user / "settings.json", user / "settings.local.json"}
        directory, names = ".claude", ("settings.json", "settings.local.json")
    for folder in (cwd, *cwd.parents):
        candidates.update(folder / directory / name for name in names)
        if (folder / ".git").exists():
            break
    return sorted(candidates)


def check_existing_hooks(host, cwd):
    """Inspect bounded visible settings; never remove or rewrite user settings."""
    for path in _visible_settings(host, cwd):
        data = install._read(path, install.MAX_CONFIG_BYTES)
        if data is None:
            continue
        try:
            value = tomllib.loads(data.decode("utf-8")) if path.suffix == ".toml" else install._decode(data)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("cannot safely inspect existing host hook configuration") from exc
        if install._contains_scope(value.get("hooks", {})):
            raise ValueError("existing Scope hooks require migration before launch; visible user/project settings were preserved")


def child_environment(home, launch_id, host, *, source=None):
    """Keep authentication and host restrictions; clear inherited session identity."""
    environment = dict(os.environ if source is None else source)
    for name in tuple(environment):
        if name in _PARENT_MARKERS or name.startswith("SCOPE_"):
            environment.pop(name, None)
    environment.update(SCOPE_HOME=str(home), SCOPE_LAUNCH_ID=launch_id, SCOPE_HOST=host, SCOPE_POPUP="0")
    return environment


def _read_private(path, maximum=MAX_RUN_FILE):
    from .ipc import _private_open, _json_object
    descriptor = _private_open(Path(path))
    with os.fdopen(descriptor, "rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("private launch record exceeds its bound")
    return _json_object(data)


def _write_private(path, value):
    from .ipc import _private_open
    data = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8") + b"\n"
    if len(data) > MAX_RUN_FILE:
        raise ValueError("private launch record exceeds its bound")
    path = Path(path)
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    descriptor = _private_open(temporary, create=True, exclusive=True, writable=True)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def owner_started(home):
    from .host_session import read_launch
    launch = read_launch(home)
    try:
        marker = _read_private(Path(home) / "owner.started", 4096)
    except FileNotFoundError:
        return False
    if marker != {"launch_id": launch["launch_id"]}:
        raise ValueError("invalid owner startup marker")
    return True


@contextmanager
def owner_lock(home):
    """A persistent sidecar OS lock, released by the kernel if its owner exits."""
    from . import host_session, storage
    from .ipc import _private_open
    launch = host_session.read_launch(home)
    if owner_started(home):
        raise ValueError("this launch already had an owner; create a fresh launch instead of reusing it")
    descriptor = _private_open(Path(home) / "owner.lock", create=True, writable=True)
    with os.fdopen(descriptor, "r+b", buffering=0) as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"\0")
        try:
            storage._try_lock(stream)
        except OSError as exc:
            raise ValueError("another process owns this launch") from exc
        try:
            _write_private(Path(home) / "owner.started", {"launch_id": launch["launch_id"]})
            yield
        finally:
            storage._unlock(stream)


def owner_alive(home):
    """Check a validated lock without creating files or treating a PID as identity."""
    from . import host_session, storage
    from .ipc import _private_open
    host_session.read_launch(home)
    try:
        descriptor = _private_open(Path(home) / "owner.lock", writable=True)
    except FileNotFoundError:
        return False
    with os.fdopen(descriptor, "r+b", buffering=0) as stream:
        try:
            storage._try_lock(stream)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return True
            raise
        storage._unlock(stream)
        return False


def preflight(host, *, cwd=None, task=None, model=None, permissions=True, understanding=True,
              manual_watch=False, dry_run=False):
    if host not in {"codex", "claude"} or not permissions and not understanding:
        raise ValueError("select a supported host and at least one workflow")
    project = Path(cwd or Path.cwd()).expanduser().resolve()
    if not project.is_dir():
        raise ValueError("--cwd must name an existing directory")
    if task is not None:
        _text(task)
    if model is not None:
        _text(model, 200)
    _text(str(project))
    if understanding and not any((parent / ".git").exists() for parent in (project, *project.parents)):
        raise ValueError("understanding requires a Git project; use --permissions-only for another directory")
    executable = install.console_script()
    check_existing_hooks(host, project)
    host_path = shutil.which(host)
    tmux = None if manual_watch else shutil.which("tmux")
    if not dry_run:
        if host_path is None or Path(host_path).suffix.lower() in {".cmd", ".bat"}:
            raise ValueError("the selected host needs a native executable on PATH; install it before launch")
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError("host launch requires an interactive terminal; --dry-run previews without launching")
        if not manual_watch and tmux is None:
            raise ValueError("automatic panes require tmux; install it or open a second terminal with --manual-watch")
        if understanding:
            from .repository import find_root
            find_root(project)
    return {"host": host, "cwd": str(project), "scope_executable": str(executable),
            "host_executable": str(Path(host_path).absolute()) if host_path else None,
            "tmux_executable": str(Path(tmux).absolute()) if tmux else None,
            "permissions": bool(permissions), "understanding": bool(understanding),
            "manual_watch": bool(manual_watch), "task": task, "model": model,
            "dry_run": bool(dry_run), "performed": False,
            "hook_additions": hook_groups(host, executable, permissions=permissions),
            "meaning": "Invocation-only configuration. Startup registration, reachable review and observed permission requests are separate checks; no host/model has started.",
            "limits": "Visible user/project hook inspection is not an exhaustive managed/plugin audit. Host sandbox, approvals, network restrictions and hook trust remain in force."}


def _guidance(launch):
    from .host_hook import guidance
    return guidance(launch)


def host_argv(plan, home):
    argv = [plan["host_executable"]]
    if plan["host"] == "codex":
        argv.extend(["-C", plan["cwd"], "--add-dir", str(home)])
        for event, groups in plan["hook_additions"].items():
            argv.extend(["-c", "hooks." + event + "=" + _toml(groups)])
        if plan["model"]:
            argv.extend(["-m", plan["model"]])
    else:
        argv.extend(["--settings", str(Path(home) / "claude-settings.json"), "--add-dir", str(home),
                     "--append-system-prompt", _guidance(plan)])
        if plan["model"]:
            argv.extend(["--model", plan["model"]])
    if plan["task"] is not None:
        argv.extend(["--", plan["task"]])
    return argv


def prepare_launch(plan):
    from . import host_session
    parent_ids = [os.environ[name] for name in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CLAUDE_SESSION_ID",
                                               "CLAUDE_CODE_SESSION_ID") if os.environ.get(name)]
    # A launched Claude host need not expose its native ID as an environment
    # variable. Consult validated current state before replacing launch context.
    # Partial or corrupt prior launch markers fail here before any child writes.
    if host_session.current_launch() is not None:
        parent_session = host_session.status().get("session_id")
        if parent_session is not None:
            parent_ids.append(parent_session)
    base = paths.scope_home().expanduser().absolute()
    identifier = "launch-" + uuid.uuid4().hex
    # Existing parent directories are checked before any private run is created.
    install._check_destination_directories(base, base / "runs" / identifier / "launch.json")
    # Canonicalize allowed platform aliases above the explicitly selected home;
    # the selected home and its descendants were checked for redirection above.
    base = base.resolve()
    home = base / "runs" / identifier
    home.mkdir(parents=True, mode=0o700)
    launch = host_session.create_launch(home, launch_id=identifier, host=plan["host"], cwd=plan["cwd"],
                                         permissions=plan["permissions"], understanding=plan["understanding"],
                                         parent_session_ids=parent_ids)
    if plan["host"] == "claude":
        _write_private(home / "claude-settings.json", {"hooks": plan["hook_additions"]})
    environment = child_environment(home, identifier, plan["host"])
    environment["PATH"] = str(Path(plan["scope_executable"]).parent) + os.pathsep + environment.get("PATH", "")
    _write_private(home / "environment.json", environment)
    record = {"schema_version": 1, "launch_id": identifier, "cwd": plan["cwd"], "host": plan["host"],
              "scope_executable": plan["scope_executable"], "argv": host_argv(plan, home),
              "manual_watch": plan["manual_watch"], "tmux_executable": plan["tmux_executable"]}
    _write_private(home / "launcher.json", record)
    return home, launch


def _load_launcher(home):
    from .host_session import read_launch
    launch = read_launch(home)
    record = _read_private(Path(home) / "launcher.json")
    if (record.get("schema_version") != 1 or record.get("launch_id") != launch["launch_id"]
            or record.get("host") != launch["host"] or record.get("cwd") != launch["cwd"]
            or not isinstance(record.get("argv"), list) or not 1 <= len(record["argv"]) <= 64
            or not all(isinstance(arg, str) and "\0" not in arg for arg in record["argv"])
            or not Path(record["argv"][0]).is_absolute()):
        raise ValueError("invalid private launcher record")
    return launch, record


def _load_environment(home, launch):
    environment = _read_private(Path(home) / "environment.json")
    if (not all(isinstance(key, str) and key and "=" not in key and "\0" not in key
                and isinstance(value, str) and "\0" not in value for key, value in environment.items())
            or any(environment.get(key) != expected for key, expected in
                   {"SCOPE_HOME": str(home), "SCOPE_LAUNCH_ID": launch["launch_id"],
                    "SCOPE_HOST": launch["host"], "SCOPE_POPUP": "0"}.items())
            or any(key in environment for key in _PARENT_MARKERS)):
        raise ValueError("invalid private launcher environment")
    return environment


@contextmanager
def _environment(environment):
    previous = os.environ.copy()
    os.environ.clear()
    os.environ.update(environment)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def readiness():
    from . import host_session, ipc, log
    state = host_session.status()
    review = ipc.exchange({"kind": "ping"}, timeout=2)
    state["review_reachable"] = review == {"ready": True}
    state["ready"] = state.get("status") == "open" and state["review_reachable"]
    if not state["review_reachable"]:
        state["action"] = "Restore this launch's review pane before dependent work; no answer or permission is inferred."
    elif state.get("status") != "open":
        state["action"] = "Wait for the real native SessionStart. In Codex, review /hooks trust, restart if needed, and submit the first task."
    else:
        host_session.require_session(state["session_id"])
        log.append(state["session_id"], "client_ready", host=state["host"], launch_id=state["launch_id"],
                   session=state["session_id"], permission_status=state["permission_status"],
                   source="native_state_and_review_ping", review_reachable=True)
        state["action"] = "Review is reachable. Configured/waiting permission status remains unobserved until a real request reaches Scope."
    return state


def _wait_review(home, *, timeout=STARTUP_TIMEOUT):
    from .ipc import exchange
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if exchange({"kind": "ping"}, timeout=min(1, max(0.05, deadline - time.monotonic())), home=home) == {"ready": True}:
            return
        time.sleep(0.05)
    raise ValueError("review did not become reachable; restore the review pane and start a fresh launch")


def _wait_review_closed(home, *, timeout=2):
    """A shutdown reply precedes server cleanup; wait before removing its pane."""
    from . import ipc
    deadline = time.monotonic() + timeout
    while True:
        try:
            try:
                (Path(home) / ipc.ENDPOINT_NAME).lstat()
                endpoint_removed = False
            except FileNotFoundError:
                endpoint_removed = True
            if endpoint_removed:
                try:
                    descriptor = ipc._private_open(Path(home) / ipc.LOCK_NAME, writable=True)
                except FileNotFoundError:
                    return True
                try:
                    ipc._lock(descriptor)
                    ipc._unlock(descriptor)
                    return True
                except ipc.AlreadyRunningError:
                    pass
                finally:
                    os.close(descriptor)
        except OSError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))


def _finalize(home, launch, code, *, host_started):
    from . import host_session, ipc, log, receipt
    unavailable = []
    for operation in ("revoke", "shutdown"):
        try:
            ipc.exchange({"kind": operation}, timeout=2, home=home)
        except Exception:
            unavailable.append("review cleanup")
    if not _wait_review_closed(home):
        unavailable.append("review shutdown completion")
    state = {}
    native_state_available = False
    try:
        state = host_session.status()
        native_state_available = True
    except Exception:
        unavailable.append("native session state")
    session = state.get("session_id")
    if state.get("status") != "closed":
        try:
            host_session.close_session(session, host=launch["host"], cwd=launch["cwd"], inferred=True,
                                       reason="launcher observed host process exit" if host_started else "launcher ended before starting a host")
        except Exception:
            unavailable.append("native closure record")
    receipt_available = False
    if session:
        try:
            log.append(session, "agent_process", host=launch["host"], exit_code=code,
                       source="launcher", host_started=host_started,
                       execution="host process exited; individual tool execution remains unknown" if host_started else "host was not started")
        except Exception:
            unavailable.append("process event")
        try:
            receipt.write(session, home=home, host=launch["host"])
            receipt_available = True
        except Exception:
            unavailable.append("receipt")
    result = {"launch_id": launch["launch_id"], "host": launch["host"], "session_id": session,
              "exit_code": code, "home": str(home), "host_started": host_started,
              "closure_source": "launcher_process_exit" if host_started else "launcher_startup_failure",
              "receipt_available": receipt_available, "evidence_unavailable": sorted(set(unavailable)),
              "native_state_available": native_state_available, "exit_record_written": True}
    try:
        _write_private(Path(home) / "exit.json", result)
    except Exception:
        # The owner still knows the actual child status even when storage cannot
        # preserve it for a separate outer process. Never manufacture a receipt.
        result["exit_record_written"] = False
        result["evidence_unavailable"].append("exit record")
    if result["evidence_unavailable"]:
        print("Scope exit evidence unavailable: " + ", ".join(result["evidence_unavailable"]) +
              ". The observed host exit status is preserved; inspect this run's records.", file=sys.stderr)
    return result


def _print_exit(result, executable, *, windows=None):
    windows = os.name == "nt" if windows is None else windows
    print("Scope host exit: " + str(result["exit_code"]))
    if result.get("session_id") and result.get("receipt_available"):
        try:
            command = _quote([executable, "receipt", result["session_id"], "--home", result["home"]], windows=windows)
        except ValueError:
            print("Receipt saved; a copyable replay command is unavailable for this path or identity.")
            print("Run records: " + _literal_display(result["home"]))
        else:
            # Shell quoting already preserves literal argv. JSON-escaping this
            # string again would change Windows backslashes and quoted paths.
            print("Receipt: " + command)
    elif not result.get("session_id") and result.get("native_state_available"):
        print("No native SessionStart was registered; no session receipt or permission interception is claimed.")
    elif not result.get("session_id"):
        print("Native startup evidence unavailable; no session receipt is claimed.")
    else:
        print("Receipt unavailable. Run records: " + _literal_display(result["home"]))


def _wait_host(process, home):
    from . import host_session, ipc, log
    next_review_check = time.monotonic() + 2
    review_notice = False
    while True:
        try:
            return process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            if review_notice or time.monotonic() < next_review_check:
                continue
            next_review_check = time.monotonic() + 2
            if ipc.exchange({"kind": "ping"}, timeout=0.3, home=home) == {"ready": True}:
                continue
            state = host_session.status()
            if state.get("status") == "closed":
                continue
            review_notice = True
            print("Scope review is unavailable. Human questions and reviewed grants cannot be supplied; "
                  "the host's native approval fallback remains in force. Restore review before dependent work.",
                  file=sys.stderr, flush=True)
            if state.get("session_id"):
                log.append(state["session_id"], "review_unavailable", host=state["host"],
                           launch_id=state["launch_id"], source="launcher_transport_check")


def run_agent(home, *, watch=False):
    home = Path(home).expanduser().absolute()
    launch, record = _load_launcher(home)
    environment = _load_environment(home, launch)
    with _environment(environment):
        if watch:
            from .watch import main as watch_main
            return watch_main(["--owner-home", str(home)])
        with owner_lock(home):
            code = 1
            process = None
            try:
                _wait_review(home)
                if not record["manual_watch"]:
                    deadline = time.monotonic() + 5
                    while not (home / "panes.json").exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    _read_private(home / "panes.json", 4096)
                # The review process has already loaded its environment by the
                # time ping succeeds, so no pane still needs this secret file.
                (home / "environment.json").unlink(missing_ok=True)
                process = subprocess.Popen(record["argv"], cwd=record["cwd"], env=environment)
                returncode = _wait_host(process, home)
                code = returncode if returncode >= 0 else 128 - returncode
            except KeyboardInterrupt:
                code = 130
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            except (OSError, ValueError):
                print("scope launch: host startup failed; inspect review readiness and host installation", file=sys.stderr)
            finally:
                result = _finalize(home, launch, code, host_started=process is not None)
                try:
                    (home / "environment.json").unlink(missing_ok=True)
                except OSError:
                    print("Scope private environment cleanup is incomplete; inspect this run's private files.", file=sys.stderr)
                _print_exit(result, record["scope_executable"])
                if not record["manual_watch"]:
                    _close_window(home, record)
            return code


def _tmux(arguments, *, executable, server=None, capture=True):
    argv = [executable]
    if server:
        argv.extend(["-L", server, "-f", os.devnull])
    argv.extend(arguments)
    result = subprocess.run(argv, capture_output=capture, text=True, encoding="utf-8", check=False)
    if result.returncode != 0:
        raise ValueError("tmux could not prepare or select the Scope panes; no environment values were logged")
    return result.stdout.strip() if capture else ""


def _close_window(home, record):
    try:
        panes = _read_private(Path(home) / "panes.json", 4096)
        if (panes.get("launch_id") != record["launch_id"] or not re.fullmatch(r"@\d+", panes.get("window", ""))
                or panes.get("server") is not None and panes["server"] != record["launch_id"]):
            return
        _tmux(["kill-window", "-t", panes["window"]], executable=record["tmux_executable"], server=panes["server"])
    except (OSError, ValueError):
        pass


def open_panes(home, plan):
    from .host_session import read_launch
    launch = read_launch(home)
    server = None if os.environ.get("TMUX") else launch["launch_id"]
    executable = plan["tmux_executable"]
    command = "exec " + _quote([plan["scope_executable"], "launch-agent", "--home", str(home)])
    watch_command = command + " --watch"
    window = None
    if server:
        output = _tmux(["new-session", "-d", "-s", server, "-n", "Scope", "-P", "-F", "#{window_id}\t#{pane_id}",
                        "-c", plan["cwd"], command], executable=executable, server=server)
    else:
        current_session = _tmux(["display-message", "-p", "#{session_id}"], executable=executable)
        if not re.fullmatch(r"\$\d+", current_session):
            raise ValueError("tmux did not identify the current session")
        output = _tmux(["new-window", "-d", "-t", current_session, "-n", "Scope", "-P", "-F", "#{window_id}\t#{pane_id}",
                        "-c", plan["cwd"], command], executable=executable)
    try:
        window, left = output.split("\t")
        if not re.fullmatch(r"@\d+", window) or not re.fullmatch(r"%\d+", left):
            raise ValueError("tmux did not return actual window and pane identities")
        right = _tmux(["split-window", "-h", "-t", left, "-P", "-F", "#{pane_id}", "-c", plan["cwd"], watch_command],
                      executable=executable, server=server)
        if not re.fullmatch(r"%\d+", right) or right == left:
            raise ValueError("tmux did not return a distinct review pane")
        panes = {"launch_id": launch["launch_id"], "server": server, "window": window, "agent_pane": left, "review_pane": right}
        _write_private(Path(home) / "panes.json", panes)
        _tmux(["select-pane", "-t", left], executable=executable, server=server)
        return panes
    except (OSError, ValueError):
        if server:
            try:
                _tmux(["kill-server"], executable=executable, server=server)
            except (OSError, ValueError):
                pass
        elif isinstance(window, str) and re.fullmatch(r"@\d+", window):
            try:
                _tmux(["kill-window", "-t", window], executable=executable)
            except (OSError, ValueError):
                pass
        raise


def start(plan):
    home, launch = prepare_launch(plan)
    print("Scope run: " + _literal_display(home), flush=True)
    if plan["host"] == "codex":
        print("Codex first use: review /hooks, trust the exact definitions, then restart if required. Native SessionStart occurs at the first task.", flush=True)
    if plan["manual_watch"]:
        print("In your review terminal run: " + _quote([plan["scope_executable"], "watch", "--owner-home", str(home)],
                                                      windows=os.name == "nt"), flush=True)
        return run_agent(home)
    try:
        panes = open_panes(home, plan)
    except (OSError, ValueError):
        (home / "environment.json").unlink(missing_ok=True)
        from .host_session import close_session
        close_session(home=home, inferred=True, reason="launcher pane setup failed; inspect any startup evidence")
        raise
    print("Agent left; review right. Ctrl-b then arrow switches panes; Ctrl-b d detaches without stopping the launch.", flush=True)
    if panes["server"]:
        print("Reattach: " + _quote([plan["tmux_executable"], "-L", panes["server"], "attach-session", "-t", panes["server"]]), flush=True)
        try:
            _tmux(["attach-session", "-t", panes["server"]], executable=plan["tmux_executable"], server=panes["server"], capture=False)
        except ValueError:
            if not (home / "exit.json").exists():
                raise
        if not (home / "exit.json").exists():
            if owner_started(home) and not owner_alive(home):
                raise ValueError("the owner exited without a persisted exit record; the outer launcher cannot recover the host status")
            print("Detached; this Scope launch continues in its tmux server.")
            return 0
    else:
        _tmux(["select-window", "-t", panes["window"]], executable=plan["tmux_executable"])
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while not (home / "exit.json").exists():
            if owner_started(home) and not owner_alive(home):
                raise ValueError("the launcher owner exited without a final process record; inspect this run's receipt")
            if not owner_started(home) and time.monotonic() >= deadline:
                _close_window(home, _load_launcher(home)[1])
                raise ValueError("the launcher owner did not start; inspect the run before starting a fresh launch")
            time.sleep(0.1)
    result = _read_private(home / "exit.json", 4096)
    _print_exit(result, plan["scope_executable"])
    return result["exit_code"]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise ValueError("launcher route required")
    route = argv.pop(0)
    parser = argparse.ArgumentParser(prog="scope " + route, description=__doc__, allow_abbrev=False)
    if route == "ready":
        parser.add_argument("--json", action="store_true")
    elif route == "launch-agent":
        parser.add_argument("--home", type=Path, required=True)
        parser.add_argument("--watch", action="store_true", help=argparse.SUPPRESS)
    elif route in {"codex", "claude"}:
        parser.add_argument("task", nargs="?")
        parser.add_argument("-C", "--cwd", type=Path)
        parser.add_argument("-m", "--model")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--permissions-only", action="store_true")
        mode.add_argument("--understanding-only", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--manual-watch", action="store_true")
    else:
        raise ValueError("unsupported launcher route")
    args = parser.parse_args(argv)
    try:
        if route == "launch-agent":
            return run_agent(args.home, watch=args.watch)
        if route == "ready":
            result = readiness()
            if args.json:
                print(json.dumps(result, ensure_ascii=True))
            else:
                from .watch_ui import display_text
                print("Native startup: " + display_text(result["status"]) + "; review: " +
                      ("reachable" if result["review_reachable"] else "unavailable") +
                      "; permissions: " + display_text(result["permission_status"]))
                print(result["action"])
            return 0 if result["ready"] else 1
        plan = preflight(route, cwd=args.cwd, task=args.task, model=args.model,
                         permissions=not args.understanding_only, understanding=not args.permissions_only,
                         manual_watch=args.manual_watch, dry_run=args.dry_run)
        if args.dry_run:
            print(json.dumps(plan, ensure_ascii=True, indent=2))
            return 0
        return start(plan)
    except (OSError, ValueError, RuntimeError) as exc:
        from .watch_ui import display_text
        print("scope " + route + ": " + display_text(exc), file=sys.stderr)
        return 1
