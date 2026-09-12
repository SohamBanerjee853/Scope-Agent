#!/usr/bin/env python3
"""Exercise installed Scope launchers with real tmux/PTYs and fake coding hosts.

Run after a locked noneditable install:
    uv run --no-sync python -I scripts/check-launcher.py

All answers are labeled test fixtures in disposable terminals. This proves the
Scope launcher/adapter journey, not native host trust, human understanding, model
work, or native Windows pane behavior. No signed-in host executable is invoked.
"""

import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time
import tomllib
import uuid

MAX_OUTPUT = 256 * 1024
CASE_SECONDS = 60
_TAG = re.compile(r"Reply with ([1-9][0-9]*-[0-9a-f]{8}) followed")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def installed_scope():
    import scope
    from scope.install import console_script

    path = Path(scope.__file__).resolve()
    distribution = metadata.distribution("scope-agent")
    sites = {Path(sysconfig.get_path(name)).resolve() for name in ("purelib", "platlib")}
    require(any(path.is_relative_to(site) for site in sites), "Scope must come from installed site-packages")
    require(path == Path(distribution.locate_file("scope/__init__.py")).resolve(), "Scope package provenance mismatch")
    direct = distribution.read_text("direct_url.json")
    require(direct is None or json.loads(direct).get("dir_info", {}).get("editable") is not True,
            "Scope must be installed without editable sources")
    from scope import host_hook, host_session, launch, mailbox  # Installed new modules must exist.
    return console_script(), distribution.version


def command(argv, *, env=None, cwd=None, timeout=15, expected=0, input=None):
    # These are fixed fixture programs. Bound capture on disk before decoding.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        result = subprocess.run(argv, cwd=cwd, env=env, input=input, stdout=output, stderr=errors,
                                timeout=timeout, check=False)
        output.seek(0)
        raw = output.read(MAX_OUTPUT + 1)
        errors.seek(0)
        diagnostic = errors.read(MAX_OUTPUT + 1)
    require(len(raw) <= MAX_OUTPUT and len(diagnostic) <= MAX_OUTPUT, "fixture subprocess output exceeded its bound")
    require(result.returncode == expected, "fixture command failed: " + Path(str(argv[0])).name +
            " (exit " + str(result.returncode) + "; " + diagnostic.decode("utf-8", errors="replace")[:300] + ")")
    return raw.decode("utf-8", errors="strict")


def fixture_environment(root):
    # Keep terminal/executable/OS basics without copying account credentials into
    # the fixture's private environment record, even temporarily.
    allowed = {"PATH", "LANG", "TERM", "COLORTERM", "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "WINDIR"}
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in allowed or key.upper().startswith("LC_")}
    for key, name in (("HOME", "user"), ("USERPROFILE", "user"), ("XDG_CONFIG_HOME", "config"),
                      ("APPDATA", "appdata"), ("LOCALAPPDATA", "localappdata"), ("SCOPE_HOME", "scope"),
                      ("CODEX_HOME", "codex"), ("CLAUDE_CONFIG_DIR", "claude")):
        destination = root / "homes" / name
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        environment[key] = str(destination)
    token = uuid.uuid4().hex
    guard = root / "fixture-guard.json"
    guard.write_text(json.dumps({"token": token}), encoding="utf-8")
    guard.chmod(0o600)
    environment.update(LAUNCHER_FIXTURE_ROOT=str(root), LAUNCHER_FIXTURE_TOKEN=token,
                       LAUNCHER_ENV_SENTINEL="fixture-environment-preserved",
                       SCOPE_POPUP="0", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
                       GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       TERM="xterm-256color")
    return environment


