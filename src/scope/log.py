"""Shared UTF-8 JSONL event storage.

Records are {ts, event, fields}; fields is a nested object. Writers of a shared
session must coordinate across processes when their workflow requires ordering.
"""

from datetime import datetime, timezone
import json

from .paths import session_log


def append(session_id: str, event: str, **fields: object) -> None:
    if not isinstance(event, str) or not event:
        raise ValueError("event must be a nonempty string")
    record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, "fields": fields}
    # Serialize before touching disk, including rejection of non-JSON floats.
    line = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
    path = session_log(session_id)
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
