"""Task/source state. Understanding evidence never creates permission state."""

from copy import deepcopy
import hashlib
import os
from pathlib import Path
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


def _validate_state(state: dict | None, root: Path) -> dict:
    if state is None:
        return {"schema_version": 1, "project_id": storage.project_id(root), "root": str(root), "tasks": {}}
    if state.get("root") != str(root) or not isinstance(state.get("tasks"), dict):
        raise storage.StateError("incomplete task state; original preserved")
    for key, task in state["tasks"].items():
        if (not isinstance(task, dict) or task.get("task_id") != key
                or not isinstance(task.get("session_id"), str) or not task["session_id"]
                or not isinstance(task.get("baseline"), dict)
                or not isinstance(task.get("source"), dict)
                or not isinstance(task["source"].get("files"), dict)
                or not isinstance(task.get("checkpoints"), list)
                or not isinstance(task.get("observations"), list)):
            raise storage.StateError("incomplete task record; original preserved")
        for name, file in task["source"]["files"].items():
            if (not isinstance(name, str) or not isinstance(file, dict)
                    or not isinstance(file.get("sha256"), str)
                    or not isinstance(file.get("text"), str)
                    or type(file.get("line_count")) is not int):
                raise storage.StateError("incomplete source record; original preserved")
            data = file["text"].encode("utf-8")
            if (repository._path_reason(name) or len(data) > repository.MAX_FILE_BYTES
                    or file["sha256"] != hashlib.sha256(data).hexdigest()
                    or file["line_count"] != len(file["text"].splitlines())):
                raise storage.StateError("corrupt source evidence; original preserved")
        if (len(task["source"]["files"]) > repository.MAX_FILES
                or sum(len(file["text"].encode("utf-8")) for file in task["source"]["files"].values()) > repository.MAX_TOTAL_BYTES):
            raise storage.StateError("source evidence exceeds snapshot limits")
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
    result["current_status"] = recorded if evidence["status"] == "current" and recorded in {"matched", "mismatched"} else "not_verified"
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
            "meaning": "Evidence applies only to the referenced source version; it is not a mastery score or permission."}
