"""Prepare a disposable payment retry project; combined rehearsal awaits A2."""

import argparse
from importlib.resources import files
import json
import os
from pathlib import Path
import sys
import uuid

from . import runner

MARKER = ".scope-demo.json"
ASSETS = ("payment.py", "test_payment.py", "README.md")


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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scope demo", description=__doc__, allow_abbrev=False)
    parser.add_argument("directory", nargs="?", type=Path, default=Path("scope-payment-demo"))
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare-only", action="store_true")
    modes.add_argument("--scripted", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.scripted:
        print("scope demo: the combined scripted prediction/consent rehearsal awaits Arjun's published A2 API. "
              "Use --prepare-only for the independent disposable project. No fixture answers or commands were run.", file=sys.stderr)
        return 2
    try:
        result = prepare(args.directory, dry_run=args.dry_run)
        if args.json:
            print(json.dumps(result, ensure_ascii=True, indent=2))
        else:
            from .watch_ui import display_text
            print(("Preview: " if args.dry_run else "Prepared: ") + display_text(result["project"]))
            print(result["meaning"])
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        from .watch_ui import display_text
        print("scope demo: " + display_text(exc), file=sys.stderr)
        return 1
