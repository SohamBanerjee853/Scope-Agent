#!/usr/bin/env python3
"""Verify the installed payment fixture and combined offline A2/A3 rehearsal.

After ``uv sync --locked --no-editable``, run
``uv run --no-sync python -I scripts/check-demo.py``. All project files and
application homes are disposable. Only the bundled fixture and its exact known
repair execute; this script accepts no project path or human answers.
"""

import argparse
import hashlib
from importlib import metadata, resources
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
import uuid
from xml.etree import ElementTree


MAX_OUTPUT_BYTES = 256 * 1024
FIXTURE_TESTS = {
    "test_retry_after_lost_acknowledgement_charges_one_order_once",
    "test_fake_service_deduplicates_an_identical_idempotency_key",
    "test_distinct_orders_have_distinct_charges",
}
RETRY_TEST = "test_retry_after_lost_acknowledgement_charges_one_order_once"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def installed_package():
    import scope

    package_path = Path(scope.__file__).resolve()
    site_paths = {Path(sysconfig.get_path(name)).resolve() for name in ("purelib", "platlib")}
    require(any(package_path.is_relative_to(path) for path in site_paths),
            "Scope must be imported from this interpreter's installed site-packages")
    distribution = metadata.distribution("scope-agent")
    require(package_path == Path(distribution.locate_file("scope/__init__.py")).resolve(),
            "Imported Scope does not match the installed distribution metadata")
    direct_url = distribution.read_text("direct_url.json")
    require(direct_url is None or json.loads(direct_url).get("dir_info", {}).get("editable") is not True,
            "Scope must be installed without editable sources")
    require(any(entry.group == "console_scripts" and entry.name == "scope"
                and entry.value == "scope.cli:main" for entry in distribution.entry_points),
            "Installed Scope CLI entry point is missing")
    installed_files = {str(path) for path in distribution.files or ()}
    names = ("assets/demo/payment.py", "assets/demo/test_payment.py", "assets/demo/README.md",
             "skills/scope-understand/SKILL.md")
    payloads = {}
    for name in names:
        require("scope/" + name in installed_files, "Fixture resource missing from installed metadata: " + name)
        with resources.files("scope").joinpath(*name.split("/")).open("rb") as stream:
            payload = stream.read(MAX_OUTPUT_BYTES + 1)
        require(0 < len(payload) <= MAX_OUTPUT_BYTES, "Installed fixture resource has an invalid size")
        payloads[name] = payload
    return {"distribution": distribution.metadata["Name"], "version": distribution.version,
            "noneditable": True, "package_path": str(package_path)}, payloads


def fixture_environment(root):
    # Keep OS executable discovery, but remove caller-specific host identities and
    # Python/pytest/Git selectors. User configuration is isolated even for Git
    # commands whose production helpers intentionally remove GIT_* overrides.
    prefixes = ("SCOPE_", "CODEX_", "CLAUDE", "GIT_", "PYTHON", "PYTEST_")
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(prefixes)}
    for key, name in (("HOME", "user"), ("USERPROFILE", "user"),
                      ("XDG_CONFIG_HOME", "config"), ("APPDATA", "appdata"),
                      ("LOCALAPPDATA", "localappdata"), ("SCOPE_HOME", "scope"),
                      ("CODEX_HOME", "codex"), ("CLAUDE_CONFIG_DIR", "claude")):
        destination = root / "homes" / name
        destination.mkdir(parents=True, exist_ok=True)
        environment[key] = str(destination)
    environment.update({"SCOPE_POPUP": "0", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                        "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
                        "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
                        "GIT_CONFIG_GLOBAL": os.devnull})
    return environment


def run(arguments, *, cwd, environment, expected_exit=0, timeout=30):
    # File-backed capture keeps child output out of terminal logs and memory.
    # These commands execute only fixed installed code and generated fixture data.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        completed = subprocess.run([sys.executable, "-I", "-B", *arguments], cwd=cwd,
                                   env=environment, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, timeout=timeout, check=False)
        stdout.seek(0)
        stderr.seek(0)
        output = stdout.read(MAX_OUTPUT_BYTES + 1)
        diagnostic = stderr.read(MAX_OUTPUT_BYTES + 1)
    require(len(output) <= MAX_OUTPUT_BYTES and len(diagnostic) <= MAX_OUTPUT_BYTES,
            "Offline fixture child output exceeded its bound")
    require(completed.returncode == expected_exit,
            f"Offline fixture child exited {completed.returncode}, expected {expected_exit}: "
            + diagnostic.decode("utf-8", errors="replace")[:2000])
    return output


def cli(arguments, *, cwd, environment, timeout=30):
    output = run(["-m", "scope", *arguments], cwd=cwd, environment=environment, timeout=timeout)
    result = json.loads(output)
    require(isinstance(result, dict), "Offline fixture CLI returned a non-object result")
    return result


