"""Receipt evidence regressions, using only synthetic temporary sessions."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scope import log, paths, receipt


def event(name, **fields):
    return {"ts": "2026-09-12T00:00:00+00:00", "event": name, "fields": fields}


def request(request_id="r1", **fields):
    return event("permission_request", request_id=request_id, command="touch fixture",
                 cwd="/fixture", shell="bash", **fields)


def summary(events):
    return {**{key: [] for key in receipt.SUMMARY_FIELDS - {"meaning"}},
            "meaning": "Explicit fixture projection", "observations": [e for e in events if e["event"] == "observation"]}


def persist(events, session="fixture", home=None):
    path = (paths.scope_home() if home is None else home) / "sessions" / paths.session_log(session).name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


def test_correlates_hook_watcher_and_grant_records_without_execution_claim():
    events = [request(tier="T2"), event("permission_review", request_id="r1", command="touch fixture", tier="T2"),
              event("permission_review_decision", request_id="r1", behavior="allow", action="scope_allow"),
              event("permission_decision", request_id="r1", behavior="allow"),
              event("permission_review_delivery", request_id="r1", delivered=True),
              event("grant_created", request_id="r1", grant_id="g1"),
              event("grant_created", request_id="r1", grant_id="g1"),
              request("r2", tier="T3"), event("permission_decision", request_id="r2", behavior=None),
              event("host_connected"), event("client_ready")]
    result = receipt.project("fixture", events, understanding=summary)
    assert result["counts"] == dict(requests=2, auto_allowed=1, allowed_once=0, denied=0, hard_asks=1, scopes_granted=1)
    assert [action["decision"] for action in result["actions"]] == ["allow", "unknown"]
    assert all(action["execution"] == action["native_decision"] == "unknown" for action in result["actions"])


def test_once_and_deny_are_separate_from_automatic_allows():
    events = [request("once", tier="T2"), event("permission_review_decision", request_id="once", behavior="allow", action="allowed_once"),
              event("permission_decision", request_id="once", behavior="allow"), request("deny", tier="T2"),
              event("permission_decision", request_id="deny", behavior="deny")]
    result = receipt.project("fixture", events, understanding=summary)
    assert result["counts"]["allowed_once"] == result["counts"]["denied"] == 1
    assert result["counts"]["auto_allowed"] == 0


def test_missing_conflicting_or_malformed_decisions_are_unknown():
    events = [request("missing"), request("conflict"),
              event("permission_decision", request_id="conflict", behavior="allow"),
              event("permission_decision", request_id="conflict", behavior="deny"),
              request("malformed"), event("permission_decision", request_id="malformed", behavior={"allow": True}),
              event("permission_request", command="touch fixture"),
              event("permission_decision", command="touch fixture", behavior="allow"),
              event("grant_created", grant_id="bad", request_id=[])]
    result = receipt.project("fixture", events, understanding=summary)
    assert result["counts"]["requests"] == 4
    assert result["counts"]["auto_allowed"] == result["counts"]["scopes_granted"] == 0
    assert all(action["decision"] == "unknown" for action in result["actions"])


def test_failed_watcher_delivery_does_not_count_an_allow_or_created_grant():
    events = [request(tier="T2"), event("permission_review_decision", request_id="r1", behavior="allow"),
              event("grant_created", request_id="r1", grant_id="g1"),
              event("permission_review_delivery", request_id="r1", delivered=False)]
    result = receipt.project("fixture", events, understanding=summary)
    assert result["counts"]["auto_allowed"] == result["counts"]["scopes_granted"] == 0
    assert result["actions"][0]["delivery"] == "failed"


def test_understanding_seam_preserves_full_projection(monkeypatch):
    evidence = event("observation", observation={"value": True, "status": "observed"})
    monkeypatch.setitem(sys.modules, "scope.understanding_summary", SimpleNamespace(summarize=summary))
    result = receipt.project("fixture", [evidence])
    assert result["understanding"] == summary([evidence])
    assert result["counts"]["requests"] == 0


def test_missing_projection_is_explicit_and_broken_projection_is_not_dropped(monkeypatch):
    monkeypatch.setitem(sys.modules, "scope.understanding_summary", None)
    assert receipt.project("fixture", [])["understanding"]["status"] == "pending_integration"
    with pytest.raises(ValueError, match="frozen contract"):
        receipt.project("fixture", [], understanding=lambda events: {})
    def broken(events):
        raise RuntimeError("projection failed")
    with pytest.raises(RuntimeError, match="projection failed"):
        receipt.project("fixture", [], understanding=broken)


def test_legacy_schema_one_remains_readable_and_renderable(tmp_path, capsys):
    path = receipt.receipt_path("legacy")
    path.parent.mkdir(parents=True)
    legacy = {"schema_version": 1, "session_id": "legacy", "counts": dict.fromkeys(receipt.COUNTS, 0), "actions": []}
    path.write_text(json.dumps(legacy), encoding="utf-8")
    assert receipt.load(path) == legacy
    assert "absent in this legacy receipt" in receipt.render(legacy)
    assert receipt.main(["legacy", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == legacy


def test_event_content_cache_ignores_receipt_markers_but_tracks_evidence():
    source = persist([request(tier="T1")])
    before = source.read_bytes()
    first = receipt.write("fixture", understanding=summary)
    destination = receipt.receipt_path("fixture")
    saved = destination.read_bytes(), destination.stat().st_mtime_ns
    log.append("fixture", "receipt_written", destination="irrelevant")
    assert receipt.write("fixture", understanding=summary) == first
    assert (destination.read_bytes(), destination.stat().st_mtime_ns) == saved
    log.append("fixture", "permission_decision", request_id="r1", behavior="allow")
    second = receipt.write("fixture", understanding=summary)
    assert second["event_fingerprint"] != first["event_fingerprint"]
    assert second["counts"]["auto_allowed"] == 1
    assert source.read_bytes().startswith(before)


def test_rebuild_corrects_tampered_cache_and_never_returns_another_session():
    first = receipt.write("one", understanding=summary)
    second = receipt.write("two", understanding=summary)
    assert first["event_fingerprint"] != second["event_fingerprint"]
    destination = receipt.receipt_path("one")
    destination.write_text(json.dumps(second))
    assert receipt.write("one", understanding=summary)["session_id"] == "one"
    first["counts"]["auto_allowed"] = 99
    destination.write_text(json.dumps(first))
    assert receipt.write("one", understanding=summary)["counts"]["auto_allowed"] == 0


def test_legacy_replay_rejects_mismatched_session(capsys):
    second = receipt.write("two", understanding=summary)
    receipt.receipt_path("one").write_text(json.dumps(second))
    assert receipt.main(["one", "--json"]) == 1
    assert capsys.readouterr().out == ""


def test_conflicting_request_context_cannot_establish_allow():
    events = [request(tier="T1"),
              event("permission_request", request_id="r1", command="git push", tier="T3"),
              event("permission_decision", request_id="r1", behavior="allow")]
    result = receipt.project("fixture", events, understanding=summary)
    assert result["actions"][0]["decision"] == "unknown"
    assert result["counts"]["auto_allowed"] == 0


def test_allow_without_review_provenance_does_not_invent_an_allow_category():
    events = [request(tier="T2"), event("permission_decision", request_id="r1", behavior="allow")]
    result = receipt.project("fixture", events, understanding=summary)
    assert result["actions"][0]["decision"] == "allow"
    assert result["actions"][0]["action"] == "unknown"
    assert result["counts"]["auto_allowed"] == result["counts"]["allowed_once"] == 0


def test_receipt_replacement_failure_preserves_old_receipt_and_raw_log(monkeypatch):
    source = persist([request()])
    receipt.write("fixture", understanding=summary)
    destination = receipt.receipt_path("fixture")
    saved = destination.read_bytes()
    log.append("fixture", "session_end")
    evidence = source.read_bytes()
    def fail(*args):
        raise OSError("fixture replace failure")
    monkeypatch.setattr(receipt.os, "replace", fail)
    with pytest.raises(OSError):
        receipt.write("fixture", understanding=summary)
    assert destination.read_bytes() == saved
    assert source.read_bytes() == evidence
    assert not list(destination.parent.glob("*.tmp"))


def test_concurrent_writers_produce_one_valid_cached_receipt():
    persist([request(), event("permission_decision", request_id="r1", behavior="allow")])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: receipt.write("fixture", understanding=summary), range(4)))
    assert all(value == results[0] for value in results)
    assert receipt.load(receipt.receipt_path("fixture")) == results[0]


def test_explicit_home_and_hashed_session_do_not_borrow_parent_session(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "real-parent")
    foreign = tmp_path / "run"
    persist([request(), event("session_end")], session="../synthetic-session", home=foreign)
    result = receipt.write("../synthetic-session", home=foreign, understanding=summary)
    assert result["status"] == "ended"
    assert receipt.receipt_path("../synthetic-session", home=foreign).parent == foreign / "receipts"
    assert not paths.scope_home().exists()


def test_bounded_log_skips_incomplete_invalid_and_duplicate_records(monkeypatch):
    path = persist([request()])
    valid = path.read_bytes()
    invalid = b'{"ts":"x","event":"permission_request","fields":{},"fields":{"behavior":"allow"}}\n'
    path.write_bytes(valid + invalid + b'{"event": NaN}\n' + valid[:-1])
    events, status = receipt.read_events("fixture")
    assert len(events) == 1 and status["malformed_records"] == 3
    monkeypatch.setattr(receipt, "MAX_LOG_BYTES", len(valid) + 5)
    events, status = receipt.read_events("fixture")
    assert len(events) == 1 and status["status"] == "truncated"
    assert status["malformed_records"] == 1


def test_missing_or_unavailable_evidence_is_explicit(tmp_path, monkeypatch):
    assert receipt.build("missing", understanding=summary)["input"]["status"] == "missing"
    path = paths.session_log("directory")
    path.mkdir(parents=True)
    assert receipt.build("directory", understanding=summary)["input"]["status"] == "unavailable"


def test_audit_requires_explicit_request_and_inputs(monkeypatch):
    from scope import coverage
    calls = []
    def audit(events, **kwargs):
        calls.append(kwargs)
        return {"status": "unknown", "meaning": "fixture audit"}
    monkeypatch.setattr(coverage, "audit", audit)
    persist([request()])
    assert receipt.build("fixture", understanding=summary)["coverage"]["status"] == "not_requested"
    assert not calls
    receipt.build("fixture", understanding=summary, audit=True, host="claude")
    assert calls == [{"transcript_path": None, "rules": (), "host": "claude"}]
    with pytest.raises(SystemExit) as error:
        receipt.main(["fixture", "--transcript", "fixture.jsonl"])
    assert error.value.code == 2


def test_render_escapes_untrusted_terminal_controls():
    result = receipt.project("fixture", [event("permission_request", request_id="r1", command="\x1b[2J\rforged")], understanding=summary)
    rendered = receipt.render(result)
    assert "\x1b" not in rendered and "\r" not in rendered
    assert "execution unknown" in rendered


def test_invalid_legacy_action_shape_and_oversized_receipt_rejected(tmp_path, monkeypatch):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"schema_version": 1, "actions": [None], "counts": {}}))
    with pytest.raises(ValueError, match="schema-1"):
        receipt.load(path)
    monkeypatch.setattr(receipt, "MAX_RECEIPT_BYTES", 5)
    with pytest.raises(ValueError, match="too large"):
        receipt.load(path)
