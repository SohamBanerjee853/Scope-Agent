#!/usr/bin/env python3
"""Check a noneditable installed Scope package and its offline fixture resources.

Run after ``uv sync --locked --no-editable`` using
``uv run --no-sync python -I scripts/check-package.py``. Package imports and all
resource reads must come from this interpreter's installed distribution.
"""

import argparse
from importlib import metadata, resources
import json
from pathlib import Path
import sys
import sysconfig


RESOURCE_NAMES = (
    "rules/allow.yaml",
    "rules/hard-ask.yaml",
    "skills/scope-permissions/SKILL.md",
    "skills/scope-understand/SKILL.md",
    "assets/demo/payment.py",
    "assets/demo/test_payment.py",
    "assets/demo/README.md",
)
UNDERSTANDING_FIELDS = {
    "tasks", "predictions", "observations", "executions", "next_tasks", "dispatches",
    "skipped_checks", "deferred_tasks", "revocations", "meaning",
}
EXPECTED_COUNTS = {
    "requests": 4, "auto_allowed": 2, "allowed_once": 0,
    "denied": 0, "hard_asks": 1, "scopes_granted": 1,
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_resources():
    import scope
    import yaml
    from scope.understanding_summary import summarize

    package_path = Path(scope.__file__).resolve()
    site_paths = {Path(sysconfig.get_path(name)).resolve() for name in ("purelib", "platlib")}
    source_path = Path(__file__).resolve().parents[1] / "src"
    require(not package_path.is_relative_to(source_path), "Scope was imported from checkout sources")
    require(any(package_path.is_relative_to(path) for path in site_paths),
            "Scope is not installed in the current interpreter's site-packages")
    distribution = metadata.distribution("scope-agent")
    direct_url = distribution.read_text("direct_url.json")
    if direct_url is not None:
        require(json.loads(direct_url).get("dir_info", {}).get("editable") is not True,
                "Scope is installed in editable mode")
    installed_files = {str(path) for path in distribution.files or ()}
    package = resources.files("scope")
    checked = {}
    for name in RESOURCE_NAMES:
        require("scope/" + name in installed_files, f"Resource absent from installed metadata: {name}")
        data = package.joinpath(*name.split("/")).read_bytes()
        text = data.decode("utf-8", errors="strict")
        require(bool(text.strip()), f"Empty installed resource: {name}")
        if name.endswith(".yaml"):
            value = yaml.safe_load(text)
            require(isinstance(value, dict) and value.get("version") == 1,
                    f"Invalid installed policy: {name}")
        elif name.endswith("SKILL.md"):
            parts = text.split("---", 2)
            require(len(parts) == 3 and not parts[0].strip(), f"Missing skill frontmatter: {name}")
            frontmatter = yaml.safe_load(parts[1])
            require(isinstance(frontmatter, dict) and
                    all(isinstance(frontmatter.get(key), str) and frontmatter[key].strip()
                        for key in ("name", "description")), f"Invalid skill frontmatter: {name}")
        elif name.endswith(".py"):
            compile(text, name, "exec")  # Syntax validation; no demo code executes.
        checked[name] = len(data)
    summary = summarize([])
    require(isinstance(summary, dict) and UNDERSTANDING_FIELDS <= summary.keys(),
            "Installed understanding projection does not satisfy the shared contract")
    return {"distribution": distribution.metadata["Name"], "version": distribution.version,
            "package_path": str(package_path), "noneditable": True,
            "resource_bytes": checked, "understanding_projection": "present"}


def check_smoke():
    from scope.smoke import run

    results = []
    sessions = set()
    for shell in ("posix", "powershell"):
        result = run(shell)
        require(result["verified"] is True and result["mode"] == "scripted_fixture",
                f"Installed {shell} fixture did not verify")
        require(result["receipt"]["counts"] == EXPECTED_COUNTS, f"Unexpected {shell} receipt counts")
        require(UNDERSTANDING_FIELDS <= result["receipt"]["understanding"].keys(),
                f"Installed {shell} receipt dropped the understanding projection")
        require(result["temporary_files_removed"] is True, "Fixture temporary homes remain")
        require(result["host_execution"] is False and result["requested_command_execution"] is False,
                "Permission fixture unexpectedly ran a host or requested command")
        require(result["session_id"] not in sessions, "Smoke dialects shared a synthetic session")
        sessions.add(result["session_id"])
        results.append({"shell": shell, "session_id": result["session_id"],
                        "counts": result["receipt"]["counts"], "revoked": result["revoked"],
                        "temporary_files_removed": result["temporary_files_removed"]})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources-only", action="store_true",
                        help="verify installed provenance and resources without running fixtures")
    args = parser.parse_args(argv)
    try:
        result = check_resources()
        if not args.resources_only:
            result["scripted_permission_smoke"] = check_smoke()
        result["limits"] = (
            "Offline fixtures only. No human answers, model calls, live hook trust, native coding-host "
            "execution, or launcher behavior were verified. Demo resources were read and syntax-checked."
        )
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except Exception as error:
        print(f"check-package: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
