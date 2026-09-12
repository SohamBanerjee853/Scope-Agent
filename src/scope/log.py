"""Shared UTF-8 JSONL event storage.

Records are {ts, event, fields}; fields is a nested object. Writers of a shared
session must coordinate across processes when their workflow requires ordering.
"""

from datetime import datetime, timezone
import json
from pathlib import Path

from .paths import session_log


def append(session_id: str, event: str, **fields: object) -> None:
    _append_to(session_log(session_id), event, fields)


def append_at(home, /, session_id: str, event: str, **fields: object) -> None:
    """Append in an explicit run home without mutating process environment.

    The legacy append signature retains arbitrary event fields, including home.
    Consumers still derive canonical session filenames through session_log.
    """
    path = Path(home).expanduser() / "sessions" / session_log(session_id).name
    _append_to(path, event, fields)


def _append_to(path, event, fields):
    if not isinstance(event, str) or not event:
        raise ValueError("event must be a nonempty string")
    record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, "fields": fields}
    # Serialize before touching disk, including rejection of non-JSON floats.
    line = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(line.encode("utf-8"))


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def read(session_id: str) -> list[dict]:
    path = session_log(session_id)
    try:
        stream = path.open("rb")
    except FileNotFoundError:
        return []
    records = []
    with stream:
        for line in stream:
            if not line.endswith(b"\n"):
                continue  # An incomplete final append is not evidence.
            try:
                record = json.loads(line, parse_constant=_invalid_constant)
            except (ValueError, UnicodeError):
                continue
            if (isinstance(record, dict)
                    and isinstance(record.get("ts"), str)
                    and isinstance(record.get("event"), str)
                    and record["event"]
                    and isinstance(record.get("fields"), dict)):
                records.append(record)
    return records
