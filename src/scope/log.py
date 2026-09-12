"""Append-only UTF-8 event records; incomplete input is never evidence."""

from datetime import datetime, timezone
import json
import os

from .paths import session_log

MAX_LOG_BYTES = 32 * 1024 * 1024
MAX_RECORD_BYTES = 1024 * 1024


def append(session_id: str, event: str, **fields) -> dict:
    if not isinstance(event, str) or not event:
        raise ValueError("event must be a nonempty string")
    if "ts" in fields:
        raise ValueError("ts is supplied by the event logger")
    record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    data = (json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError("event exceeds the 1 MiB record limit")
    path = session_log(session_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("incomplete event write")
    finally:
        os.close(fd)
    return record


def read(session_id: str) -> list[dict]:
    try:
        with session_log(session_id).open("rb") as stream:
            data = stream.read(MAX_LOG_BYTES)
    except FileNotFoundError:
        return []
    records = []
    for line in data.splitlines(keepends=True):
        if not line.endswith(b"\n") or len(line) > MAX_RECORD_BYTES:
            continue
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(record, dict) and isinstance(record.get("ts"), str) and isinstance(record.get("event"), str):
            records.append(record)
    return records
