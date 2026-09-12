"""Understanding commands over the real A1/A2 engine and human watcher seams."""

from __future__ import annotations

import argparse
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import stat
import re
import sys
from typing import Callable

from .watch_ui import display_text

MAX_SKILL_BYTES = 128 * 1024
MAX_SPEC_BYTES = 128 * 1024
SKILL_PARTS = ("skills", "scope-understand", "SKILL.md")


def run_check(argv: list[str]) -> dict:
    """Run a fresh A2 check with independently parsed prediction and consent."""
    from . import experience, learning, ui

    parser = _parser("check")
    args = parser.parse_args(argv)
    task_id = _task(args, parser)
    with args.spec.open("rb") as stream:
        payload = stream.read(MAX_SPEC_BYTES + 1)
    if len(payload) > MAX_SPEC_BYTES:
        raise ValueError("check spec exceeds 128 KiB")
    spec = ui.parse_json(payload.decode("utf-8", errors="strict"), maximum=MAX_SPEC_BYTES)
    task = learning.read_task(args.cwd, task_id)
    record = experience.check(args.cwd, task_id, spec, ask=ui.answer_request,
                              show=ui.show_request, provenance="human_ipc")
    return {**record, "task_id": task_id, "session_id": task["session_id"]}


def run_next(argv: list[str]) -> dict:
    """Select an instruction, or record its native caller's explicit receipt claim."""
    from . import experience, learning, ui

    parser = _parser("next")
    args = parser.parse_args(argv)
    task_id = _task(args, parser)
    if args.acknowledge is not None:
        if args.larger is not None or args.received_sha256 is None:
            parser.error("--acknowledge requires --received-sha256 and cannot include --larger")
        return _acknowledge(args, task_id)
    if args.received_sha256 is not None:
        parser.error("--received-sha256 requires --acknowledge")
    task = learning.read_task(args.cwd, task_id)
    result = experience.choose_next(args.cwd, task_id, smaller=args.smaller, larger=args.larger,
                                    ask=ui.answer_request, show=ui.show_request, provenance="human_ipc")
    return {**result, "task_id": task_id, "session_id": task["session_id"],
            **({"instruction_sha256": _instruction_digest(result["instruction"])} if result["status"] == "selected" else {}),
            "delivery_status": "not_attempted",
            "delivery_reason": "This CLI saves selection only. Printing an instruction is not acknowledged agent delivery."}


def _instruction_digest(instruction: str) -> str:
    return hashlib.sha256(instruction.encode("utf-8")).hexdigest()


