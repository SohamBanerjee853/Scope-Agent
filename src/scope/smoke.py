"""Offline, explicitly scripted checks of Scope's real permission transport.

The requested commands remain inert JSON strings. Only Scope's own Python hook
processes run; the fixture never starts a coding host, shell command or model.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


_ALLOW = '{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}'
_PYTHON_ENTRY = "import sys; from scope.cli import main; raise SystemExit(main(sys.argv[1:]))"
_WORKER_ENTRY = "from scope.smoke import _worker_main; raise SystemExit(_worker_main())"
_SHELLS = ("posix", "powershell")
_HOMES = {"SCOPE_HOME": "scope", "CODEX_HOME": "codex", "CLAUDE_CONFIG_DIR": "claude"}


def plan(shell: str, *, live: bool = False) -> dict:
    """Describe work without probing the filesystem, network or installed hosts."""
    if shell not in _SHELLS:
        raise ValueError("unsupported smoke shell")
    result = {
        "mode": "live_plan" if live else "offline_plan",
        "shell": shell,
        "performed": False,
        "human_answers": "none; execution would use explicitly labeled scripted fixtures",
        "host_execution": False,
        "requested_command_execution": False,
        "steps": [
            "Create a disposable workspace and fresh SCOPE_HOME, CODEX_HOME and CLAUDE_CONFIG_DIR.",
            "Generate a new synthetic session UUID; remove inherited host and launch identities.",
            "Start Scope's authenticated loopback watcher with one labeled fixture grant.",
            "Submit two exact local command patterns with budget 3; request two matching T2 permissions.",
            "Capture exact allow bytes from two actual Scope hook subprocesses; execute neither requested command.",
            "Revoke while one budget unit remains; confirm the next matching request abstains.",
            "Submit a synthetic git push string; confirm T3 and empty hook stdout without executing it.",
            "Append synthetic SessionEnd, build the receipt, close the watcher and remove temporary files.",
        ],
        "limits": [
            "Scripted fixture decisions are not human answers, proof of understanding or command execution.",
            "Both shell dialects are parsed on any OS; this does not exercise a native PowerShell host.",
            "No global hooks, host settings, model calls or external network services are used.",
        ],
    }
    if live:
        result["status"] = "manual_setup_required"
        result["steps"] = [
            "Run codex --version and codex --help yourself, then verify that version's official hook and CLI contract.",
            "Use a disposable Git project, temporary Scope records and isolated host configuration with your existing signed-in Codex.",
            "Prepare the installed version's explicit temporary prompt rules and on-request approval configuration.",
            "Configure Scope hooks for that invocation only, preserving the host sandbox and unrelated settings.",
            "Inspect and trust the hook definitions through the host's normal trust UI; restart if that version requires it.",
            "Verify a real PermissionRequest was intercepted before testing a human grant, reuse and revocation.",
            "Keep git push synthetic; record native handling and command execution separately from hook permission output.",
            "Remove temporary configuration afterward; never leave an always-allow hook or broaden network access.",
        ]
        result["limits"].append(
            "Live launch automation is unavailable in S3; native Windows helper/sandbox behavior and live hook trust remain unverified."
        )
    return result


def _environment(root: Path, session: str, shell: str) -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if (name in {"CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_TASK_ID", "CLAUDECODE"}
                or name.startswith(("SCOPE_", "CLAUDE_"))):
            environment.pop(name, None)
    environment.update({name: str(root / child) for name, child in _HOMES.items()})
    environment.update({
        "SCOPE_SMOKE": "1", "SCOPE_POPUP": "0", "PYTHONDONTWRITEBYTECODE": "1",
        "_SCOPE_SMOKE_ROOT": str(root), "_SCOPE_SMOKE_SESSION": session,
        "_SCOPE_SMOKE_SHELL": shell,
    })
    return environment


def run(shell: str = "posix") -> dict:
    """Run the complete fixture in a separate process and erase its private homes."""
    if shell not in _SHELLS:
        raise ValueError("unsupported smoke shell")
    session = "demo-smoke-" + str(uuid.uuid4())
    with tempfile.TemporaryDirectory(prefix="scope-smoke-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        for name in _HOMES.values():
            (root / name).mkdir(mode=0o700)
        try:
            completed = subprocess.run(
                [sys.executable, "-c", _WORKER_ENTRY],
                env=_environment(root, session, shell), cwd=workspace,
                capture_output=True, text=True, encoding="utf-8", timeout=40,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("offline smoke worker could not complete") from exc
        if completed.returncode != 0:
            raise RuntimeError("offline smoke fixture failed; no live host was launched")
        try:
            report = json.loads(completed.stdout)
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError("offline smoke worker returned an invalid report") from exc
        if (not isinstance(report, dict) or report.get("session_id") != session
                or report.get("mode") != "scripted_fixture" or report.get("shell") != shell
                or report.get("verified") is not True):
            raise RuntimeError("offline smoke worker returned an inconsistent report")
    report["temporary_files_removed"] = True
    return report


class _ScriptedUI:
    """An explicit fixture adapter; it never reads a person or coding session."""

    interactive = True

    def __init__(self):
        self.reviews = []

    def review(self, request, card, context):
        action = "grant" if not self.reviews and card is not None else "abstain"
        self.reviews.append({"command": request.command, "fixture_action": action})
        return {"action": action}

    def question(self, *args):
        raise RuntimeError("the permission smoke fixture has no understanding answers")

    def notice(self, message):
        pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _hook(payload: dict, *, route: str = "hook") -> dict:
    completed = subprocess.run(
        [sys.executable, "-c", _PYTHON_ENTRY, route],
        input=json.dumps(payload), text=True, encoding="utf-8", capture_output=True,
        timeout=8, check=False,
    )
    _require(completed.returncode == 0, "Scope hook returned a nonzero status")
    _require(completed.stderr == "", "Scope hook reported an unexpected diagnostic")
    return {"stdout": completed.stdout, "exit_code": completed.returncode}


def _fixture(root: Path, session: str, shell: str) -> dict:
    from . import ipc, log, receipt
    from .tiers import classify
    from .watch import Watcher

    workspace = root / "workspace"
    cwd = str(workspace)
    commands = (["touch scope-smoke-one", "touch scope-smoke-two"] if shell == "posix" else
                ["Set-Content scope-smoke-one fixture-one", "Set-Content scope-smoke-two fixture-two"])
    card = {"summary": "SCRIPTED FIXTURE: two local permission requests; no commands execute",
            "commands": commands, "domains": [], "budget": 3}

    def payload(command):
        return {"hook_event_name": "PermissionRequest", "session_id": session,
                "cwd": cwd, "tool_name": "Bash" if shell == "posix" else "PowerShell",
                "tool_input": {"command": command, "shell": shell,
                               "description": "SCRIPTED FIXTURE request; never executed"}}

    _require(all(classify(command, cwd, shell).name == "T2" for command in commands),
             "fixture commands must require a T2 decision")
    log.append(session, "smoke_fixture_start", scripted=True, shell=shell,
               human_answers=False, requested_command_execution=False)
    ui = _ScriptedUI()
    results = []
    with Watcher(ui, human_timeout=3) as watcher:
        _require(ipc.exchange({"kind": "ping"}, timeout=3) == {"ready": True},
                 "isolated watcher is unavailable")
        proposal = {"kind": "proposal", "session_id": session, "cwd": cwd, "card": card}
        _require(ipc.exchange(proposal, timeout=3) == {"accepted": True}, "fixture proposal was refused")
        for command in commands:
            result = _hook(payload(command))
            _require(result["stdout"] == _ALLOW, "grant request did not produce exact allow bytes")
            results.append({"label": "matching_t2", "command": command, "tier": "T2", **result})
        _require(len(ui.reviews) == 1, "second request did not reuse the fixture grant")
        decision_events = [event["fields"] for event in log.read(session)
                           if event["event"] == "permission_review_decision"]
        _require([event.get("remaining") for event in decision_events] == [2, 1],
                 "grant consumption did not leave one unused budget unit")
        _require(ipc.exchange({"kind": "revoke"}, timeout=3) == {"revoked": True}, "revocation failed")
        _require(watcher.store.sessions() == (), "revocation retained a grant or proposal")
        revoked = _hook(payload(commands[0]))
        _require(revoked["stdout"] == "", "revoked request reused permission")
        results.append({"label": "after_revoke", "command": commands[0], "tier": "T2", **revoked})
        dangerous = "git push origin main"
        _require(classify(dangerous, cwd, shell).name == "T3", "synthetic git push was not T3")
        hard_ask = _hook(payload(dangerous))
        _require(hard_ask["stdout"] == "", "synthetic T3 request did not abstain")
        results.append({"label": "synthetic_hard_ask", "command": dangerous, "tier": "T3", **hard_ask})
        _require(len(ui.reviews) == 2, "hard ask reached the fixture approval UI")

    ended = _hook({"hook_event_name": "SessionEnd", "session_id": session,
                   "cwd": cwd, "reason": "other"}, route="session-end")
    _require(ended["stdout"] == "", "SessionEnd emitted a permission decision")
    _require(not list(workspace.iterdir()), "requested fixture commands unexpectedly wrote files")
    log.append(session, "smoke_fixture_complete", scripted=True, requested_command_execution=False)
    document = receipt.write(session, home=root / "scope")
    _require(document.get("counts") == {"requests": 4, "auto_allowed": 2, "allowed_once": 0,
                                      "denied": 0, "hard_asks": 1, "scopes_granted": 1},
             "receipt counts do not match observed hook decisions")
    return {"mode": "scripted_fixture", "verified": True, "shell": shell, "session_id": session,
            "human_answers": "SCRIPTED FIXTURE: grant, then abstain; no person answered",
            "host_execution": False, "requested_command_execution": False,
            "external_network_services": False, "native_shell_execution": False,
            "wire_results": results, "fixture_reviews": ui.reviews, "card": card,
            "unused_budget_before_revoke": 1, "revoked": True,
            "session_end_stdout": ended["stdout"], "receipt": document,
            "limits": plan(shell)["limits"]}


def _worker_main() -> int:
    """Private child entry point; the public CLI always creates its own homes."""
    try:
        root = Path(os.environ["_SCOPE_SMOKE_ROOT"])
        session = os.environ["_SCOPE_SMOKE_SESSION"]
        shell = os.environ["_SCOPE_SMOKE_SHELL"]
        _require(session.startswith("demo-smoke-") and
                 str(uuid.UUID(session.removeprefix("demo-smoke-"))) == session.removeprefix("demo-smoke-"),
                 "synthetic session identity is required")
        _require(shell in _SHELLS and root.name.startswith("scope-smoke-"), "invalid fixture context")
        _require(all(os.environ.get(name) == str(root / child) for name, child in _HOMES.items()),
                 "fixture homes must remain isolated")
        _require("CODEX_THREAD_ID" not in os.environ and "SCOPE_LAUNCH_ID" not in os.environ,
                 "inherited host identity is forbidden")
        print(json.dumps(_fixture(root, session, shell), ensure_ascii=True, allow_nan=False))
        return 0
    except Exception:
        print("scope smoke: isolated fixture verification failed", file=sys.stderr)
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scope smoke", description=__doc__, allow_abbrev=False)
    parser.add_argument("--shell", choices=_SHELLS, default="posix")
    parser.add_argument("--dry-run", action="store_true", help="print the offline plan without writes or processes")
    parser.add_argument("--json", action="store_true", help="emit the captured report or plan as JSON")
    parser.add_argument("--live", action="store_true", help="with --dry-run, show manual live-test prerequisites only")
    args = parser.parse_args(argv)
    if args.live and not args.dry_run:
        print("scope smoke: live automation is unavailable; run scope smoke --live --dry-run for the manual "
              "version, on-request approval and invocation hook-trust prerequisites. No host was started.", file=sys.stderr)
        return 2
    try:
        report = plan(args.shell, live=args.live) if args.dry_run else run(args.shell)
    except Exception:
        print("scope smoke: offline fixture failed; no live host was launched or global hook installed", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2))
    elif args.dry_run:
        print(f"{report['mode'].upper()} ({args.shell}): no work performed")
        for index, step in enumerate(report["steps"], 1):
            print(f"{index}. {step}")
        for limit in report["limits"]:
            print(limit)
    else:
        print(f"SCRIPTED FIXTURE ({args.shell}): {report['session_id']}")
        print("Two exact hook allows; revocation blocked reuse with budget remaining; synthetic git push stayed T3.")
        print("Receipt built from observed events. Temporary homes removed.")
        print("No human answers, requested commands, native shell, coding host or model were executed.")
        pending = report["receipt"].get("understanding", {}).get("status") == "pending_integration"
        print("Understanding projection unavailable." if pending else "Understanding projection loaded; this fixture collects no predictions.")
        print("The A2 understanding workflow and live/native Windows behavior remain unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
