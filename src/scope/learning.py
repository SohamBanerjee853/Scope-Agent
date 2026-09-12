"""Task/source state. Understanding evidence never creates permission state."""

from copy import deepcopy
from datetime import datetime
import hashlib
import math
import os
from pathlib import Path
import re
import uuid

from . import log, repository, storage

_PROVENANCE = {"human_terminal", "human_ipc", "test_fixture"}
_CHECK_PHASES = {"prepared", "predicted", "approved", "running", "completed", "skipped", "interrupted"}
_MAX_RECORDS = 4096
_MAX_TEXT = 128 * 1024


def resolve_session(session_id: str | None = None, *, task_id: str | None = None) -> str:
    """One integration seam for future native host-session registration."""
    if os.environ.get("SCOPE_LAUNCH_ID"):
        raise storage.StateError("native launch identity is not implemented until I1L")
    value = session_id if session_id is not None else os.environ.get("CODEX_THREAD_ID")
    if value is None:
        value = task_id or f"task-{uuid.uuid4()}"
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("session must be a nonempty string of at most 512 characters")
    return value


def _manifest(source: dict) -> dict:
    return {"timestamp": source["timestamp"], "files": {
        name: {key: file[key] for key in ("sha256", "bytes", "line_count")}
        for name, file in source["files"].items()}, "skipped": dict(source["skipped"]),
        "listing_complete": source["listing_complete"], "skipped_overflow": source["skipped_overflow"]}