def _acknowledge(args, task_id) -> dict:
    """The caller explicitly claims receipt of exact bytes; no host inbox is inferred."""
    from . import experience, host_session, learning, log

    if not re.fullmatch(r"[0-9a-f]{64}", args.received_sha256):
        raise ValueError("--received-sha256 must be exactly 64 lowercase hexadecimal characters")
    launch = host_session.current_launch()
    if launch is None:
        raise ValueError("next-task acknowledgment requires a registered open native Scope launch")
    session = host_session.require_session(host=launch["host"])
    task = learning.read_task(args.cwd, task_id)
    if task["session_id"] != session:
        raise ValueError("task belongs to another native session")
    selected = task["handoffs"].get(args.acknowledge)
    if selected is None or selected["status"] != "selected":
        raise ValueError("handoff is unavailable or acknowledgment was already attempted")
    if _instruction_digest(selected["instruction"]) != args.received_sha256:
        raise ValueError("received instruction digest differs from the selected handoff")
    expected = {**selected, "status": "dispatching"}

    def received(offered):
        # The explicit receiver invocation plus its digest is the acknowledgment.
        # Identity alone, printed output, and a merely selected task are not.
        host_session.require_session(session, host=launch["host"])
        if (offered != expected or type(offered) is not dict
                or _instruction_digest(offered["instruction"]) != args.received_sha256):
            return False
        log.append(session, "host_handoff_acknowledged", task_id=task_id, session=session,
                   handoff_id=offered["handoff_id"], host=launch["host"], launch_id=launch["launch_id"],
                   instruction_sha256=args.received_sha256, provenance="caller_reported",
                   source="scope_next_acknowledge",
                   meaning="Explicit receiver claim; not proof of automatic delivery, execution or completion.")
        return True

    result = experience.dispatch(args.cwd, task_id, args.acknowledge, deliver=received)
    acknowledged = result["status"] == "dispatched"
    return {**result, "task_id": task_id, "session_id": session,
            "instruction_sha256": args.received_sha256,
            "delivery_status": "caller_acknowledged" if acknowledged else "not_acknowledged",
            "delivery_provenance": "caller_reported",
            "delivery_reason": ("The registered native caller explicitly reports receipt of this exact instruction. "
                                "This self-reported acknowledgment does not prove automatic delivery, execution or completion."
                                if acknowledged else "The native caller acknowledgment was not recorded; do not automatically retry.")}


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
    elif command in {"checkpoint", "knowledge", "check", "next"}:
        parser.description = {
            "checkpoint": "Capture bounded source changes for this task.",
            "knowledge": "Inspect saved observations against current source evidence.",
            "check": "Save a human prediction, separately request consent, then run one caller-side probe. Requires scope watch.",
            "next": "Ask for a next-task choice, or explicitly acknowledge receipt of its exact instruction from the current native host.",
        }[command]
        parser.add_argument("task_id", nargs="?")
        parser.add_argument("--task", dest="task_option", help="task ID (alternative to the positional argument)")
        if command == "checkpoint":
            parser.add_argument("--note", default="")
        elif command == "check":
            parser.add_argument("--spec", required=True, type=Path,
                                help="UTF-8 JSON file: question, citations, field, argv; optional shell and timeout. File is read relative to the calling directory.")
        elif command == "next":
            operation = parser.add_mutually_exclusive_group(required=True)
            operation.add_argument("--smaller", help="complete smaller next-task instruction")
            operation.add_argument("--acknowledge", metavar="HANDOFF", help="current native caller reports receipt of an already selected instruction")
            parser.add_argument("--larger", help="optional complete larger next-task instruction")
            parser.add_argument("--received-sha256", metavar="DIGEST", help="exact instruction's SHA-256; required with --acknowledge")
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
    elif command == "check":
        print("Check " + display_text(result["check_id"]) + ": " + display_text(result["phase"]))
        if result["phase"] == "completed":
            observation = result["observation"]
            print("Observation: " + display_text(observation["status"]))
            print(display_text(observation["reason"]))
        else:
            print(display_text(result.get("reason", "No verified observation was recorded.")))
        print("Session " + display_text(result["session_id"]) + "; inspect scope knowledge --json for saved evidence.")
        print("A completed check is a recorded comparison, not proof of repair, understanding or permission.")
    elif command == "next":
        status = "caller_acknowledged" if result["delivery_status"] == "caller_acknowledged" else result["status"]
        print("Next task: " + display_text(status))
        if result["status"] == "selected":
            print("Saved " + display_text(result["choice"]) + " choice " + display_text(result["handoff_id"]))
            print("Instruction (untrusted): " + display_text(result["instruction"]))
            print("Instruction SHA-256: " + result["instruction_sha256"])
        else:
            print(display_text(result["reason"]))
        print(display_text(result["delivery_reason"]))
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
    try:
        parser = _parser(command)
    except ValueError:
        print("scope understanding: unknown command", file=sys.stderr)
        return 1
    args = parser.parse_args(trailing)
    try:
        if command == "check":
            result = run_check(trailing)
        elif command == "next":
            result = run_next(trailing)
        elif command == "skill":
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
        if command == "check":
            return 0 if (result["phase"] == "completed" and
                         result["observation"]["status"] in {"matched", "mismatched"}) else 1
        if command == "next" and result["delivery_status"] == "not_acknowledged":
            return 1
        return 0 if command != "revoke" or result["revoked"] is True and result["recorded"] else 1
    except Exception as error:
        print("scope " + command + ": " + display_text(str(error)), file=sys.stderr)
        return 1
