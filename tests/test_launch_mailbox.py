"""Real launch-local files/OS locks with deterministic, nonexecuting handlers."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from scope import host_session, ipc, mailbox


@pytest.fixture
def launch(tmp_path, monkeypatch):
    home = (tmp_path / "owned run").resolve()
    record = host_session.create_launch(home, launch_id="fixture-mailbox", host="codex", cwd=tmp_path.resolve())
    for key, value in {"SCOPE_HOME": str(home), "SCOPE_LAUNCH_ID": record["launch_id"], "SCOPE_HOST": "codex"}.items():
        monkeypatch.setenv(key, value)
    return record


def block_connect(monkeypatch):
    def blocked(*args, **kwargs):
        raise PermissionError("fixture sandbox blocks loopback connection establishment")

    monkeypatch.setattr(socket.socket, "connect", blocked)


def file_request(server, **overrides):
    endpoint = ipc._read_endpoint(server.home)
    nonce = overrides.get("nonce", secrets.token_hex(16))
    message = {"kind": "ping", "token": endpoint["token"], "nonce": nonce, **overrides}
    return mailbox.exchange(server.home, endpoint, ipc._encode(message), nonce, time.monotonic() + 1)


def raw_tcp(server, message):
    with socket.create_connection(server.address, timeout=1) as client:
        client.settimeout(1)
        client.sendall(ipc._encode(message))
        try:
            return client.recv(ipc.MAX_FRAME)
        except (ConnectionError, socket.timeout):
            return b""


def test_blocked_connect_uses_real_authenticated_mailbox_and_cleans_files(launch, monkeypatch):
    seen = []

    def handler(message, context):
        seen.append((message, context.nonce, context.state, context.active()))
        return {"answer": "Labeled fixture: 理解"}

    with ipc.Server(handler, home=launch["home"], launch_id=launch["launch_id"], admission=lambda m, c: 7) as server:
        directory = server._mailbox.directory
        block_connect(monkeypatch)
        assert ipc.exchange({"kind": "question"}, timeout=2) == {"answer": "Labeled fixture: 理解"}
        assert seen[0][0] == {"kind": "question"}
        assert seen[0][2:] == (7, True)
        assert len(seen) == 1
        assert sorted(path.name for path in directory.iterdir()) == ["admission.lock"]
        endpoint = ipc._read_endpoint(server.home)
        assert set(endpoint) == {"port", "token", "launch_id", "mailbox"}
        assert endpoint["launch_id"] == launch["launch_id"]
        if os.name != "nt":
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert not directory.exists()
    assert not (Path(launch["home"]) / "watch.json").exists()


@pytest.mark.parametrize("stage", ["bind", "socket"])
def test_blocked_bind_creates_explicit_mailbox_only_endpoint(launch, monkeypatch, stage):
    def blocked(*args, **kwargs):
        raise PermissionError("fixture bind forbidden")

    if stage == "bind":
        monkeypatch.setattr(socket.socket, "bind", blocked)
    else:
        monkeypatch.setattr(socket, "socket", blocked)
    with ipc.Server(lambda m, c: {"ready": True}, home=launch["home"], launch_id=launch["launch_id"]) as server:
        assert server.address is None
        assert ipc._read_endpoint(server.home)["port"] is None
        assert server.start() is server
        assert ipc.exchange({"kind": "ping"}, timeout=2) == {"ready": True}


@pytest.mark.parametrize("stage", ["send", "receive"])
def test_no_mailbox_retry_after_any_tcp_send_attempt(launch, monkeypatch, stage):
    with ipc.Server(lambda m, c: {"ready": True}, home=launch["home"], launch_id=launch["launch_id"]):
        def forbidden(*args, **kwargs):
            pytest.fail("post-send fallback could ask or spend twice")

        monkeypatch.setattr(mailbox, "exchange", forbidden)
        if stage == "send":
            def broken(*args, **kwargs):
                raise ConnectionResetError("fixture partial-send failure")

            monkeypatch.setattr(socket.socket, "sendall", broken)
        else:
            original = ipc._receive

            def broken(sock, deadline, cancelled=None):
                if cancelled is None:
                    raise ConnectionResetError("fixture response failure")
                return original(sock, deadline, cancelled)

            monkeypatch.setattr(ipc, "_receive", broken)
        assert ipc.exchange({"kind": "ping"}, timeout=1) is None


@pytest.mark.parametrize("field,value", [("SCOPE_LAUNCH_ID", "wrong-launch"), ("SCOPE_HOST", "claude"),
                                         ("SCOPE_LAUNCH_ID", ""), ("SCOPE_HOST", "")])
def test_mismatched_launch_never_attempts_tcp_or_files(launch, monkeypatch, field, value):
    with ipc.Server(lambda m, c: pytest.fail("invalid launch must never arrive"),
                    home=launch["home"], launch_id=launch["launch_id"]):
        monkeypatch.setenv(field, value)
        monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("must not connect"))
        assert ipc.exchange({"kind": "ping"}, timeout=1) is None


def test_off_launch_connection_failure_has_no_mailbox_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("SCOPE_LAUNCH_ID", raising=False)
    monkeypatch.delenv("SCOPE_HOST", raising=False)
    with ipc.Server(lambda m, c: {"ready": True}, home=tmp_path):
        block_connect(monkeypatch)
        monkeypatch.setattr(mailbox, "exchange", lambda *args: pytest.fail("off-launch mailbox forbidden"))
        assert ipc.exchange({"kind": "ping"}, home=tmp_path, timeout=1) is None


def test_lifecycle_mailbox_does_not_require_native_session(launch, monkeypatch):
    with ipc.Server(lambda m, c: {"ready": True}, home=launch["home"], launch_id=launch["launch_id"]):
        block_connect(monkeypatch)
        monkeypatch.setattr(host_session, "session_id", lambda: pytest.fail("startup/exit transport cannot require open native ID"))
        assert ipc.exchange({"kind": "ping"}, timeout=1) == {"ready": True}


@pytest.mark.parametrize("override", [{"token": "a" * 64}, {"token": 1}, {"token": ""}])
def test_bad_file_authentication_never_reaches_handler(launch, override):
    seen = []
    with ipc.Server(lambda m, c: seen.append(m) or {}, home=launch["home"], launch_id=launch["launch_id"]) as server:
        assert file_request(server, **override) is None
        assert seen == []


@pytest.mark.parametrize("first", ["tcp", "mailbox"])
def test_replay_is_suppressed_across_both_transports(launch, first):
    seen = []
    with ipc.Server(lambda m, c: seen.append(m) or {"ready": True}, home=launch["home"], launch_id=launch["launch_id"]) as server:
        endpoint = ipc._read_endpoint(server.home)
        nonce = secrets.token_hex(16)
        message = {"kind": "ping", "token": endpoint["token"], "nonce": nonce}
        if first == "tcp":
            assert json.loads(raw_tcp(server, message))["ready"] is True
            assert file_request(server, nonce=nonce) is None
        else:
            assert file_request(server, nonce=nonce)["ready"] is True
            assert raw_tcp(server, message) == b""
        assert len(seen) == 1


def test_replay_memory_capacity_fails_closed_without_eviction(launch, monkeypatch):
    monkeypatch.setattr(ipc, "MAX_SEEN_NONCES", 2)
    seen = []
    with ipc.Server(lambda m, c: seen.append(m) or {"ready": True}, home=launch["home"], launch_id=launch["launch_id"]) as server:
        first = secrets.token_hex(16)
        assert file_request(server, nonce=first)["ready"] is True
        assert file_request(server)["ready"] is True
        assert file_request(server) is None
        assert file_request(server, nonce=first) is None
        assert len(server._seen_nonces) == 2 and len(seen) == 2


def test_client_timeout_invalidates_context_and_removes_artifacts(launch, monkeypatch):
    entered, cancelled = threading.Event(), threading.Event()
    contexts = []

    def handler(message, context):
        contexts.append(context)
        entered.set()
        while context.active():
            time.sleep(0.005)
        cancelled.set()
        return {"behavior": "allow"}

    with ipc.Server(handler, home=launch["home"], launch_id=launch["launch_id"]) as server:
        block_connect(monkeypatch)
        assert ipc.exchange({"kind": "question"}, timeout=0.15) is None
        assert entered.is_set() and cancelled.wait(1)
        assert contexts[0].delivered is False
        assert sorted(path.name for path in server._mailbox.directory.iterdir()) == ["admission.lock"]


def test_real_client_process_crash_releases_lease_and_invalidates_answer(launch):
    entered, cancelled = threading.Event(), threading.Event()

    def handler(message, context):
        entered.set()
        while context.active():
            time.sleep(0.005)
        cancelled.set()
        return {"behavior": "allow"}

    child = r'''
import os, secrets, sys
from pathlib import Path
from scope import ipc, mailbox
home = Path(sys.argv[1])
endpoint = ipc._read_endpoint(home)
directory = mailbox.location(home, endpoint["mailbox"])
nonce = secrets.token_hex(16)
fd = ipc._private_open(directory / (nonce + ".lease"), create=True, exclusive=True, writable=True)
ipc._lock(fd)
mailbox._write(directory / (nonce + ".request"), ipc._encode({"kind": "question", "token": endpoint["token"], "nonce": nonce}))
sys.stdin.readline()
os._exit(0)
'''
    with ipc.Server(handler, home=launch["home"], launch_id=launch["launch_id"], request_timeout=5) as server:
        process = subprocess.Popen([sys.executable, "-c", child, launch["home"]], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            assert entered.wait(3), "fixture child did not reach the mailbox handler"
            stdout, stderr = process.communicate(b"exit\n", timeout=3)
            assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
            assert stdout == b""
            assert cancelled.wait(1), "OS lease must cancel without waiting the five-second request deadline"
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)


@pytest.mark.parametrize("outcome", ["valid", "invalid", "refused", "disconnect", "publication_failure"])
def test_mailbox_preserves_reply_commit_rollback_and_lock(launch, monkeypatch, outcome):
    order, finalized = [], threading.Event()
    lock = threading.RLock()

    def handler(message, context):
        context.reply_lock = lock
        context.reply_valid = lambda: outcome != "invalid"

        def before():
            assert lock._is_owned()
            order.append("before")
            if outcome == "disconnect":
                context.cancelled.set()
            return outcome != "refused"

        def finalize(delivered):
            assert lock._is_owned()
            order.append(delivered)
            finalized.set()

        context.before_reply, context.reply_finalize = before, finalize
        return {"behavior": "allow"}

    original = mailbox._write

    def write(path, data):
        if outcome == "publication_failure" and path.name.endswith(".response"):
            raise OSError("fixture cannot publish response")
        return original(path, data)

    monkeypatch.setattr(mailbox, "_write", write)
    with ipc.Server(handler, home=launch["home"], launch_id=launch["launch_id"]) as server:
        reply = file_request(server, kind="request")
        assert finalized.wait(1)
    assert (reply is not None) is (outcome == "valid")
    assert order == ([False] if outcome == "invalid" else ["before", outcome == "valid"])


@pytest.mark.parametrize("control", ["revoke", "shutdown"])
def test_control_slot_survives_mailbox_saturation_and_shutdown_ack(launch, monkeypatch, control):
    entered, cancelled = threading.Event(), threading.Event()
    pending = []
    server = None

    def handler(message, context):
        if message["kind"] == "question":
            pending.append(context)
            entered.set()
            while context.active():
                time.sleep(0.005)
            cancelled.set()
            return {"answer": "Late fixture must be discarded"}
        assert message["kind"] == control
        pending[0].cancelled.set()
        if control == "shutdown":
            context.after_reply = server.close
            return {"stopped": True}
        return {"revoked": True}

    with ipc.Server(handler, home=launch["home"], launch_id=launch["launch_id"], max_clients=1,
                    control_kinds=frozenset({"revoke", "shutdown"})) as server, ThreadPoolExecutor(1) as pool:
        block_connect(monkeypatch)
        future = pool.submit(ipc.exchange, {"kind": "question"}, 2)
        assert entered.wait(1)
        assert ipc.exchange({"kind": "request"}, timeout=1) is None
        assert ipc.exchange({"kind": control}, timeout=1) == {"stopped" if control == "shutdown" else "revoked": True}
        assert future.result(timeout=1) is None
        assert cancelled.wait(1)


def test_mailbox_limit_never_creates_unbounded_pending_files(launch, monkeypatch):
    with ipc.Server(lambda m, c: {"ready": True}, home=launch["home"], launch_id=launch["launch_id"]) as server:
        monkeypatch.setattr(mailbox, "MAX_PENDING", 0)
        assert file_request(server) is None
        assert sorted(path.name for path in server._mailbox.directory.iterdir()) == ["admission.lock"]


@pytest.mark.parametrize("part", ["base", "generation"])
def test_mailbox_rejects_directory_link_or_reparse_redirection(launch, monkeypatch, part):
    with ipc.Server(lambda m, c: pytest.fail("redirected mailbox must not dispatch"),
                    home=launch["home"], launch_id=launch["launch_id"]) as server:
        suspect = server._mailbox.directory if part == "generation" else server._mailbox.directory.parent
        original = Path.lstat

        def redirected(path):
            info = original(path)
            if path == suspect:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info

        with monkeypatch.context() as patch:
            patch.setattr(Path, "lstat", redirected)
            block_connect(patch)
            assert ipc.exchange({"kind": "ping"}, timeout=0.1) is None


def test_mailbox_path_generation_and_oversized_frames_reject_before_writing(launch):
    with ipc.Server(lambda m, c: pytest.fail("invalid frame must not dispatch"),
                    home=launch["home"], launch_id=launch["launch_id"]) as server:
        endpoint = ipc._read_endpoint(server.home)
        with pytest.raises(ValueError):
            mailbox.location(server.home, "../outside")
        with pytest.raises(ValueError):
            mailbox.exchange(server.home, endpoint, b"{}\n", "../outside", time.monotonic() + 1)
        nonce = secrets.token_hex(16)
        with pytest.raises(ValueError):
            mailbox.exchange(server.home, endpoint, b"x" * (ipc.MAX_FRAME + 1), nonce, time.monotonic() + 1)
        assert sorted(path.name for path in server._mailbox.directory.iterdir()) == ["admission.lock"]


def test_close_cleans_only_owned_generation(launch):
    with ipc.Server(lambda m, c: {}, home=launch["home"], launch_id=launch["launch_id"]) as server:
        other = server._mailbox.directory.parent / secrets.token_hex(16)
        other.mkdir(mode=0o700)
        sentinel = other / "leave-this-evidence.txt"
        sentinel.write_text("independent generation fixture", encoding="utf-8")
        own = server._mailbox.directory
    assert sentinel.read_text(encoding="utf-8") == "independent generation fixture"
    assert not own.exists()


def test_stale_crashed_client_artifacts_are_cleaned_without_dispatch(launch):
    seen = []
    with ipc.Server(lambda m, c: seen.append(m) or {}, home=launch["home"], launch_id=launch["launch_id"]) as server:
        nonce = secrets.token_hex(16)
        directory = server._mailbox.directory
        with server._mailbox.mutex:
            fd = ipc._private_open(directory / (nonce + ".lease"), create=True, exclusive=True, writable=True)
            ipc._lock(fd)
            mailbox._write(directory / (nonce + ".request"), ipc._encode({"kind": "request", "token": server._token, "nonce": nonce}))
            ipc._unlock(fd)
            os.close(fd)  # Published files remain, but no process holds the OS lock.
        deadline = time.monotonic() + 1
        while list(directory.glob(nonce + ".*")) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert list(directory.glob(nonce + ".*")) == []
        assert seen == []


def test_live_publisher_prelock_window_is_not_mistaken_for_a_crash(launch, monkeypatch):
    opened, checked, release = threading.Event(), threading.Event(), threading.Event()
    original_open, original_alive = ipc._private_open, mailbox.lease_alive
    created = []

    def paused_open(path, **kwargs):
        fd = original_open(path, **kwargs)
        if kwargs.get("create") and path.name.endswith(".lease"):
            created.append(path)
            opened.set()
            assert release.wait(3)
        return fd

    def observed_alive(path):
        result = original_alive(path)
        if opened.is_set() and path.name == "admission.lock" and result:
            checked.set()
        return result

    with ipc.Server(lambda m, c: {"ready": True}, home=launch["home"], launch_id=launch["launch_id"]), ThreadPoolExecutor(1) as pool:
        monkeypatch.setattr(ipc, "_private_open", paused_open)
        monkeypatch.setattr(mailbox, "lease_alive", observed_alive)
        block_connect(monkeypatch)
        future = pool.submit(ipc.exchange, {"kind": "ping"}, 3)
        try:
            assert opened.wait(1)
            assert checked.wait(1), "poller must see admission ownership during the pre-lock window"
            assert created[0].exists()
        finally:
            release.set()
        assert future.result(timeout=2) == {"ready": True}