def _text(value, *, maximum: int | None = None, nonempty: bool = False) -> bool:
    if not isinstance(value, str) or (nonempty and not value.strip()) or (maximum is not None and len(value) > maximum):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _timestamp(value) -> bool:
    if not _text(value, maximum=64, nonempty=True):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def _source_manifest(value: dict) -> None:
    """Validate the saved evidence shape before an update can replace it."""
    if (not isinstance(value, dict) or not _timestamp(value.get("timestamp"))
            or not isinstance(value.get("files"), dict)
            or not isinstance(value.get("skipped"), dict)
            or type(value.get("listing_complete")) is not bool
            or type(value.get("skipped_overflow")) is not int or value["skipped_overflow"] < 0):
        raise storage.StateError("incomplete source manifest; original preserved")
    if len(value["files"]) > repository.MAX_FILES or len(value["skipped"]) > repository.MAX_SKIPPED:
        raise storage.StateError("source evidence exceeds snapshot limits")
    total = 0
    for name, file in value["files"].items():
        if (not _text(name, nonempty=True) or repository._path_reason(name)
                or not isinstance(file, dict) or not isinstance(file.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", file["sha256"])
                or type(file.get("bytes")) is not int or not 0 <= file["bytes"] <= repository.MAX_FILE_BYTES
                or type(file.get("line_count")) is not int or not 0 <= file["line_count"] <= file["bytes"]):
            raise storage.StateError("corrupt source evidence; original preserved")
        total += file["bytes"]
    if total > repository.MAX_TOTAL_BYTES:
        raise storage.StateError("source evidence exceeds snapshot limits")
    if any(not _text(name, nonempty=True) or not _text(reason, nonempty=True)
           for name, reason in value["skipped"].items()):
        raise storage.StateError("incomplete source skip record; original preserved")


def _checkpoint_record(entry: dict, task_id: str) -> None:
    if (not isinstance(entry, dict) or not _text(entry.get("checkpoint_id"), nonempty=True)
            or entry.get("task_id") != task_id or not _timestamp(entry.get("timestamp"))
            or not _text(entry.get("note"), maximum=2000) or not isinstance(entry.get("changes"), dict)):
        raise storage.StateError("incomplete checkpoint record; original preserved")
    _source_manifest(entry.get("source"))
    changes = entry["changes"]
    if (entry["timestamp"] != entry["source"]["timestamp"]
            or any(not isinstance(changes.get(key), list) or any(
                not _text(name, nonempty=True) or repository._path_reason(name) for name in changes[key])
                for key in ("added", "modified"))
            or not isinstance(changes.get("unavailable"), list)
            or not _text(changes.get("diff"))
            or len(changes["diff"].encode("utf-8")) > repository.MAX_DIFF_BYTES
            or type(changes.get("diff_truncated")) is not bool):
        raise storage.StateError("corrupt checkpoint changes; original preserved")
    for item in changes["unavailable"]:
        if (not isinstance(item, dict) or not _text(item.get("path"), nonempty=True)
                or repository._path_reason(item["path"]) or not _text(item.get("reason"), nonempty=True)):
            raise storage.StateError("incomplete unavailable-source record; original preserved")


def _record(value, required: set[str], optional: set[str] = frozenset()) -> bool:
    return isinstance(value, dict) and required <= value.keys() and value.keys() <= required | optional


def _choice(value, choices: set[str]) -> bool:
    return isinstance(value, str) and value in choices


def _json_value(value) -> bool:
    """Bounded, typed JSON validation without evaluating recorded content."""
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 100_000 or depth > 64:
            return False
        if item is None or type(item) in (bool, int):
            continue
        if type(item) is float:
            if not math.isfinite(item):
                return False
        elif type(item) is str:
            if not _text(item, maximum=_MAX_TEXT):
                return False
        elif type(item) is list:
            if len(item) > 100_000:
                return False
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is dict:
            if len(item) > 100_000 or any(not _text(key, maximum=_MAX_TEXT) for key in item):
                return False
            pending.extend((child, depth + 1) for child in item.values())
        else:
            return False
    return True


def _same_json(left, right) -> bool:
    """Compare validated saved values without collapsing booleans into numbers."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def _argv(value) -> bool:
    return (isinstance(value, list) and 0 < len(value) <= 256
            and all(_text(arg, maximum=_MAX_TEXT) and "\0" not in arg for arg in value)
            and bool(value[0]) and not value[0].lower().endswith((".bat", ".cmd"))
            and sum(len(arg.encode("utf-8")) for arg in value) <= _MAX_TEXT)


def _references(value) -> bool:
    if not isinstance(value, list) or not 0 < len(value) <= repository.MAX_FILES:
        return False
    for ref in value:
        if (not _record(ref, {"path", "start_line", "end_line", "sha256", "citation"})
                or not _text(ref["path"], maximum=_MAX_TEXT, nonempty=True)
                or repository._path_reason(ref["path"])
                or type(ref["start_line"]) is not int or type(ref["end_line"]) is not int
                or not 1 <= ref["start_line"] <= ref["end_line"] <= repository.MAX_FILE_BYTES
                or not _text(ref["sha256"]) or not re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])
                or not _text(ref["citation"], maximum=_MAX_TEXT)):
            return False
        # Validate the citation against its recorded identity, not today's file.
        manifest = {"files": {ref["path"]: {"line_count": ref["end_line"], "sha256": ref["sha256"]}}}
        try:
            if repository.validate_citation(manifest, ref["citation"]) != ref:
                return False
        except ValueError:
            return False
    return True


def _source_status(value) -> bool:
    if (not _record(value, {"status"}, {"files", "reason"})
            or not _choice(value["status"], {"current", "stale", "unverified"})
            or ("reason" in value and not _text(value["reason"], maximum=_MAX_TEXT))):
        return False
    if "files" not in value:
        return True
    return (isinstance(value["files"], list) and len(value["files"]) <= repository.MAX_FILES
            and all(_record(item, {"path", "status", "reason"})
                    and (item["path"] is None or _text(item["path"], maximum=_MAX_TEXT, nonempty=True))
                    and _choice(item["status"], {"current", "stale", "unverified"})
                    and _text(item["reason"], maximum=_MAX_TEXT)
                    for item in value["files"]))


def _execution(value, spec: dict, root: Path) -> bool:
    return (_record(value, {"argv", "cwd", "timestamp", "exit_code", "stdout", "stderr", "timed_out",
                            "stdout_truncated", "stderr_truncated", "error", "cleanup_incomplete"})
            and _argv(value["argv"]) and value["argv"] == spec["argv"]
            and value["cwd"] == str(root) and _timestamp(value["timestamp"])
            and (value["exit_code"] is None or type(value["exit_code"]) is int)
            and all(_text(value[key], maximum=64 * 1024)
                    and len(value[key].encode("utf-8")) <= 64 * 1024 for key in ("stdout", "stderr"))
            and all(type(value[key]) is bool for key in
                    ("timed_out", "stdout_truncated", "stderr_truncated", "cleanup_incomplete"))
            and (value["error"] is None or _text(value["error"], maximum=_MAX_TEXT)))


def _check_record(value: dict, check_id: str, root: Path) -> None:
    if (not _record(value, {"check_id", "created_at", "phase", "spec", "references"},
                    {"prediction", "approval", "execution", "observation", "reason", "stage"})
            or value["check_id"] != check_id or not _timestamp(value["created_at"])
            or not _choice(value["phase"], _CHECK_PHASES)
            or not _references(value["references"])
            or any(key in value and not _text(value[key], maximum=_MAX_TEXT) for key in ("reason", "stage"))):
        raise storage.StateError("incomplete check record; original preserved")
    spec = value["spec"]
    if (not _record(spec, {"question", "citations", "field", "argv", "shell", "timeout"})
            or any(not _text(spec[key], maximum=_MAX_TEXT, nonempty=True) for key in ("question", "field"))
            or not isinstance(spec["citations"], list)
            or spec["citations"] != [ref["citation"] for ref in value["references"]]
            or not _argv(spec["argv"]) or not _choice(spec["shell"], {"posix", "powershell"})
            or type(spec["timeout"]) not in (int, float)
            or not 0 < spec["timeout"] <= 300 or not math.isfinite(spec["timeout"])):
        raise storage.StateError("corrupt check specification; original preserved")
    if "prediction" in value:
        prediction = value["prediction"]
        if (not _record(prediction, {"value", "reason", "assistance", "provenance", "references", "question", "field", "argv"})
                or not _json_value(prediction["value"])
                or any(not _text(prediction[key], maximum=_MAX_TEXT) for key in ("reason", "assistance"))
                or not _choice(prediction["provenance"], _PROVENANCE)
                or not _references(prediction["references"]) or prediction["references"] != value["references"]
                or any(prediction[key] != spec[key] for key in ("question", "field", "argv"))):
            raise storage.StateError("corrupt saved prediction; original preserved")
    if "approval" in value:
        approval = value["approval"]
        if (not _record(approval, {"approved", "provenance", "reason"})
                or type(approval["approved"]) is not bool
                or not _choice(approval["provenance"], _PROVENANCE)
                or not _text(approval["reason"], maximum=_MAX_TEXT)):
            raise storage.StateError("corrupt probe approval; original preserved")
    if "execution" in value and not _execution(value["execution"], spec, root):
        raise storage.StateError("corrupt probe execution; original preserved")
    if "observation" in value:
        observation = value["observation"]
        if (not _record(observation, {"status", "field", "expected", "reason", "references", "source_status", "execution_timestamp"},
                        {"actual", "check_id"})
                or not _choice(observation["status"], {"matched", "mismatched", "not_verified"})
                or observation["field"] != spec["field"]
                or not _json_value(observation["expected"])
                or ("actual" in observation and not _json_value(observation["actual"]))
                or not _text(observation["reason"], maximum=_MAX_TEXT)
                or not _references(observation["references"]) or observation["references"] != value["references"]
                or not _source_status(observation["source_status"])
                or (observation["execution_timestamp"] is not None and not _timestamp(observation["execution_timestamp"]))
                or ("check_id" in observation and observation["check_id"] != check_id)):
            raise storage.StateError("corrupt probe observation; original preserved")


def _handoff_record(value: dict, handoff_id: str) -> None:
    if (not _record(value, {"handoff_id", "choice", "instruction", "target", "status", "provenance", "created_at"}, {"reason"})
            or value["handoff_id"] != handoff_id or not _timestamp(value["created_at"])
            or not _choice(value["choice"], {"smaller", "larger"})
            or not _text(value["instruction"], maximum=_MAX_TEXT, nonempty=True)
            or value["target"] != "current_agent"
            or not _choice(value["status"], {"selected", "dispatching", "dispatched", "failed"})
            or not _choice(value["provenance"], _PROVENANCE)
            or ("reason" in value and not _text(value["reason"], maximum=_MAX_TEXT))):
        raise storage.StateError("corrupt next-task handoff; original preserved")


def _validate_state(state: dict | None, root: Path) -> dict:
    if state is None:
        return {"schema_version": 1, "project_id": storage.project_id(root), "root": str(root), "tasks": {}}
    if (not isinstance(state, dict) or type(state.get("schema_version")) is not int
            or state["schema_version"] != 1 or state.get("project_id") != storage.project_id(root)
            or state.get("root") != str(root) or not isinstance(state.get("tasks"), dict)):
        raise storage.StateError("incomplete task state; original preserved")
    for key, task in state["tasks"].items():
        if (not _text(key, nonempty=True) or not isinstance(task, dict) or task.get("task_id") != key
                or not _text(task.get("session_id"), maximum=512, nonempty=True)
                or not _text(task.get("description"), maximum=2000, nonempty=True)
                or not _timestamp(task.get("started_at"))
                or not isinstance(task.get("checkpoints"), list)
                or len(task["checkpoints"]) > 5
                or not isinstance(task.get("observations"), list)):
            raise storage.StateError("incomplete task record; original preserved")
        _source_manifest(task.get("baseline"))
        _source_manifest(task.get("source"))
        source = task["source"]
        if (task["started_at"] != task["baseline"]["timestamp"] or source.get("root") != str(root)
                or source.get("untrusted_source") is not True
                or type(source.get("total_bytes")) is not int
                or source["total_bytes"] != sum(file["bytes"] for file in source["files"].values())
                or not isinstance(source.get("limits"), dict)
                or any(type(source["limits"].get(name)) is not int or source["limits"][name] != limit
                       for name, limit in (("file_bytes", repository.MAX_FILE_BYTES),
                                           ("total_bytes", repository.MAX_TOTAL_BYTES), ("files", repository.MAX_FILES)))):
            raise storage.StateError("corrupt source metadata; original preserved")
        for name, file in task["source"]["files"].items():
            if not _text(file.get("text")) or not isinstance(file.get("symbols"), list):
                raise storage.StateError("incomplete source record; original preserved")
            data = file["text"].encode("utf-8")
            if (file["bytes"] != len(data) or file["sha256"] != hashlib.sha256(data).hexdigest()
                    or file["line_count"] != len(repository.source_lines(file["text"]))
                    or file["symbols"] != repository._symbols(file["text"], name)):
                raise storage.StateError("corrupt source evidence; original preserved")
        for entry in task["checkpoints"]:
            _checkpoint_record(entry, key)
        latest = task["checkpoints"][-1]["source"] if task["checkpoints"] else task["baseline"]
        if latest != _manifest(source):
            raise storage.StateError("source and checkpoint evidence disagree; original preserved")
        for collection, validator in (("checks", _check_record), ("handoffs", _handoff_record)):
            if collection not in task:
                continue
            records = task[collection]
            if not isinstance(records, dict) or len(records) > _MAX_RECORDS:
                raise storage.StateError(f"invalid {collection} collection; original preserved")
            for identifier, record in records.items():
                if not _text(identifier, maximum=512, nonempty=True):
                    raise storage.StateError(f"invalid {collection} identity; original preserved")
                if collection == "checks":
                    validator(record, identifier, root)
                else:
                    validator(record, identifier)
        # A1 observations remain historical input. When A2 links a projection
        # to a saved check, both copies must preserve exactly the same evidence.
        for observation in task["observations"]:
            if not isinstance(observation, dict) or not isinstance(observation.get("check_id"), str):
                continue
            check = task.get("checks", {}).get(observation["check_id"])
            if check is not None and (not _json_value(observation)
                                      or not _same_json(observation, check.get("observation"))):
                raise storage.StateError("check and observation evidence disagree; original preserved")
    return state


def read_task(path: str | Path, task_id: str) -> dict:
    """Return independent validated task state without taking a source snapshot."""
    if not _text(task_id, maximum=512, nonempty=True):
        raise ValueError("task_id is required")
    root = repository.find_root(path)
    state = _validate_state(storage.load(root), root)
    task = state["tasks"].get(task_id)
    if task is None:
        raise storage.StateError("task is unavailable; start a task first")
    return deepcopy(task)


def update_task(path: str | Path, task_id: str, change) -> dict:
    """Mutate one task atomically; change must only perform in-memory work.

    The callback's return value is ignored. It must never wait for a person,
    snapshot a repository, run a probe or deliver an instruction while locked.
    """
    if not _text(task_id, maximum=512, nonempty=True):
        raise ValueError("task_id is required")
    if not callable(change):
        raise ValueError("change must be callable")
    root = repository.find_root(path)

    def apply(state):
        state = _validate_state(state, root)
        task = state["tasks"].get(task_id)
        if task is None:
            raise storage.StateError("task is unavailable; start a task first")
        identity = deepcopy({key: task[key] for key in ("task_id", "session_id", "baseline", "started_at")})
        change(task)
        if any(task.get(key) != value for key, value in identity.items()):
            raise storage.StateError("task identity or baseline changed; original preserved")
        return _validate_state(state, root)

    state = storage.update(root, apply)
    return deepcopy(state["tasks"][task_id])


def start(path: str | Path, description: str, *, session_id: str | None = None) -> dict:
    if not isinstance(description, str) or not description.strip() or len(description) > 2000:
        raise ValueError("task description must contain 1–2000 characters")
    source = repository.snapshot(path)
    root = Path(source["root"])
    task_id = f"task-{uuid.uuid4()}"
    session = resolve_session(session_id, task_id=task_id)
    task = {"task_id": task_id, "session_id": session, "description": description,
            "started_at": source["timestamp"], "baseline": _manifest(source),
            "source": source, "checkpoints": [], "observations": [], "checks": {}, "handoffs": {}}

    def change(state):
        state = _validate_state(state, root)
        state["tasks"][task_id] = task
        return state

    storage.update(root, change)
    log.append(session, "task_start", task_id=task_id, session=session,
               project_id=storage.project_id(root), repo=str(root), description=description,
               baseline=task["baseline"])
    return deepcopy(task)


def checkpoint(path: str | Path, task_id: str, *, note: str = "") -> dict:
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id is required")
    if not isinstance(note, str) or len(note) > 2000:
        raise ValueError("checkpoint note must be at most 2000 characters")
    root = repository.find_root(path)
    captured = {}

    def change(state):
        state = _validate_state(state, root)
        task = state["tasks"].get(task_id)
        if task is None:
            raise storage.StateError("task is unavailable; start a task first")
        source = repository.snapshot(root)
        entry = {"checkpoint_id": f"checkpoint-{uuid.uuid4()}", "task_id": task_id,
                 "timestamp": source["timestamp"], "note": note,
                 "changes": repository.changes(task["source"], source), "source": _manifest(source)}
        task["source"] = source
        task["checkpoints"] = [*task["checkpoints"], entry][-5:]
        captured.update({"entry": entry, "session_id": task["session_id"]})
        return state

    storage.update(root, change)
    log.append(captured["session_id"], "task_checkpoint", task_id=task_id,
               session=captured["session_id"], checkpoint=captured["entry"])
    return deepcopy(captured["entry"])


def version_observation(observation: dict, source: dict) -> dict:
    """Historical results are retained; missing/changed references prevent reuse."""
    if not isinstance(observation, dict):
        return {"status": "not_verified", "current_status": "not_verified",
                "source_status": {"status": "unverified", "reason": "invalid observation"}}
    result = deepcopy(observation)
    evidence = repository.evidence_status(observation.get("references"), source)
    result["source_status"] = evidence
    recorded = observation.get("status", "not_verified")
    result["current_status"] = recorded if (evidence["status"] == "current"
        and isinstance(recorded, str) and recorded in {"matched", "mismatched"}) else "not_verified"
    return result


def knowledge(path: str | Path, task_id: str) -> dict:
    root = repository.find_root(path)
    task = read_task(root, task_id)
    source = repository.snapshot(root)
    return {"task_id": task_id, "session_id": task["session_id"],
            "description": task["description"], "source": _manifest(source),
            "checkpoints": deepcopy(task["checkpoints"]),
            "checks": deepcopy(task.get("checks", {})), "handoffs": deepcopy(task.get("handoffs", {})),
            "observations": [version_observation(item, source) for item in task["observations"]],
            "meaning": "Debugging evidence applies only to the referenced source version; it is not a mastery score or permission."}
