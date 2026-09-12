"""Prepare a payment retry project or rehearse it with explicit fixture answers."""

import argparse
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from xml.etree import ElementTree

from . import runner

MARKER = ".scope-demo.json"
ASSETS = ("payment.py", "test_payment.py", "README.md")
_WORKER_ENTRY = "from scope.demo import _worker_main; raise SystemExit(_worker_main())"
_HOMES = {"SCOPE_HOME": "scope", "CODEX_HOME": "codex", "CLAUDE_CONFIG_DIR": "claude",
          "HOME": "user", "USERPROFILE": "user", "XDG_CONFIG_HOME": "config",
          "APPDATA": "appdata", "LOCALAPPDATA": "localappdata"}
_COMMAND = "scope demo-adapter probe"
_REPAIR = "SCRIPTED FIXTURE: change retry_key to return order_id, preserving distinct order identities."
_REGRESSION_NAMES = frozenset({"test_retry_after_lost_acknowledgement_charges_one_order_once",
                              "test_fake_service_deduplicates_an_identical_idempotency_key",
                              "test_distinct_orders_have_distinct_charges"})
_STAGES = frozenset({"context", "prepare", "watcher", "first_permission", "first_check",
                     "initial_tests", "next_task", "dispatch", "repair", "final_tests",
                     "second_permission", "second_check", "revoke", "hard_ask", "session_end", "receipt"})


def _linked(path):
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if part.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            return True
    return False


def _git_environment():
    # Git selection overrides must not redirect preparation into a real repo.
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def prepare(path, *, dry_run=False):
    destination = Path(path).expanduser().absolute()
    if _linked(destination):
        raise ValueError("demo destination must not use symbolic links")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("demo preparation requires a new or empty directory")
    result = {"mode": "prepare_only", "project": str(destination), "dry_run": dry_run,
              "files": [*ASSETS, "pytest.ini", MARKER, ".agents/skills/scope-understand/SKILL.md"],
              "meaning": "Disposable fixture only. No prediction, consent, probe or model session was performed."}
    if dry_run:
        return result
    destination.mkdir(parents=True, exist_ok=True)
    # Exclusive creation refuses races rather than replacing existing work.
    resources = files("scope").joinpath("assets", "demo")
    session = "demo-" + str(uuid.uuid4())
    marker = {"schema_version": 1, "fixture": "scope-payment-retry", "session_id": session}
    payloads = {name: resources.joinpath(name).read_bytes() for name in ASSETS}
    payloads.update({"pytest.ini": b"[pytest]\n", MARKER: (json.dumps(marker) + "\n").encode()})
    for name, data in payloads.items():
        with (destination / name).open("xb") as stream:
            stream.write(data)
    git = runner.run(["git", "init", "--quiet"], cwd=destination, env=_git_environment(), timeout=10)
    if not git.succeeded:
        raise RuntimeError("demo files were prepared, but Git initialization failed; inspect that directory")
    from .learning_cli import install_skill
    install_skill(destination)
    result["session_id"] = session
    result["prepared"] = True
    return result


def scripted_plan(shell="posix"):
    if shell not in {"posix", "powershell"}:
        raise ValueError("unsupported demo shell")
    return {"mode": "scripted_plan", "shell": shell, "performed": False,
            "steps": ["Create temporary project and homes with a fresh demo UUID.",
                      "Use explicitly labeled fixture answers through the authenticated TCP watcher.",
                      "Record prediction, separate consent and actual bounded probe before and after the fixture repair.",
                      "Select and deliver a fixture instruction to a fixture inbox, then consume it for the exact repair.",
                      "Run the packaged regression tests before and after the repair.",
                      "Observe one narrow grant, two exact hook allows, revoke and synthetic T3 abstention.",
                      "Append synthetic SessionEnd, build a combined receipt, and remove temporary files."],
            "meaning": "Scripted fixture only: no human answers, coding host, model or payment service."}


def _scripted_environment(root, shell):
    environment = os.environ.copy()
    for name in tuple(environment):
        if (name in {"CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_TASK_ID", "CLAUDECODE", "PYTHONPATH"}
                or name.startswith(("SCOPE_", "_SCOPE_", "CODEX_", "CLAUDE", "GIT_", "PYTHON", "PYTEST_"))):
            environment.pop(name, None)
    environment.update({name: str(root / child) for name, child in _HOMES.items()})
    environment.update({"SCOPE_POPUP": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
                        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "_SCOPE_DEMO_ROOT": str(root),
                        "_SCOPE_DEMO_SHELL": shell})
    # The installed console script is the narrow command under review. Prefer
    # this interpreter's environment rather than an unrelated Scope on PATH.
    environment["PATH"] = str(Path(sys.executable).absolute().parent) + os.pathsep + environment.get("PATH", "")
    return environment


