from datetime import datetime
import json

import pytest

from scope import log, paths


def test_missing_read_has_no_side_effects():
    assert log.read("missing") == []
    assert not paths.scope_home().exists()


def test_roundtrip_order_utf8_and_reserved_fields():
    log.append("test", "prediction", answer="café 🧪", ts="untrusted", approved=False)
    log.append("test", "observation", value={"count": 2})
    records = log.read("test")
    assert [r["event"] for r in records] == ["prediction", "observation"]
    assert datetime.fromisoformat(records[0]["ts"]).utcoffset().total_seconds() == 0
    assert records[0]["fields"] == {"answer": "café 🧪", "ts": "untrusted", "approved": False}
    assert records[1]["fields"] == {"value": {"count": 2}}
    assert "café" in paths.session_log("test").read_text(encoding="utf-8")


def test_malformed_records_and_incomplete_tail_are_ignored():
    log.append("test", "before")
    path = paths.session_log("test")
    with path.open("ab") as f:
        f.write(b'not json\n\xff\n[]\n{}\n{"ts":"x","event":"x","fields":NaN}\n')
    log.append("test", "after")
    with path.open("ab") as f:
        f.write(json.dumps({"ts": "x", "event": "incomplete", "fields": {}}).encode())
    assert [r["event"] for r in log.read("test")] == ["before", "after"]


@pytest.mark.parametrize("fields", [{"bad": object()}, {"bad": float("nan")}])
def test_invalid_fields_do_not_create_a_log(fields):
    with pytest.raises((TypeError, ValueError)):
        log.append("test", "event", **fields)
    assert not paths.scope_home().exists()


@pytest.mark.parametrize("event", ["", None, 1])
def test_invalid_event(event):
    with pytest.raises(ValueError):
        log.append("test", event)
