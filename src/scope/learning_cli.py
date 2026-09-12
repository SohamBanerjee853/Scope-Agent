"""Understanding commands over the established A1 and human transport seams.

A2 owns prediction/consent/comparison and next-task selection. Its callable
signatures are not yet available on the inspected Arjun branch. ``run_check``
and ``run_next`` are explicit adapters awaiting that handoff, not substitute
implementations. They must eventually pass human ask/show callbacks to the real
A2 engine and leave probe execution in that caller-side engine.
"""

from __future__ import annotations

import argparse
from importlib.resources import files
import json
from pathlib import Path
import stat
import sys
from typing import Callable

from .watch_ui import display_text

MAX_SKILL_BYTES = 128 * 1024
SKILL_PARTS = ("skills", "scope-understand", "SKILL.md")
A2_ADAPTER_REQUIREMENTS = (
    "Await Arjun's tested experience.py check and next-task callable signatures, "
    "their request/result schemas, and ask/show callback contracts. The adapter "
    "must preserve prediction-before-consent ordering, caller-side execution, "
    "T3 rejection, distinct provenance and cancellation. No engine API is guessed."
)


class DependencyUnavailable(RuntimeError):
    """A required milestone has not supplied its reviewed callable contract."""


def run_check(argv: list[str]) -> dict:
    """A3 adapter entry point; wire only after A2's real check API is supplied."""
    raise DependencyUnavailable("check is pending Arjun's A2 prediction/consent API")


def run_next(argv: list[str]) -> dict:
    """A3 adapter entry point; wire only after A2's real next-task API is supplied."""
    raise DependencyUnavailable("next is pending Arjun's A2 next-task API")


def skill_text() -> str:
    resource = files("scope").joinpath(*SKILL_PARTS)
    with resource.open("rb") as stream:
        payload = stream.read(MAX_SKILL_BYTES + 1)
    if len(payload) > MAX_SKILL_BYTES:
        raise ValueError("packaged understanding skill is too large")
    return payload.decode("utf-8", errors="strict")