def run_scripted(shell="posix"):
    """Run only a newly owned temporary project, with no parent environment edits."""
    scripted_plan(shell)
    with tempfile.TemporaryDirectory(prefix="scope-demo-") as temporary:
        root = Path(temporary).resolve(strict=True)
        for child in set(_HOMES.values()):
            (root / child).mkdir(mode=0o700)
        try:
            completed = subprocess.run([sys.executable, "-c", _WORKER_ENTRY],
                                       cwd=root, env=_scripted_environment(root, shell),
                                       stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                       encoding="utf-8", timeout=90, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("scripted demo worker could not complete") from exc
        if completed.returncode != 0:
            stage = next((stage for stage in _STAGES if completed.stderr.strip() ==
                          f"scope demo: isolated fixture verification failed [{stage}]"), None)
            detail = f" [{stage}]" if stage else ""
            raise RuntimeError(f"scripted demo failed{detail}; no live host was launched")
        try:
            report = json.loads(completed.stdout)
            session = report["session_id"]
            valid_id = (session.startswith("demo-") and
                        str(uuid.UUID(session.removeprefix("demo-"))) == session.removeprefix("demo-"))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise RuntimeError("scripted demo returned an invalid report") from exc
        if (not valid_id or report.get("mode") != "scripted_fixture" or report.get("shell") != shell
                or report.get("verified") is not True or report.get("project") != str(root / "workspace")):
            raise RuntimeError("scripted demo returned an inconsistent report")
    report["temporary_files_removed"] = True
    return report


class _FixtureUI:
    """Labeled fixture answers, without using a real person's terminal reader."""

    interactive = True

    def __init__(self):
        self.questions = []
        self.reviews = []

    def question(self, repo, context_text, prompt, context):
        context_data = json.loads(context_text)
        sequence = ("prediction", "probe_approval", "next_task", "prediction", "probe_approval")
        index = len(self.questions)
        if (index >= len(sequence) or context_data.get("fixture") != "scope-payment-retry"
                or context_data.get("kind") != sequence[index] or not prompt.startswith("SCRIPTED FIXTURE:")
                or not context.active()):
            raise ValueError("unexpected fixture question")
        kind = sequence[index]
        if kind == "prediction":
            answer = json.dumps({"value": 1,
                                 "reason": "FIXTURE hypothesis: retrying one order should preserve its payment identity.",
                                 "assistance": "Scripted rehearsal answer, not a human prediction."})
        else:
            answer = "true" if kind == "probe_approval" else "smaller"
        self.questions.append({"kind": kind, "answer": answer, "provenance": "test_fixture",
                               "route": "authenticated_tcp_watcher"})
        return answer

    def review(self, request, card, context):
        action = "grant" if not self.reviews and card is not None else "abstain"
        self.reviews.append({"command": request.command, "fixture_action": action})
        return {"action": action}

    def notice(self, message):
        pass


class _FixtureAgent:
    """A real in-memory fixture inbox and deterministic consumer, not a host."""

    def __init__(self):
        self.inbox = []

    def deliver(self, handoff):
        if handoff.get("provenance") != "test_fixture" or handoff.get("instruction") != _REPAIR:
            return False
        self.inbox.append(handoff)
        return True

    def repair(self, workspace, handoff_id):
        import hashlib
        from .demo_agent import BUGGY_KEY, FIXED_KEY
        if len(self.inbox) != 1 or self.inbox[0].get("handoff_id") != handoff_id:
            raise RuntimeError("fixture repair requires its delivered instruction")
        instruction = self.inbox.pop()
        source = workspace / "payment.py"
        bundled = files("scope").joinpath("assets", "demo", "payment.py").read_bytes()
        if source.read_bytes() != bundled or bundled.count(BUGGY_KEY.encode()) != 1:
            raise RuntimeError("fixture source changed before the exact repair")
        fixed = bundled.replace(BUGGY_KEY.encode(), FIXED_KEY.encode(), 1)
        source.write_bytes(fixed)
        if source.read_bytes() != fixed:
            raise RuntimeError("fixture repair could not be verified")
        return {"actor": "deterministic_test_fixture", "handoff_id": handoff_id,
                "instruction": instruction["instruction"], "inbox_consumed": True,
                "path": "payment.py", "before_sha256": hashlib.sha256(bundled).hexdigest(),
                "after_sha256": hashlib.sha256(fixed).hexdigest(), "coding_host_executed": False}


def _regressions(workspace, *, repaired):
    report_path = workspace.parent / ("after-tests.xml" if repaired else "before-tests.xml")
    result = runner.run([sys.executable, "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                         "--color=no", "--confcutdir", str(workspace), "--rootdir", str(workspace),
                         "-c", str(workspace / "pytest.ini"), "--junitxml", str(report_path),
                         "test_payment.py"], cwd=workspace, timeout=30)
    expected = r"\b3 passed\b" if repaired else r"\b1 failed, 2 passed\b"
    if (result.exit_code != (0 if repaired else 1) or result.timed_out or result.error is not None
            or result.stdout_truncated or result.stderr_truncated or result.cleanup_incomplete
            or re.search(expected, result.stdout) is None):
        raise RuntimeError("packaged regression result did not match the fixture behavior")
    with report_path.open("rb") as stream:
        xml = stream.read(64 * 1024 + 1)
    if len(xml) > 64 * 1024:
        raise RuntimeError("packaged regression report exceeded its bound")
    cases = ElementTree.fromstring(xml).findall(".//testcase")
    if (len(cases) != 3 or {case.get("name") for case in cases} != _REGRESSION_NAMES
            or any(case.find("error") is not None or case.find("skipped") is not None for case in cases)):
        raise RuntimeError("packaged regression did not run exactly its three intended cases")
    failed = {case.get("name") for case in cases if case.find("failure") is not None}
    expected_failed = set() if repaired else {"test_retry_after_lost_acknowledgement_charges_one_order_once"}
    if failed != expected_failed:
        raise RuntimeError("packaged regression failure did not match the retry bug")
    return {"phase": "after_repair" if repaired else "before_repair", "execution": result.to_dict(),
            "passed": len(cases) - len(failed), "failed": len(failed), "skipped": 0,
            "test_names": sorted(case.get("name") for case in cases), "failed_tests": sorted(failed)}


def _scripted_fixture(root, shell, *, progress):
    from . import experience, ipc, learning, log, receipt, ui
    from .smoke import _ALLOW, _hook, _require
    from .tiers import classify
    from .watch import Watcher

    expected_scope = Path(sys.executable).absolute().parent / ("scope.exe" if os.name == "nt" else "scope")
    found_scope = shutil.which("scope")
    _require(found_scope is not None and os.path.normcase(os.path.abspath(found_scope)) ==
             os.path.normcase(str(expected_scope)), "demo requires this interpreter's installed Scope console script")
    progress("prepare")
    workspace = root / "workspace"
    prepared = prepare(workspace)
    session = prepared["session_id"]
    task = learning.start(workspace, "SCRIPTED FIXTURE: investigate duplicate charges after a lost acknowledgement.",
                          session_id=session)
    task_id = task["task_id"]
    log.append(session, "demo_fixture_start", scripted=True, provenance="test_fixture", human_answers=False)
    card = {"summary": "SCRIPTED FIXTURE: narrow local payment probe, with separate A2 consent for each execution",
            "commands": [_COMMAND], "domains": [], "budget": 3}
    _require(classify(_COMMAND, str(workspace), shell).name == "T2", "fixture adapter must be T2")

    def payload(command):
        return {"hook_event_name": "PermissionRequest", "session_id": session, "cwd": str(workspace),
                "tool_name": "Bash" if shell == "posix" else "PowerShell",
                "tool_input": {"command": command, "shell": shell, "description": "SCRIPTED FIXTURE permission request"}}

    def ask(request):
        kind = request["kind"]
        # Only fixed fixture protocol metadata and the exact probe plan are sent;
        # answers are never derived from source, prior output or host text.
        context = {"fixture": "scope-payment-retry", "kind": kind, "argv": request.get("spec", {}).get("argv"),
                   "cwd": request["repo"]}
        answer = ui.ask("SCRIPTED FIXTURE: " + kind, repo=request["repo"],
                        context=json.dumps(context), timeout=5)
        if set(answer) != {"answer", "provenance"} or answer["provenance"] != "human_ipc":
            return None
        text = answer["answer"]
        if kind == "prediction":
            return json.loads(text)
        if kind == "probe_approval":
            return True if text == "true" else False if text == "false" else None
        return text if kind == "next_task" and text in {"smaller", "larger", "defer"} else None

    def check():
        return experience.check(workspace, task_id,
                                {"question": "How many charges will one order produce after a lost reply, and why?",
                                 "citations": ["payment.py:31-33"], "field": "charge_count",
                                 "argv": ["scope", "demo-adapter", "probe"], "shell": shell, "timeout": 15},
                                ask=ask, provenance="test_fixture")

    fixture_ui = _FixtureUI()
    agent = _FixtureAgent()
    wire = []

    def permission(label, command, expected):
        result = _hook(payload(command))
        _require(result["stdout"] == expected, "fixture hook output violated its exact contract")
        wire.append({"label": label, "command": command, **result})

    progress("watcher")
    with Watcher(fixture_ui, human_timeout=5) as watcher:
        _require(ipc.exchange({"kind": "ping"}, timeout=3) == {"ready": True}, "watcher is unavailable")
        _require(ipc.exchange({"kind": "proposal", "session_id": session, "cwd": str(workspace), "card": card},
                              timeout=3) == {"accepted": True}, "narrow fixture proposal was refused")
        progress("first_permission")
        permission("before_probe", _COMMAND, _ALLOW)
        progress("first_check")
        before = check()
        wire[-1]["execution_check_id"] = before["check_id"]
        _require(before["phase"] == "completed" and before["observation"]["status"] == "mismatched"
                 and before["observation"]["actual"] == 2, "initial probe did not observe the retry bug")
        progress("initial_tests")
        initial_tests = _regressions(workspace, repaired=False)
        log.append(session, "demo_regression", scripted=True, **initial_tests)
        progress("next_task")
        selected = experience.choose_next(workspace, task_id, smaller=_REPAIR,
                                          larger="SCRIPTED FIXTURE: inspect retry identity across additional order entry points.",
                                          ask=ask, provenance="test_fixture")
        _require(selected.get("status") == "selected", "fixture did not select its next task")
        progress("dispatch")
        delivered = experience.dispatch(workspace, task_id, selected["handoff_id"], deliver=agent.deliver)
        _require(delivered["status"] == "dispatched", "fixture inbox delivery was not acknowledged")
        progress("repair")
        patch = agent.repair(workspace, selected["handoff_id"])
        log.append(session, "demo_fixture_patch", **patch)
        learning.checkpoint(workspace, task_id, note="SCRIPTED FIXTURE consumed its instruction and applied the exact stable-key repair.")
        progress("final_tests")
        final_tests = _regressions(workspace, repaired=True)
        log.append(session, "demo_regression", scripted=True, **final_tests)
        progress("second_permission")
        permission("after_repair_probe", _COMMAND, _ALLOW)
        progress("second_check")
        after = check()
        wire[-1]["execution_check_id"] = after["check_id"]
        _require(after["phase"] == "completed" and after["observation"]["status"] == "matched"
                 and after["observation"]["actual"] == 1, "repaired probe did not observe one charge")
        decisions = [event["fields"] for event in log.read(session) if event["event"] == "permission_review_decision"]
        _require(len(fixture_ui.reviews) == 1 and [item.get("remaining") for item in decisions] == [2, 1],
                 "questions changed or failed to reuse the narrow fixture grant")
        progress("revoke")
        _require(ipc.exchange({"kind": "revoke"}, timeout=3) == {"revoked": True}, "revocation failed")
        _require(watcher.store.sessions() == (), "revocation left permission state")
        permission("after_revoke_inert", _COMMAND, "")
        progress("hard_ask")
        _require(classify("git push origin main", str(workspace), shell).name == "T3", "synthetic push must be T3")
        permission("synthetic_hard_ask_never_executed", "git push origin main", "")
        _require(len(fixture_ui.reviews) == 2, "T3 reached the fixture permission reviewer")

    progress("session_end")
    ended = _hook({"hook_event_name": "SessionEnd", "session_id": session, "cwd": str(workspace),
                   "reason": "other"}, route="session-end")
    _require(ended["stdout"] == "", "synthetic SessionEnd emitted a decision")
    log.append(session, "demo_fixture_complete", scripted=True, actor="deterministic_test_fixture")
    progress("receipt")
    document = receipt.write(session, home=root / "scope")
    _require(document["counts"] == {"requests": 4, "auto_allowed": 2, "allowed_once": 0, "denied": 0,
                                    "hard_asks": 1, "scopes_granted": 1}, "combined receipt permission counts differ")
    summary = document["understanding"]
    _require(all(len(summary[key]) == count for key, count in {"tasks": 1, "predictions": 2, "executions": 2,
                 "observations": 2, "next_tasks": 1, "dispatches": 1, "revocations": 1}.items()),
             "combined receipt omitted understanding evidence")
    events = log.read(session)
    for record in (before, after):
        order = [event["event"] for event in events if event["fields"].get("check_id") == record["check_id"]]
        _require(order == ["prediction", "probe_approval", "execution", "observation"],
                 "probe evidence order was not prediction then consent then execution")
    _require([event["event"] for event in events].count("session_end") == 1, "synthetic SessionEnd missing")
    return {"mode": "scripted_fixture", "verified": True, "shell": shell, "session_id": session,
            "project": str(workspace), "task_id": task_id, "provenance": "test_fixture",
            "human_answers": False, "host_execution": False, "model_execution": False,
            "external_network_services": False, "native_shell_execution": False,
            "probe_execution": "Two actual bounded caller-side Scope adapter subprocesses; watcher executes neither.",
            "checks": [before, after], "regressions": [initial_tests, final_tests], "patch": patch,
            "handoff": delivered, "delivery_target": "deterministic fixture inbox, actually consumed; no coding host",
            "card": card, "wire_results": wire, "fixture_questions": fixture_ui.questions,
            "fixture_reviews": fixture_ui.reviews, "unused_budget_before_revoke": 1, "revoked": True,
            "session_end": ended, "event_order": [event["event"] for event in events], "receipt": document,
            "meaning": "Scripted fixture evidence only. No human understanding, actual coding-agent repair, native host interception or time saved is claimed."}


def _worker_main():
    stage = "context"

    def progress(value):
        nonlocal stage
        if value not in _STAGES:
            raise RuntimeError("unsupported fixture stage")
        stage = value

    try:
        root = Path(os.environ["_SCOPE_DEMO_ROOT"])
        shell = os.environ["_SCOPE_DEMO_SHELL"]
        if (not root.name.startswith("scope-demo-") or shell not in {"posix", "powershell"}
                or not all(os.environ.get(name) == str(root / child) for name, child in _HOMES.items())
                or "CODEX_THREAD_ID" in os.environ or "SCOPE_LAUNCH_ID" in os.environ):
            raise ValueError("invalid isolated demo context")
        print(json.dumps(_scripted_fixture(root, shell, progress=progress), ensure_ascii=True, allow_nan=False))
        return 0
    except Exception:
        print(f"scope demo: isolated fixture verification failed [{stage}]", file=sys.stderr)
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scope demo", description=__doc__, allow_abbrev=False)
    parser.add_argument("directory", nargs="?", type=Path)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare-only", action="store_true")
    modes.add_argument("--scripted", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--shell", choices=("posix", "powershell"), default="posix",
                        help="permission dialect for the scripted fixture; no native shell is started")
    args = parser.parse_args(argv)
    try:
        if args.scripted and args.directory is not None:
            raise ValueError("--scripted creates its own temporary project; use --prepare-only with a directory")
        result = ((scripted_plan(args.shell) if args.dry_run else run_scripted(args.shell)) if args.scripted else
                  prepare(args.directory or Path("scope-payment-demo"), dry_run=args.dry_run))
        if args.json:
            print(json.dumps(result, ensure_ascii=True, indent=2))
        else:
            from .watch_ui import display_text
            if args.scripted:
                print("SCRIPTED FIXTURE PLAN" if args.dry_run else "SCRIPTED FIXTURE: " + result["session_id"])
                if not args.dry_run:
                    print("Observed charges: 2 before repair, 1 after. Final packaged regressions: 3 passed.")
                    print("One narrow grant, two exact hook allows, explicit revoke and synthetic T3 abstention.")
                    print("Combined receipt captured; temporary project and homes removed.")
            else:
                print(("Preview: " if args.dry_run else "Prepared: ") + display_text(result["project"]))
            print(result["meaning"])
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        from .watch_ui import display_text
        print("scope demo: " + display_text(exc), file=sys.stderr)
        return 1