def fake_host(host, argv):
    """Run only inside the current harness's guarded disposable launch."""
    from scope import experience, host_session, learning, ui, wire

    root = Path(os.environ["LAUNCHER_FIXTURE_ROOT"]).resolve(strict=True)
    require(json.loads((root / "fixture-guard.json").read_text())["token"] == os.environ["LAUNCHER_FIXTURE_TOKEN"],
            "fake host requires its owned fixture guard")
    launch = host_session.current_launch()
    require(launch and launch["host"] == host and Path(launch["home"]).is_relative_to(root), "fake host is outside its owned launch")
    require(Path(launch["cwd"]).is_relative_to(root), "fake host project is outside fixture root")
    require(all(Path(os.environ[key]).is_relative_to(root) for key in ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR")),
            "fake host inherited unrelated account homes")
    require(not os.environ.get("CODEX_THREAD_ID") and not os.environ.get("CLAUDECODE"), "parent native identity leaked into fake host")
    require(os.environ.get("LAUNCHER_ENV_SENTINEL") == "fixture-environment-preserved", "launcher lost fixture environment")
    scope, _ = installed_scope()
    home, project = Path(launch["home"]), Path(launch["cwd"])
    stage = "configuration"
    try:
        if host == "codex":
            hooks = {}
            for index, argument in enumerate(argv):
                if argument == "-c":
                    parsed = tomllib.loads(argv[index + 1])
                    hooks.update(parsed.get("hooks", {}))
            require(argv[argv.index("-C") + 1] == str(project), "Codex cwd invocation differs")
        else:
            settings = Path(argv[argv.index("--settings") + 1])
            require(settings.parent == home, "Claude settings escaped run")
            hooks = json.loads(settings.read_text())["hooks"]
            require("scope ready" in argv[argv.index("--append-system-prompt") + 1], "Claude appended guidance is missing")
        require(set(hooks) == {"SessionStart", "PermissionRequest", "Stop", "SessionEnd"}, "generated hook event set differs")
        require(argv[argv.index("--add-dir") + 1] == str(home), "only intended run access must be added")
        for groups in hooks.values():
            require(len(groups) == 1 and len(groups[0]["hooks"]) == 1, "unexpected Scope hook multiplicity")
            handler = groups[0]["hooks"][0]
            require(shlex.split(handler["command"]) == [str(scope), "host-hook", "--host", host],
                    "fake host refuses a non-Scope adapter command")

        native = "fixture-" + host + "-" + uuid.uuid4().hex
        environment = dict(os.environ)

        def hook(kind, **fields):
            handler = hooks[kind][0]["hooks"][0]
            event = {"hook_event_name": kind, "session_id": native, "cwd": str(project), **fields}
            return command(shlex.split(handler["command"]), cwd=project, env=environment,
                           timeout=CASE_SECONDS, input=json.dumps(event).encode())

        stage = "startup"
        startup = json.loads(hook("SessionStart", source="startup"))
        require(set(startup) == {"hookSpecificOutput"} and
                startup["hookSpecificOutput"]["hookEventName"] == "SessionStart", "startup wire differs")
        if host == "codex":
            environment["CODEX_THREAD_ID"] = native
            os.environ["CODEX_THREAD_ID"] = native
        ready = json.loads(command([str(scope), "ready", "--json"], cwd=project, env=environment))
        require(ready["ready"] is True and ready["session_id"] == native and
                ready["permission_status"] == "configured/waiting", "readiness invented or missed evidence")

        stage = "permission"
        command([str(scope), "propose", "--summary", "Scripted fixture staging", "--command", "git add *", "--budget", "2"],
                cwd=project, env=environment)
        wire_results = [hook("PermissionRequest", tool_name="Bash", tool_input={"command": "git add probe.py"})
                        for _ in range(2)]
        require(wire_results == [wire.decision_json("allow")] * 2, "two matching requests did not reuse the bounded grant")
        require(hook("PermissionRequest", tool_name="Bash", tool_input={"command": "git push origin main"}) == "",
                "T3 request did not abstain")
        # The push above is only hook input; no Git push process is created.
        observed = json.loads(command([str(scope), "ready", "--json"], cwd=project, env=environment))
        require(observed["ready"] is True and observed["permission_status"] == "requests observed"
                and observed["permission_requests"] == 3, "readiness did not report actual intercepted fixture requests")

        stage = "understanding"
        task = learning.start(project, "Scripted fixture checks its one-value probe")
        check = experience.check(project, task["task_id"], {
            "question": "SCRIPTED FIXTURE: predict the answer field emitted by this fixed probe.",
            "citations": ["probe.py:2"], "field": "answer", "argv": [sys.executable, str(project / "probe.py")],
            "shell": "posix", "timeout": 5,
        }, ask=ui.answer_request, show=ui.show_request, provenance="test_fixture")
        require(check["phase"] == "completed" and check["observation"]["status"] == "matched", "fixture understanding probe was not verified")
        require(check["prediction"]["provenance"] == "test_fixture", "scripted answer provenance was not retained")

        stage = "shutdown"
        require(hook("Stop") == "" and hook("SessionEnd") == "", "lifecycle emitted a decision")
        report = {"verified": True, "host": host, "session_id": native, "task_id": task["task_id"],
                  "human_answers": "test_fixture", "startup": "fake host invoked real native adapter",
                  "wire_allows": len(wire_results), "t3_abstained": True,
                  "observation": check["observation"]["status"], "hook_events": sorted(hooks)}
        (home / "fixture-result.json").write_text(json.dumps(report), encoding="utf-8")
        return 0
    except Exception as exc:
        (home / "fixture-result.json").write_text(json.dumps({"verified": False, "stage": stage,
                                                             "error_type": type(exc).__name__}), encoding="utf-8")
        raise


def spawn_pty(argv, environment, cwd):
    import fcntl
    import pty
    import struct
    import termios

    pid, descriptor = pty.fork()
    if pid == 0:
        try:
            os.chdir(cwd)
            os.execvpe(str(argv[0]), [str(item) for item in argv], environment)
        finally:
            os._exit(127)
    fcntl.ioctl(descriptor, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 140, 0, 0))
    os.set_blocking(descriptor, False)
    return pid, descriptor