def _skill_destination(project) -> Path:
    root = Path(project).expanduser().absolute()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("skill project must be an existing directory without a symlink")
    current = root
    for component in (".agents", "skills", "scope-understand"):
        current = current / component
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if (not stat.S_ISDIR(info.st_mode) or current.is_symlink()
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("understanding skill destination contains a link or non-directory")
    return current / "SKILL.md"


def install_skill(project, *, dry_run: bool = False) -> dict:
    """Install this project's bundled guidance without overwriting local edits."""
    from .install import _read, _replace, _write_lock

    payload = skill_text().encode("utf-8")

    def prepare():
        destination = _skill_destination(project)
        previous = _read(destination, MAX_SKILL_BYTES)
        if previous is not None and previous != payload:
            raise ValueError("understanding skill has local edits; existing file preserved")
        return destination, {"target": str(destination), "changed": previous is None,
                             "dry_run": dry_run,
                             "notice": "Understanding guidance does not grant permission or replace execution consent."}

    destination, report = prepare()
    if dry_run or not report["changed"]:
        return report
    with _write_lock(destination):
        destination, report = prepare()
        if report["changed"]:
            _replace(destination, payload, None)
    return report


def revoke(session_id: str | None = None, *, exchange: Callable | None = None) -> dict:
    """Clear watcher decisions, recording the actual outcome without erasing knowledge."""
    from . import ipc, learning, log

    session = learning.resolve_session(session_id)
    transport = ipc.exchange if exchange is None else exchange
    try:
        reply = transport({"kind": "revoke"})
    except Exception:
        reply = None
    revoked = (reply["revoked"] if isinstance(reply, dict) and set(reply) == {"revoked"}
               and type(reply["revoked"]) is bool else None)
    reason = ("watcher confirmed revocation" if revoked is True else
              "watcher declined revocation" if revoked is False else
              "watcher unavailable; revocation not confirmed")
    result = {"session_id": session, "revoked": revoked, "recorded": False,
              "reason": reason, "meaning": "Revocation clears permission grants and pending answers; saved debugging evidence is retained."}
    try:
        log.append(session, "scopes_revoked", session=session, revoked=revoked,
                   reason=reason, source="understanding_cli")
        result["recorded"] = True
    except Exception:
        result["error"] = "revocation outcome could not be recorded"
    return result


def _parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scope " + command, allow_abbrev=False)
    if command == "start":
        parser.description = "Capture source evidence for a debugging task. No probe runs."
        parser.add_argument("description", nargs="+")
        parser.add_argument("--session", help="explicit session; otherwise CODEX_THREAD_ID or a new task ID")
    elif command in {"checkpoint", "knowledge"}:
        parser.description = ("Capture bounded source changes for this task." if command == "checkpoint" else
                              "Inspect saved observations against current source evidence.")
        parser.add_argument("task_id", nargs="?")
        parser.add_argument("--task", dest="task_option", help="task ID (alternative to the positional argument)")
        if command == "checkpoint":
            parser.add_argument("--note", default="")
    elif command == "revoke":
        parser.description = "Clear watcher grants and pending answers; retain saved debugging evidence."
        parser.add_argument("--session")
    elif command == "skill":
        parser.description = "Show or install this project's bundled understanding guidance."
        parser.add_argument("--install", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
    else:
        raise ValueError("unknown understanding command")
    if command != "revoke":
        parser.add_argument("-C", "--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true", help="print the complete structured result")
    return parser


def _task(args, parser):
    if bool(args.task_id) == bool(args.task_option):
        parser.error("supply exactly one task ID, positionally or with --task")
    return args.task_id or args.task_option


def _display(command, result):
    if command == "start":
        print("Started task " + display_text(result["task_id"]) + " in session " + display_text(result["session_id"]))
        print(f"Captured {len(result['source']['files'])} source files. No prediction, consent or probe has occurred.")
    elif command == "checkpoint":
        changes = result["changes"]
        print("Saved checkpoint " + display_text(result["checkpoint_id"]))
        print(f"Added evidence: {len(changes['added'])}; changed: {len(changes['modified'])}; unavailable: {len(changes['unavailable'])}.")
    elif command == "knowledge":
        print("Task " + display_text(result["task_id"]) + ": " + str(len(result["observations"])) + " saved observations")
        for observation in result["observations"]:
            print("  " + display_text(observation.get("current_status", "not_verified")) + ": " +
                  display_text(observation.get("reason", "inspect --json for evidence")))
        print(display_text(result["meaning"]))
    elif command == "revoke":
        print(display_text(result["reason"]))
        print(display_text(result["meaning"]))
        if not result["recorded"]:
            print(display_text(result["error"]), file=sys.stderr)
    elif command == "skill":
        action = "Would install" if result["dry_run"] and result["changed"] else "Installed" if result["changed"] else "Already installed"
        print(action + " " + display_text(result["target"]))
        print(display_text(result["notice"]))


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print("scope understanding: command required", file=sys.stderr)
        return 1
    command, trailing = arguments[0], arguments[1:]
    if command in {"check", "next"}:
        if trailing == ["--help"] or trailing == ["-h"]:
            print("scope " + command + ": " + A2_ADAPTER_REQUIREMENTS)
            return 0
        try:
            (run_check if command == "check" else run_next)(trailing)
        except DependencyUnavailable as error:
            print("scope " + command + ": " + str(error), file=sys.stderr)
            return 1
        raise RuntimeError("A2 adapter returned without an implemented CLI result contract")
    try:
        parser = _parser(command)
    except ValueError:
        print("scope understanding: unknown command", file=sys.stderr)
        return 1
    args = parser.parse_args(trailing)
    try:
        if command == "skill":
            if args.dry_run and not args.install:
                parser.error("--dry-run requires --install")
            if not args.install:
                text = skill_text()
                if args.json:
                    print(json.dumps({"skill": text}, ensure_ascii=True))
                else:
                    sys.stdout.write(text)
                return 0
            result = install_skill(args.cwd, dry_run=args.dry_run)
        elif command == "revoke":
            result = revoke(args.session)
        else:
            from . import learning

            if command == "start":
                result = learning.start(args.cwd, " ".join(args.description), session_id=args.session)
            elif command == "checkpoint":
                result = learning.checkpoint(args.cwd, _task(args, parser), note=args.note)
            else:
                result = learning.knowledge(args.cwd, _task(args, parser))
        if args.json:
            print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        else:
            _display(command, result)
        return 0 if command != "revoke" or result["revoked"] is True and result["recorded"] else 1
    except Exception as error:
        print("scope " + command + ": " + display_text(str(error)), file=sys.stderr)
        return 1
