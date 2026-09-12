"""Task/source state. Understanding evidence never creates permission state."""

from copy import deepcopy
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import uuid

from . import log, repository, storage


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


def _validate_state(state: dict | None, root: Path) -> dict:
    if state is None:
        return {"schema_version": 1, "project_id": storage.project_id(root), "root": str(root), "tasks": {}}
    if state.get("root") != str(root) or not isinstance(state.get("tasks"), dict):
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
    return state


def start(path: str | Path, description: str, *, session_id: str | None = None) -> dict:
    if not isinstance(description, str) or not description.strip() or len(description) > 2000:
        raise ValueError("task description must contain 1–2000 characters")
    source = repository.snapshot(path)
    root = Path(source["root"])
    task_id = f"task-{uuid.uuid4()}"
    session = resolve_session(session_id, task_id=task_id)
    task = {"task_id": task_id, "session_id": session, "description": description,
            "started_at": source["timestamp"], "baseline": _manifest(source),
            "source": source, "checkpoints": [], "observations": []}

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
    state = _validate_state(storage.load(root), root)
    task = state["tasks"].get(task_id)
    if task is None:
        raise storage.StateError("task is unavailable")
    source = repository.snapshot(root)
    return {"task_id": task_id, "session_id": task["session_id"],
            "observations": [version_observation(item, source) for item in task["observations"]],
            "meaning": "Debugging evidence applies only to the referenced source version; it is not a mastery score or permission."}