def regression(project, root, environment, *, repaired):
    report = root / ("repaired.xml" if repaired else "initial.xml")
    run(["-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short",
         "--confcutdir", str(project), "--rootdir", str(project), "-c", str(project / "pytest.ini"),
         "--junitxml", str(report), str(project / "test_payment.py")],
        cwd=project, environment=environment, expected_exit=0 if repaired else 1)
    with report.open("rb") as stream:
        xml = stream.read(MAX_OUTPUT_BYTES + 1)
    require(len(xml) <= MAX_OUTPUT_BYTES, "Offline fixture regression report exceeded its bound")
    cases = ElementTree.fromstring(xml).findall(".//testcase")
    require(len(cases) == 3 and {case.get("name") for case in cases} == FIXTURE_TESTS,
            "Packaged regression did not run exactly its three intended cases")
    require(all(case.find("error") is None and case.find("skipped") is None for case in cases),
            "Packaged regression had errors or skipped cases")
    failures = {case.get("name") for case in cases if case.find("failure") is not None}
    require(failures == (set() if repaired else {RETRY_TEST}),
            "Packaged regression did not expose the retry bug and verify the stable-key repair")
    return {"passed": len(cases) - len(failures), "failed": len(failures), "skipped": 0,
            "failure_expected": not repaired}


def combined_rehearsals(root, environment):
    results = []
    sessions = set()
    expected_counts = {"requests": 4, "auto_allowed": 2, "allowed_once": 0,
                       "denied": 0, "hard_asks": 1, "scopes_granted": 1}
    expected_evidence = {"tasks": 1, "predictions": 2, "executions": 2,
                         "observations": 2, "next_tasks": 1, "dispatches": 1}
    for shell in ("posix", "powershell"):
        report = cli(["demo", "--scripted", "--shell", shell, "--json"], cwd=root,
                     environment=environment, timeout=120)
        require(report.get("mode") == "scripted_fixture" and report.get("verified") is True
                and report.get("provenance") == "test_fixture" and report.get("shell") == shell,
                "Installed combined rehearsal did not verify as an explicit fixture")
        session = report.get("session_id")
        require(isinstance(session, str) and session.startswith("demo-") and session not in sessions,
                "Combined rehearsals reused or omitted a fresh demo identity")
        uuid.UUID(session.removeprefix("demo-"))
        sessions.add(session)
        require(report.get("temporary_files_removed") is True and report.get("revoked") is True,
                "Combined rehearsal retained temporary state or unrevoked grants")
        require(report["receipt"]["counts"] == expected_counts,
                "Combined receipt does not match actual permission fixture counts")
        understanding = report["receipt"]["understanding"]
        require(all(len(understanding[key]) == count for key, count in expected_evidence.items()),
                "Combined receipt is missing actual understanding evidence")
        checks = report["checks"]
        require(len(checks) == 2 and all(record["phase"] == "completed"
                and record["prediction"]["provenance"] == "test_fixture"
                and record["approval"]["approved"] is True
                and record["observation"]["status"] in {"matched", "mismatched"}
                for record in checks), "Combined checks lack completed, separately approved fixture evidence")
        observed = [record["observation"]["actual"] for record in checks]
        require(all(type(value) is int for value in observed) and observed == [2, 1],
                "Combined engine did not capture the actual duplicate-charge repair")
        regressions = [{key: item[key] for key in ("passed", "failed")} for item in report["regressions"]]
        require(regressions == [{"passed": 2, "failed": 1}, {"passed": 3, "failed": 0}],
                "Combined rehearsal did not verify actual packaged regressions")
        require(report["patch"]["inbox_consumed"] is True and report["handoff"]["status"] == "dispatched",
                "Fixture repair did not consume its acknowledged fixture-inbox handoff")
        results.append({"shell": shell, "session_id": session, "observed_charge_counts": observed,
                        "regressions": regressions, "permission_counts": expected_counts,
                        "understanding_counts": {key: len(understanding[key]) for key in expected_evidence},
                        "delivery": "deterministic fixture inbox, not a coding host",
                        "revoked": True, "temporary_files_removed": True})
    return results