def drain(descriptor, output):
    try:
        while True:
            chunk = os.read(descriptor, 8192)
            if not chunk:
                return
            output.extend(chunk)
            if len(output) > MAX_OUTPUT:
                del output[:-MAX_OUTPUT]
    except (BlockingIOError, OSError):
        return


def tmux_command(tmux, server, arguments, environment, *, expected=0):
    return command([tmux, "-L", server, "-f", os.devnull, *arguments], env=environment, expected=expected).strip()


def one_case(root, host, inside, scope, tmux):
    from scope import receipt

    case_name = host + ("-inside" if inside else "-outside")
    case_root = root / case_name
    case_root.mkdir(mode=0o700)
    environment = fixture_environment(case_root)
    project = case_root / "project"
    project.mkdir()
    (project / "probe.py").write_text('import json\nprint(json.dumps({"answer": 1}))\n', encoding="utf-8")
    command(["git", "init", "-q", str(project)], env=environment)
    command(["git", "-C", str(project), "add", "probe.py"], env=environment)
    binaries = case_root / "bin"
    binaries.mkdir()
    for name in ("codex", "claude"):
        wrapper = binaries / name
        wrapper.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, "-I", str(Path(__file__).resolve()),
                                                            "--fake-host", name]) + ' "$@"\n', encoding="utf-8")
        wrapper.chmod(0o700)
    environment["PATH"] = str(binaries) + os.pathsep + str(scope.parent) + os.pathsep + environment.get("PATH", "")
    parent_id = "fixture-parent-" + uuid.uuid4().hex
    environment["CODEX_THREAD_ID"] = parent_id
    environment["CLAUDECODE"] = "fixture-parent-marker"
    environment["CLAUDE_SESSION_ID"] = parent_id
    argv = [str(scope), host, "SCRIPTED FIXTURE ONLY", "-C", str(project)]
    outer_server = "scope-harness-" + uuid.uuid4().hex if inside else None
    owned_servers = set()
    child_pid = descriptor = None
    child_status = None
    terminal_output = bytearray()
    home = panes = None
    replies = []
    sentinel = None
    try:
        if inside:
            owned_servers.add(outer_server)
            sentinel = tmux_command(tmux, outer_server, ["new-session", "-d", "-s", "harness", "-n", "sentinel",
                                    "-x", "140", "-y", "40", "-P", "-F", "#{window_id}",
                                    shlex.join([sys.executable, "-c", "import time; time.sleep(120)"])], environment)
            tmux_command(tmux, outer_server, ["set-option", "-g", "base-index", "7"], environment)
            tmux_command(tmux, outer_server, ["set-window-option", "-g", "pane-base-index", "11"], environment)
            tmux_command(tmux, outer_server, ["new-window", "-d", "-t", "harness", "-n", "fixture-invoker",
                                             "-c", str(project), "exec " + shlex.join(argv)], environment)
            child_pid, descriptor = spawn_pty([tmux, "-L", outer_server, "-f", os.devnull, "attach-session", "-t", "harness"],
                                               environment, project)
        else:
            child_pid, descriptor = spawn_pty(argv, environment, project)
        deadline = time.monotonic() + CASE_SECONDS
        answers = ["grant", json.dumps({"value": 1, "reason": "SCRIPTED FIXTURE: the fixed probe emits one."}), "true"]
        indexes = None
        while time.monotonic() < deadline:
            drain(descriptor, terminal_output)
            if child_status is None:
                waited, status = os.waitpid(child_pid, os.WNOHANG)
                if waited:
                    child_status = os.waitstatus_to_exitcode(status)
            runs = Path(environment["SCOPE_HOME"]) / "runs"
            candidates = list(runs.iterdir()) if runs.exists() else []
            require(len(candidates) <= 1, "one invocation unexpectedly created multiple runs")
            if candidates:
                home = candidates[0]
                if panes is None and (home / "panes.json").exists():
                    panes = json.loads((home / "panes.json").read_text())
                    require(panes["launch_id"] == home.name and re.fullmatch(r"%[0-9]+", panes["review_pane"]),
                            "refusing answers for an unrelated or malformed pane")
                    if not inside:
                        require(panes["server"] == home.name, "outside launcher did not use its own server")
                        owned_servers.add(panes["server"])
                    else:
                        require(panes["server"] is None, "inside launcher created a different server")
                if panes and not (home / "exit.json").exists():
                    server = outer_server or panes["server"]
                    if indexes is None:
                        raw = tmux_command(tmux, server, ["list-panes", "-t", panes["window"], "-F", "#{pane_id}\t#{pane_index}"], environment)
                        indexes = dict(line.split("\t") for line in raw.splitlines())
                        require(set(indexes) == {panes["agent_pane"], panes["review_pane"]}, "unexpected launch pane membership")
                        if inside:
                            require(sorted(map(int, indexes.values())) == [11, 12], "custom pane numbering was not exercised")
                    try:
                        screen = tmux_command(tmux, server, ["capture-pane", "-p", "-J", "-S", "-150", "-t", panes["review_pane"]], environment)
                    except RuntimeError:
                        # Native SessionEnd closes review just before the owner
                        # writes exit.json; no further fixture answer is needed.
                        if len(replies) != len(answers):
                            raise
                        screen = ""
                    tags = _TAG.findall(screen)
                    for tag in tags:
                        if tag in replies:
                            continue
                        require(len(replies) < len(answers), "unexpected extra terminal prompt")
                        answer = answers[len(replies)]
                        tmux_command(tmux, server, ["send-keys", "-t", panes["review_pane"], "-l", tag + " " + answer], environment)
                        tmux_command(tmux, server, ["send-keys", "-t", panes["review_pane"], "Enter"], environment)
                        replies.append(tag)
                if (home / "exit.json").exists():
                    break
            if child_status is not None:
                require(False, "launcher terminal exited before final evidence: " + terminal_output.decode("utf-8", errors="replace")[-1200:])
            time.sleep(0.05)
        require(home is not None and (home / "exit.json").exists(), "launcher rehearsal timed out: " + case_name)
        result = json.loads((home / "fixture-result.json").read_text())
        require(result.get("verified") is True, "fake host failed at " + str(result.get("stage")))
        exit_result = json.loads((home / "exit.json").read_text())
        require(exit_result["exit_code"] == 0 and exit_result["session_id"] == result["session_id"], "host exit status or session was not preserved")
        require(len(replies) == 3 and len(set(replies)) == 3, "fixture terminal replies did not use three distinct tags")
        final = receipt.load(receipt.receipt_path(result["session_id"], home=home))
        expected_counts = {"requests": 3, "auto_allowed": 2, "allowed_once": 0, "denied": 0, "hard_asks": 1, "scopes_granted": 1}
        require(final["counts"] == expected_counts, "correlated permission receipt differs from actual fixture requests")
        require(final["host"] == host, "receipt host label differs")
        for key in ("tasks", "predictions", "executions", "observations"):
            require(len(final["understanding"][key]) == 1, "receipt missing fixture understanding evidence: " + key)
        require(not receipt.receipt_path(parent_id, home=home).exists(), "fixture receipt contaminated parent identity")
        # Wait for actual pane destruction; exit.json deliberately precedes it.
        cleanup_deadline = time.monotonic() + 5
        server = outer_server or panes["server"]
        window_closed = False
        while time.monotonic() < cleanup_deadline:
            result_windows = subprocess.run([tmux, "-L", server, "list-windows", "-a", "-F", "#{window_id}"],
                                            env=environment, capture_output=True, text=True, timeout=5)
            windows = set(result_windows.stdout.splitlines())
            if panes["window"] not in windows:
                window_closed = True
                if inside:
                    require(result_windows.returncode == 0 and sentinel in windows, "launcher closed the unrelated sentinel window")
                break
            time.sleep(0.05)
        require(window_closed, "owned Scope window did not close")
        require(not (home / "environment.json").exists(), "owned environment survived normal exit")
        require(not (home / "watch.json").exists(), "owned reviewer endpoint survived normal exit")
        return {"case": case_name, "host": host, "inside_tmux": inside, "verified": True,
                "tagged_fixture_answers": len(replies), "permission_counts": final["counts"],
                "understanding_counts": {key: len(final["understanding"][key]) for key in ("tasks", "predictions", "executions", "observations")},
                "custom_pane_indexes": sorted(map(int, indexes.values())), "owned_window_closed": True,
                "unrelated_window_preserved": True if inside else None, "host_exit_code": exit_result["exit_code"],
                "provenance": "test_fixture; fake host invoked real Scope adapters, PTYs and tmux"}
    finally:
        for server in owned_servers:
            subprocess.run([tmux, "-L", server, "kill-server"], env=environment,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
        if descriptor is not None:
            os.close(descriptor)
        if child_pid is not None and child_status is None:
            waited, _ = os.waitpid(child_pid, os.WNOHANG)
            if not waited:
                try:
                    os.kill(child_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                os.waitpid(child_pid, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fake-host", choices=("codex", "claude"), help=argparse.SUPPRESS)
    args, remainder = parser.parse_known_args()
    if args.fake_host:
        return fake_host(args.fake_host, remainder)
    require(not remainder, "unrecognized harness arguments")
    require(os.name == "posix", "real tmux/PTY rehearsal requires macOS, Linux or WSL; native Windows is not verified")
    scope, version = installed_scope()
    tmux = shutil.which("tmux")
    require(tmux is not None, "tmux is required for the terminal rehearsal")
    tmux_version = command([tmux, "-V"]).strip()
    with tempfile.TemporaryDirectory(prefix="scope-launcher-fixture-") as temporary:
        root = Path(temporary).resolve()
        reports = [one_case(root, host, inside, scope, tmux) for host in ("codex", "claude") for inside in (False, True)]
    print(json.dumps({"verified": True, "scope_version": version, "tmux_version": tmux_version,
                      "cases": reports, "temporary_files_removed": not root.exists(),
                      "limits": "Scripted fixtures only. Native Codex/Claude trust, real human answers, model work and native Windows panes were not exercised."}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("launcher fixture failed: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
