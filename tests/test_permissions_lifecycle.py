"""Lifecycle hooks must remain bounded, silent and evidence-only."""

import io
import json

import pytest

from scope import ipc, log, paths, receipt, session_end_hook, stop_hook, wire


def feed(monkeypatch, module, value):
    raw = value if isinstance(value, str) else json.dumps(value)
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(raw))


def test_stop_appends_status_and_notifies_with_bounded_timeout(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(ipc, "exchange", lambda message, **kwargs: calls.append((message, kwargs)))
    feed(monkeypatch, stop_hook, {"session_id": "fixture", "hook_event_name": "Stop", "cwd": "/fixture"})
    assert stop_hook.main([]) == 0
    assert calls == [({"kind": "stop", "session_id": "fixture"}, {"timeout": 0.25})]
    assert log.read("fixture")[0]["fields"]["status"] == "turn_stopped"
    assert capsys.readouterr().out == ""


def test_session_end_is_append_only_and_does_no_network_render_or_audit(monkeypatch, capsys):
    from scope import coverage
    calls = []
    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
    monkeypatch.setattr(ipc, "exchange", forbidden)
    monkeypatch.setattr(receipt, "write", forbidden)
    monkeypatch.setattr(receipt, "render", forbidden)
    monkeypatch.setattr(coverage, "audit", forbidden)
    log.append("fixture", "observation", observation={"actual": True})
    source = paths.session_log("fixture")
    before = source.read_bytes()
    monkeypatch.setenv("CODEX_THREAD_ID", "real-parent")
    feed(monkeypatch, session_end_hook, {"session_id": "fixture", "hook_event_name": "SessionEnd", "reason": "other"})
    assert session_end_hook.main([]) == 0
    assert source.read_bytes().startswith(before)
    assert [item["event"] for item in log.read("fixture")] == ["observation", "session_end"]
    assert log.read("real-parent") == []
    assert not calls
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("module", [stop_hook, session_end_hook])
def test_malformed_lifecycle_inputs_abstain_without_evidence(module, monkeypatch, capsys):
    for data in ("{", "[]", '{"session_id":"one","session_id":"two"}',
                 {"session_id": "fixture", "hook_event_name": "PermissionRequest"},
                 {"session_id": "fixture", "reason": {"unsafe": True}}, "x" * (wire.MAX_INPUT_BYTES + 1)):
        feed(monkeypatch, module, data)
        assert module.main([]) == 0
    assert not paths.scope_home().exists()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("module", [stop_hook, session_end_hook])
def test_append_failures_always_abstain(module, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise OSError("fixture disk failure")
    monkeypatch.setattr(log, "append", fail)
    feed(monkeypatch, module, {"session_id": "fixture"})
    assert module.main([]) == 0
    assert capsys.readouterr().out == ""


def test_lifecycle_reader_requests_only_bounded_bytes():
    class Stream:
        def read(self, size):
            assert size == wire.MAX_INPUT_BYTES + 1
            return b'{"session_id":"fixture","hook_event_name":"SessionEnd"}'
    assert wire.read_event(Stream(), "SessionEnd") == {"session_id": "fixture"}