def verify():
    package, payloads = installed_package()
    from scope.demo_agent import BUGGY_KEY, FIXED_KEY

    bundled = payloads["assets/demo/payment.py"]
    require(bundled.count(BUGGY_KEY.encode()) == 1, "Bundled retry repair location changed")
    fixed = bundled.replace(BUGGY_KEY.encode(), FIXED_KEY.encode(), 1)
    with tempfile.TemporaryDirectory(prefix="scope-installed-demo-") as temporary:
        # Resolve only the newly owned temporary directory, including Windows
        # short-path aliases, before production link/cwd validation sees it.
        root = Path(temporary).resolve(strict=True)
        project = root / "payment-fixture"
        environment = fixture_environment(root)
        prepared = cli(["demo", "--prepare-only", str(project), "--json"], cwd=root, environment=environment)
        require(prepared.get("mode") == "prepare_only" and prepared.get("prepared") is True,
                "Installed demo CLI did not prepare its fixture")
        session = prepared.get("session_id")
        require(isinstance(session, str) and session.startswith("demo-"), "Missing fresh fixture session")
        uuid.UUID(session.removeprefix("demo-"))
        require((project / ".git").is_dir(), "Installed demo CLI did not initialize its disposable Git project")
        for name, data in payloads.items():
            destination = (project / Path(name).name if name.startswith("assets/") else
                           project / ".agents/skills/scope-understand/SKILL.md")
            require(destination.read_bytes() == data, "Prepared fixture differs from its installed resource: " + name)
        require(not any(Path(environment["SCOPE_HOME"]).iterdir()),
                "Preparation unexpectedly recorded workflow evidence")
        task = cli(["start", "OFFLINE FIXTURE: inspect the known payment retry bug", "--session", session,
                    "-C", str(project), "--json"], cwd=root, environment=environment)
        require(task.get("session_id") == session and task.get("observations") == [],
                "A1 start did not preserve fixture identity and empty observations")
        require(task["source"]["files"]["payment.py"]["sha256"] == hashlib.sha256(bundled).hexdigest(),
                "A1 start did not capture the bundled source bytes")
        before = cli(["demo-adapter", "probe", "-C", str(project)], cwd=root, environment=environment)
        require(before.get("charge_count") == 2 and before.get("charged_cents") == 200
                and before.get("lost_acknowledgements") == 1
                and before.get("attempt_keys") == ["demo-order-attempt-1", "demo-order-attempt-2"],
                "Installed adapter did not observe the intentional duplicate charge")
        initial_tests = regression(project, root, environment, repaired=False)
        (project / "payment.py").write_bytes(fixed)
        checkpoint = cli(["checkpoint", task["task_id"], "--note", "OFFLINE FIXTURE: apply the exact stable-key repair",
                          "-C", str(project), "--json"], cwd=root, environment=environment)
        require(checkpoint["changes"]["modified"] == ["payment.py"]
                and checkpoint["changes"]["added"] == [] and checkpoint["changes"]["unavailable"] == [],
                "A1 checkpoint did not identify only the fixture source repair")
        require(checkpoint["source"]["files"]["payment.py"]["sha256"] == hashlib.sha256(fixed).hexdigest(),
                "A1 checkpoint did not capture the actual repaired source bytes")
        after = cli(["demo-adapter", "probe", "-C", str(project)], cwd=root, environment=environment)
        require(after.get("charge_count") == 1 and after.get("charged_cents") == 100
                and after.get("lost_acknowledgements") == 1
                and after.get("attempt_keys") == ["demo-order", "demo-order"],
                "Installed adapter did not observe the stable-key repair")
        repaired_tests = regression(project, root, environment, repaired=True)
        knowledge = cli(["knowledge", task["task_id"], "-C", str(project), "--json"],
                        cwd=root, environment=environment)
        require(knowledge.get("session_id") == session and knowledge.get("observations") == [],
                "A1 knowledge invented an understanding observation from a fixture command")
        events = json.loads(run(["-c", "import json, sys; from scope import log; print(json.dumps(log.read(sys.argv[1])))",
                                session], cwd=root, environment=environment))
        require([event["event"] for event in events] == ["task_start", "task_checkpoint"],
                "Fixture recorded unexpected prediction, consent, execution or other workflow events")
        result = {"mode": "offline_installed_payment_fixture", "verified": True, "package": package,
                  "session_id": session, "observed_before": before, "observed_after": after,
                  "regression_before": initial_tests, "regression_after": repaired_tests,
                  "a1": {"task_id": task["task_id"], "source_repair_captured": True,
                         "saved_understanding_observations": 0,
                         "events": [event["event"] for event in events]}}
        result["combined_rehearsals"] = combined_rehearsals(root, environment)
    require(not root.exists(), "Offline fixture temporary project or homes remain")
    result["temporary_files_removed"] = True
    result["limits"] = ("Offline fixture only: the local bundled payment code and regression tests actually ran. "
                        "The separate A1 fixture has no saved understanding observations. Combined rehearsals "
                        "exercise A2/A3 with labeled fixture predictions, separate consent, watcher grants and "
                        "fixture-inbox delivery. No human answers, coding-host delivery, model, network service, "
                        "native host interception or launcher was exercised; no human mastery is established.")
    return result


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        print(json.dumps(verify(), ensure_ascii=True, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"mode": "offline_installed_payment_fixture", "verified": False,
                          "error": str(error)[:4000]}, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
