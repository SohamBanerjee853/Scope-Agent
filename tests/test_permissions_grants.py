"""Offline grant tests; all approvals are explicitly labeled fixture decisions."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import json
from uuid import UUID

import pytest

from scope import grants
from scope.grants import Card, Store, normalize_cwd, validate_card
from scope.tiers import Tier
from scope.wire import parse_request


def card(**changes):
    value = {"summary": "Fixture: stage project changes", "commands": ["git add *"],
             "domains": [], "budget": 3}
    value.update(changes)
    return validate_card(value)


def request(command="git add src/example.py", *, shell="bash", cwd="/workspace", session="fixture-session", agent=None):
    return parse_request(json.dumps({
        "session_id": session, "agent_id": agent, "cwd": cwd,
        "tool_name": "PowerShell" if shell == "powershell" else "Bash",
        "tool_input": {"command": command, "shell": shell},
    }))


@pytest.mark.parametrize("change", [
    {"summary": ""}, {"summary": " "}, {"summary": "x" * 301}, {"summary": 1},
    {"summary": "unsafe\nterminal"}, {"summary": "\x1b[2J"}, {"summary": "\ud800"},
    {"commands": []}, {"commands": ["git add *"] * 21}, {"commands": "git add *"},
    {"commands": [1]}, {"domains": "example.com"}, {"domains": ["example.com"] * 21},
    {"budget": True}, {"budget": False}, {"budget": 0}, {"budget": 101},
    {"budget": 1.0}, {"budget": "3"}, {"budget": None}, {"extra": "ignored?"},
])
def test_exact_card_schema_and_bounds(change):
    with pytest.raises((ValueError, UnicodeError)):
        card(**change)


@pytest.mark.parametrize("raw", [None, [], "card", {}, {"summary": "missing fields"}])
def test_non_object_and_missing_fields(raw):
    with pytest.raises(ValueError):
        validate_card(raw)


@pytest.mark.parametrize("domain", [
    "", "EXAMPLE.com", "Example.com", "https://example.com", "*.example.com",
    "example.com/path", "example.com:443", "example.com.", ".example.com",
    "example..com", "-example.com", "example-.com", "éxample.com", "a" * 64 + ".com",
    "a." * 127 + "com", None, 1, "example.com\n", "example.com@evil.test",
])
def test_domains_are_exact_lowercase_hostnames(domain):
    with pytest.raises(ValueError):
        card(domains=[domain])


@pytest.mark.parametrize("pattern", [
    "*", "git *", "git", "scope *", "uv run *", "npm run *", "python *",
    "python -m pytest *", "node *", "bash *", "powershell *", "custom-tool *",
    "custom-tool validate", "./git add *", "/usr/bin/git add *", "git.exe add *",
    "git add src/file", "git add a.b", "git add ?", "git add [ab]", "git add a*",
    "git add*", "git add * extra", "git add **", " git add *", "git  add *",
    "git add * ", "git\tadd *", "git add\n*", "git add; pwd", "git add | cat",
    "git add > output", "git add $(pwd)", "git add `pwd`", "git add 'x'",
    "git add ${HOME}", "env git add *", "command git add *", "git -C repo add *",
    "git push *", "git reset *", "rm *", "Remove-Item *", "curl *",
    "git " + "a" * 300,
])
def test_unsafe_or_broad_patterns_rejected(pattern):
    with pytest.raises(ValueError):
        card(commands=[pattern])


@pytest.mark.parametrize("pattern", [
    "git add *", "git add --all", "git commit *", "mkdir *", "touch *", "cp *",
    "New-Item *", "Set-Content *", "pytest *", "uv run pytest *", "scope demo-adapter *",
])
def test_narrow_patterns_validate_against_both_shells(pattern):
    assert card(commands=[pattern]).commands == (pattern,)


def test_card_is_immutable_and_serialization_is_detached():
    value = card(summary="x" * 300, budget=100, commands=["git add *"] * 20,
                 domains=[f"{index}.example.com" for index in range(20)])
    with pytest.raises(FrozenInstanceError):
        value.budget = 101
    data = value.to_dict()
    data["commands"].append("git push *")
    data["domains"].append("evil.test")
    assert len(value.commands) == len(value.domains) == 20
    assert card(budget=1).budget == 1


def test_manually_constructed_card_is_revalidated():
    invalid = Card("fixture", ("python *",), (), 2)
    with pytest.raises(ValueError):
        Store().propose("fixture", None, "/workspace", invalid)
    with pytest.raises(ValueError):
        Store().approve(request(), invalid)


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_proposals_never_approve_or_replace_approved_grants(shell):
    store = Store()
    req = request(shell=shell)
    first = card()
    store.propose(req.session_id, req.agent_id, req.cwd, first)
    assert store.proposal_for(req) == first
    assert store.consume(req) is None
    approved = store.approve(req, first)  # Explicit fixture human approval.
    other = card(commands=["mkdir *"], budget=100)
    store.propose(req.session_id, req.agent_id, req.cwd, other.to_dict())
    assert store.proposal_for(req) == other
    assert store.consume(request("mkdir build", shell=shell)) is None
    consumed = store.consume(req)
    assert consumed.grant_id == approved.grant_id
    assert consumed.card == first
    assert consumed.remaining == 2


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_expiry_is_monotonic_exact_boundary_and_approval_ids_are_fresh(shell):
    now = [100.0]
    store = Store(clock=lambda: now[0])
    req = request(shell=shell)
    initial = store.approve(req, card())
    assert UUID(initial.grant_id).version == 4
    assert initial.expires_at == 1000.0
    now[0] = 999.999
    assert store.consume(req).remaining == 2
    now[0] = 1000.0
    assert store.consume(req) is None
    renewed = store.approve(req, card())
    assert renewed.grant_id != initial.grant_id
    assert renewed.remaining == 3 and renewed.expires_at == 1900.0


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_atomic_budget_under_parallel_consumers(shell):
    store = Store()
    req = request(shell=shell)
    approved = store.approve(req, card(budget=17))
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: store.consume(req), range(80)))
    granted = [result for result in results if result is not None]
    assert len(granted) == 17
    assert sorted(result.remaining for result in granted) == list(range(17))
    assert {result.grant_id for result in granted} == {approved.grant_id}
    assert approved.remaining == 17  # Returned records are snapshots.


@pytest.mark.parametrize("change", [
    {"session": "other-session"}, {"agent": "child"}, {"cwd": "/other"},
    {"shell": "powershell"},
])
def test_session_agent_cwd_shell_bindings_do_not_leak(change):
    store = Store()
    req = request()
    store.propose(req.session_id, req.agent_id, req.cwd, card())
    store.approve(req, card())
    other = request(**change)
    assert store.consume(other) is None
    if "shell" not in change:  # Protocol proposals intentionally have no shell.
        assert store.proposal_for(other) is None
    assert store.consume(req).remaining == 2


@pytest.mark.parametrize("cwd, same", [
    ("/workspace/./src/..", "/workspace"),
    ("C:\\Work\\src\\..", "c:/work"),
    ("C:\\WORK\\", "c:\\work"),
    ("\\\\server\\share\\src\\..", "\\\\server\\share\\"),
])
def test_normalized_cwd_binding_is_platform_independent(cwd, same):
    store = Store()
    req = request("git add file", cwd=cwd)
    store.approve(req, card())
    assert store.consume(request("git add file", cwd=same)) is not None


def test_unc_root_normalization_does_not_override_classifier_refusal():
    assert normalize_cwd("\\\\server\\share\\") == normalize_cwd("\\\\server\\share")
    # S1 deliberately cannot prove writes at a bare UNC share root; normalization
    # equivalence is never a reason to skip its stricter request classification.
    with pytest.raises(ValueError):
        Store().approve(request("git add file", cwd="\\\\server\\share"), card())


@pytest.mark.parametrize("cwd", ["relative", "C:relative", "\\rooted", "", "/bad\x00path",
                                     "/workspace/*", "/workspace/~/x", "C:\\work:stream", "\\\\server"])
def test_ambiguous_cwd_cannot_bind_grants(cwd):
    with pytest.raises(ValueError):
        normalize_cwd(cwd)


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("command", [
    "git push origin main", "git add file; git push", "git add .env", "git add $(pwd)",
    "git add file && git add other", "git add file | cat", "git add file > output",
    "git add 'file'", 'git add "file"', "git add src/*", "git add file # comment",
    "./git add file", "/usr/bin/git add file", "git.exe add file", "env git add file",
    "git add file\ngit add other", "git add file\r", "git add `file`", "custom-tool file",
    "git\u00a0add file", "git\u2003add file", "git add\u00a0file",
])
def test_grants_reject_dangerous_dynamic_compound_and_custom_shapes(shell, command):
    store = Store()
    req = request(shell=shell)
    store.approve(req, card())
    assert store.consume(request(command, shell=shell)) is None
    assert store.consume(req).remaining == 2


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_hard_ask_and_classifier_failure_happen_before_grant_lookup(monkeypatch, shell):
    store = Store()
    req = request(shell=shell)
    store.approve(req, card())

    class NoLookup(dict):
        def get(self, *args):
            pytest.fail("classification must reject before grant lookup")

        def items(self):
            pytest.fail("classification must reject before grant pruning")

    store._grants = NoLookup()
    assert store.consume(request("git push", shell=shell)) is None
    monkeypatch.setattr(grants, "classify", lambda *args: Tier("T2", "error", "classification-error"))
    assert store.consume(req) is None
    with pytest.raises(ValueError):
        store.approve(req, card())


def test_exception_and_unexpected_tier_fail_closed(monkeypatch):
    store = Store()
    store.approve(request(), card())
    monkeypatch.setattr(grants, "classify", lambda *args: object())
    assert store.consume(request()) is None


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_exact_pattern_does_not_become_prefix_or_change_executable(shell):
    store = Store()
    req = request("git add --all", shell=shell)
    store.approve(req, card(commands=["git add --all"]))
    for command in ("git add --all src", "git add --all-other", "./git add --all"):
        assert store.consume(request(command, shell=shell)) is None
    assert store.consume(req).remaining == 2


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("pattern, command", [
    ("touch *", "touch file"), ("mkdir *", "mkdir build"),
    ("git add *", "git add src/file.py"), ("scope demo-adapter *", "scope demo-adapter observe"),
])
def test_reviewable_literal_requests_reuse_narrow_family(shell, pattern, command):
    store = Store()
    req = request(command, shell=shell)
    store.approve(req, card(commands=[pattern], budget=1))
    assert store.consume(req).remaining == 0
    assert store.consume(req) is None


def test_windows_backslash_argument_is_not_posix_escape():
    store = Store()
    req = request("git add src\\file.py", shell="powershell", cwd="C:\\work")
    store.approve(req, card())
    assert store.consume(req) is not None
    with pytest.raises(ValueError):
        store.approve(request("git add src\\file.py"), card())


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_domains_cover_every_network_target_and_no_redirects(shell):
    store = Store()
    req = request("curl https://example.com/docs", shell=shell)
    network = card(commands=["curl *"], domains=["example.com"], budget=10)
    store.approve(req, network)
    for command in (
        "curl https://evil.test", "curl https://sub.example.com", "curl http://example.com.evil.test",
        "curl https://example.com https://evil.test", "curl https://example.com localhost",
        "curl --location https://example.com", "curl -L https://example.com",
        "curl https://example.com file:///etc/passwd", "curl -d hello https://example.com",
        "curl https://example.com:bad", "curl --url https://evil.test",
    ):
        assert store.consume(request(command, shell=shell)) is None
    for command in ("curl https://EXAMPLE.COM/docs", "curl --url https://example.com",
                    "curl --url=https://example.com", "curl example.com/docs"):
        assert store.consume(request(command, shell=shell)) is not None


def test_non_network_commands_cannot_smuggle_unapproved_domains():
    store = Store()
    req = request("scope demo-adapter observe")
    store.approve(req, card(commands=["scope demo-adapter *"]))
    assert store.consume(request("scope demo-adapter https://evil.test")) is None


def test_approval_requires_current_request_coverage():
    store = Store()
    for req in (request("mkdir build"), request("git push"), request("./git add file")):
        with pytest.raises(ValueError):
            store.approve(req, card())
    assert store.sessions() == ()


def test_revoke_clears_all_state_and_changes_generation_without_human_lock():
    store = Store()
    for session in ("one", "two"):
        req = request(session=session)
        store.propose(session, None, req.cwd, card())
        store.approve(req, card())
    assert store.sessions() == ("one", "two")
    assert store.revoke() == 1
    assert store.generation == 1 and store.sessions() == ()
    for session in ("one", "two"):
        assert store.consume(request(session=session)) is None
        assert store.proposal_for(request(session=session)) is None
    assert store.revoke() == 2


def test_session_cleanup_retains_other_session_and_fresh_store_has_no_state():
    store = Store()
    for session in ("one", "two"):
        store.propose(session, None, "/workspace", card())
        store.approve(request(session=session), card())
    store.clear_session("one")
    assert store.generation == 1
    assert store.sessions() == ("two",)
    assert store.consume(request(session="one")) is None
    assert store.proposal_for(request(session="one")) is None
    assert store.consume(request(session="two")) is not None
    assert Store().sessions() == ()


def test_discard_undelivered_grant_does_not_remove_a_later_approval():
    store = Store()
    original = store.approve(request(), card())
    newer = store.approve(request(), card())
    store.discard_grant(original.grant_id)
    assert store.consume(request()).grant_id == newer.grant_id
    store.discard_grant(newer.grant_id)
    assert store.consume(request()) is None


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), "time", None])
def test_invalid_clock_never_allows(invalid):
    value = [0]
    store = Store(clock=lambda: value[0])
    store.approve(request(), card())
    value[0] = invalid
    assert store.consume(request()) is None
    with pytest.raises(ValueError):
        store.approve(request(), card())


def test_bound_state_and_expiry_cleanup(monkeypatch):
    monkeypatch.setattr(grants, "MAX_BINDINGS", 1)
    now = [0]
    store = Store(clock=lambda: now[0])
    store.propose("one", None, "/workspace", card())
    store.approve(request(session="one"), card())
    with pytest.raises(ValueError):
        store.propose("two", None, "/workspace", card())
    with pytest.raises(ValueError):
        store.approve(request(session="two"), card())
    now[0] = 900
    store.approve(request(session="two"), card())
    assert store.consume(request(session="two")) is not None
