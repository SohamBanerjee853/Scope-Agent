"""Real loopback transport, with deterministic handlers instead of human input."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time

import pytest

from scope import ipc


def private_file(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = ipc._private_open(path, create=True, exclusive=True, writable=True)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)


def raw_request(server, content):
    with socket.create_connection(server.address, timeout=1) as client:
        client.settimeout(1)
        try:
            client.sendall(content)
            return client.recv(ipc.MAX_FRAME + 1)
        except (ConnectionError, socket.timeout):
            return b""


def authenticated(server, **fields):
    endpoint = ipc._read_endpoint(server.home)
    return {"kind": "ping", "token": endpoint["token"],
            "nonce": secrets.token_hex(16), **fields}


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_real_loopback_round_trip_preserves_shell_and_unicode(tmp_path, shell):
    seen = []

    def handler(message, context):
        seen.append((message, context.nonce, context.state, context.active()))
        return {"answer": "Labeled fixture: café", "shell": message["shell"]}

    with ipc.Server(handler, home=tmp_path, admission=lambda m, c: 17) as server:
        assert server.address[0] == "127.0.0.1"
        assert ipc.exchange({"kind": "question", "shell": shell}, home=tmp_path) == {
            "answer": "Labeled fixture: café", "shell": shell}
        assert len(seen) == 1
        assert seen[0][0] == {"kind": "question", "shell": shell}
        assert len(seen[0][1]) == 32
        assert seen[0][2:] == (17, True)
        if os.name != "nt":
            assert server.endpoint_path.stat().st_mode & 0o777 == 0o600
            assert (tmp_path / ipc.LOCK_NAME).stat().st_mode & 0o777 == 0o600
        # On Windows _read_endpoint checks the owner SID and protected DACL.
        assert ipc._read_endpoint(tmp_path)["port"] == server.address[1]
    assert not (tmp_path / ipc.ENDPOINT_NAME).exists()
    assert (tmp_path / ipc.LOCK_NAME).exists()


@pytest.mark.parametrize("message", [None, [], "ping", {"token": "spoof"}, {"nonce": "spoof"}])
def test_invalid_client_message_fails_closed(tmp_path, message):
    assert ipc.exchange(message, home=tmp_path) is None


@pytest.mark.parametrize("timeout", [0, -1, True, None, "1", float("inf"), float("nan")])
def test_invalid_client_timeout_fails_closed(tmp_path, timeout):
    assert ipc.exchange({"kind": "ping"}, timeout=timeout, home=tmp_path) is None


@pytest.mark.parametrize("endpoint", [
    {}, {"port": 12}, {"port": True, "token": "a" * 64},
    {"port": 0, "token": "a" * 64}, {"port": 65536, "token": "a" * 64},
    {"port": "1234", "token": "a" * 64}, {"port": 1234, "token": "weak"},
    {"port": 1234, "token": "A" * 64}, {"port": 1234, "token": None},
    {"port": 1234, "token": "a" * 64, "host": "example.com"},
    {"port": 1234, "token": "a" * 64, "extra": 1}, [],
])
def test_invalid_endpoints_are_never_connected(tmp_path, endpoint, monkeypatch):
    private_file(tmp_path / ipc.ENDPOINT_NAME, json.dumps(endpoint).encode())

    def forbidden(*args, **kwargs):
        pytest.fail("invalid endpoint attempted a socket connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    assert ipc.exchange({"kind": "ping"}, home=tmp_path) is None


@pytest.mark.parametrize("data", [b"{", b"\xff", b"a" * (ipc.MAX_ENDPOINT + 1),
                                  b'{"port":1,"port":2,"token":"' + b"a" * 64 + b'"}'])
def test_corrupt_or_oversize_endpoint(tmp_path, data):
    private_file(tmp_path / ipc.ENDPOINT_NAME, data)
    assert ipc.exchange({"kind": "ping"}, home=tmp_path) is None


def test_absent_and_stale_endpoint_fail_closed(tmp_path):
    assert ipc.exchange({"kind": "ping"}, home=tmp_path) is None
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    private_file(tmp_path / ipc.ENDPOINT_NAME,
                 json.dumps({"port": port, "token": "a" * 64}).encode())
    assert ipc.exchange({"kind": "ping"}, timeout=0.1, home=tmp_path) is None


def test_endpoint_rejects_links_and_shared_permissions(tmp_path):
    path = tmp_path / ipc.ENDPOINT_NAME
    target = tmp_path / "target"
    private_file(target, json.dumps({"port": 1, "token": "a" * 64}).encode())
    # Hardlinks need no Windows symlink privilege and must fail on every OS.
    os.link(target, path)
    assert ipc.exchange({"kind": "ping"}, home=tmp_path) is None
    path.unlink()
    if os.name != "nt":
        path.symlink_to(target)
        assert ipc.exchange({"kind": "ping"}, home=tmp_path) is None
        path.unlink()
        target.rename(path)
        path.chmod(0o644)
        with pytest.raises(OSError):
            ipc._read_endpoint(tmp_path)


@pytest.mark.parametrize("override", [
    {"token": "a" * 64}, {"token": None}, {"token": 1},
    {"nonce": ""}, {"nonce": "a" * 33}, {"nonce": "A" * 32}, {"nonce": None},
])
def test_bad_authentication_and_nonce_never_reach_handler(tmp_path, override, capsys):
    seen = []
    with ipc.Server(lambda m, c: seen.append(m) or {"ready": True}, home=tmp_path) as server:
        assert raw_request(server, ipc._encode(authenticated(server, **override))) == b""
    assert seen == []
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("frame", [
    b"[]\n", b"null\n", b"{\n", b"\xff\n", b'{"x":NaN}\n',
    b'{"token":"a","token":"b"}\n', b"x" * ipc.MAX_FRAME,
])
def test_invalid_frames_never_reach_handler(tmp_path, frame):
    seen = []
    with ipc.Server(lambda m, c: seen.append(m) or {}, home=tmp_path,
                    request_timeout=0.2) as server:
        assert raw_request(server, frame) == b""
    assert seen == []


def test_second_frame_is_rejected(tmp_path):
    seen = []
    with ipc.Server(lambda m, c: seen.append(m) or {}, home=tmp_path) as server:
        frame = ipc._encode(authenticated(server))
        assert raw_request(server, frame + frame) == b""
    assert seen == []


def test_fragmented_json_and_utf8_reassembled(tmp_path):
    with ipc.Server(lambda m, c: {"answer": m["value"]}, home=tmp_path) as server:
        message = authenticated(server, value="café")
        frame = ipc._encode(message)
        split = frame.index("é".encode()) + 1
        with socket.create_connection(server.address, timeout=1) as client:
            client.sendall(frame[:split])
            client.sendall(frame[split:])
            response = ipc._receive(client, time.monotonic() + 1)
        assert response == {"answer": "café", "token": message["token"],
                            "nonce": message["nonce"]}


def test_message_byte_limit_includes_newline_and_authentication(tmp_path):
    assert len(ipc._encode({"x": "a" * (ipc.MAX_FRAME - 9)})) == ipc.MAX_FRAME
    with pytest.raises(ValueError):
        ipc._encode({"x": "a" * (ipc.MAX_FRAME - 8)})
    seen = []
    with ipc.Server(lambda m, c: seen.append(m) or {"ready": True}, home=tmp_path):
        assert ipc.exchange({"kind": "ping", "x": "a" * ipc.MAX_FRAME}, home=tmp_path) is None
    assert seen == []


@pytest.mark.parametrize("reply", [[], None, {"token": "spoof"}, {"nonce": "spoof"},
                                  {"answer": "x" * ipc.MAX_FRAME}, {"value": float("inf")}])
def test_invalid_or_oversize_handler_reply_fails_closed(tmp_path, reply):
    with ipc.Server(lambda m, c: reply, home=tmp_path):
        assert ipc.exchange({"kind": "ping"}, home=tmp_path) is None


def test_handler_exception_abstains_without_logging_token(tmp_path, capsys):
    def handler(message, context):
        raise RuntimeError("never log " + ipc._read_endpoint(tmp_path)["token"])

    with ipc.Server(handler, home=tmp_path):
        assert ipc.exchange({"kind": "ping"}, home=tmp_path) is None
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("failure", ["token", "nonce", "missing", "double", "oversize", "stall"])
def test_client_rejects_unauthenticated_or_invalid_reply(tmp_path, failure):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        private_file(tmp_path / ipc.ENDPOINT_NAME, json.dumps({
            "port": listener.getsockname()[1], "token": "a" * 64}).encode())
        received = []

        def fake_server():
            with listener.accept()[0] as client:
                request = ipc._receive(client, time.monotonic() + 1)
                received.append(request)
                reply = {"behavior": "allow", "token": request["token"], "nonce": request["nonce"]}
                if failure in {"token", "nonce"}:
                    reply[failure] = "b" * len(reply[failure])
                elif failure == "missing":
                    del reply["token"]
                if failure == "stall":
                    client.settimeout(1)
                    client.recv(1)  # caller timeout closes the socket
                    return
                data = ipc._encode(reply)
                if failure == "double":
                    data += data
                elif failure == "oversize":
                    data = b"x" * ipc.MAX_FRAME
                try:
                    client.sendall(data)
                except ConnectionError:
                    pass

        thread = threading.Thread(target=fake_server, daemon=True)
        thread.start()
        assert ipc.exchange({"kind": "request"}, timeout=0.15, home=tmp_path) is None
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert len(received) == 1  # failed transport never retries/resends


def test_explicit_home_does_not_mutate_environment(tmp_path):
    before = {name: os.environ.get(name) for name in
              ("SCOPE_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR")}
    with ipc.Server(lambda m, c: {"ready": True}, home=tmp_path):
        assert ipc.exchange({"kind": "ping"}, home=tmp_path) == {"ready": True}
        assert ipc.exchange({"kind": "ping"}) is None
    assert {name: os.environ.get(name) for name in before} == before


def test_single_instance_and_restart_discards_endpoint_token(tmp_path):
    first = ipc.Server(lambda m, c: {}, home=tmp_path).start()
    original = ipc._read_endpoint(tmp_path)
    try:
        assert first.start() is first
        with pytest.raises(ipc.AlreadyRunningError):
            ipc.Server(lambda m, c: {}, home=tmp_path).start()
        assert ipc._read_endpoint(tmp_path) == original
    finally:
        first.close()
    first.close()
    with pytest.raises(RuntimeError):
        first.start()
    with ipc.Server(lambda m, c: {"ready": True}, home=tmp_path):
        assert ipc._read_endpoint(tmp_path)["token"] != original["token"]
        assert ipc.exchange({"kind": "ping"}, home=tmp_path) == {"ready": True}


def test_lifetime_lock_is_cross_process_and_released_after_process_exit(tmp_path):
    code = """
import sys
from scope.ipc import Server, AlreadyRunningError
try:
    server = Server(lambda m, c: {}, home=sys.argv[1]).start()
except AlreadyRunningError:
    print('locked', flush=True)
else:
    print('started', flush=True)
    sys.stdin.readline()
"""
    with ipc.Server(lambda m, c: {}, home=tmp_path):
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], input="",
                                text=True, capture_output=True, timeout=5)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "locked"
    child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "started"
    finally:
        child.terminate()
        child.communicate(timeout=5)
    # A stale endpoint survives abrupt exit, but its OS lock does not.
    with ipc.Server(lambda m, c: {"ready": True}, home=tmp_path):
        assert ipc.exchange({"kind": "ping"}, home=tmp_path) == {"ready": True}


def test_close_preserves_an_endpoint_it_does_not_own(tmp_path):
    server = ipc.Server(lambda m, c: {}, home=tmp_path).start()
    replacement = {"port": 1234, "token": "b" * 64}
    replacement_path = tmp_path / "replacement"
    private_file(replacement_path, json.dumps(replacement).encode())
    replacement_path.replace(server.endpoint_path)
    server.close()
    assert ipc._read_endpoint(tmp_path) == replacement


@pytest.mark.parametrize("timeout", [0, 96, True, "95", float("nan"), float("inf")])
def test_invalid_server_deadline(timeout):
    with pytest.raises(ValueError):
        ipc.Server(lambda m, c: {}, request_timeout=timeout)


@pytest.mark.parametrize("max_clients", [0, 129, True, 1.5, "16"])
def test_invalid_worker_bound(max_clients):
    with pytest.raises(ValueError):
        ipc.Server(lambda m, c: {}, max_clients=max_clients)


def test_parallel_clients_have_distinct_nonces_and_contexts(tmp_path):
    barrier = threading.Barrier(4)
    contexts = []

    def handler(message, context):
        contexts.append(context)
        barrier.wait(timeout=2)
        return {"session_id": message["session_id"]}

    with ipc.Server(handler, home=tmp_path), ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(ipc.exchange, {"kind": "request", "session_id": str(index)},
                               home=tmp_path) for index in range(4)]
        assert [future.result(timeout=3) for future in futures] == [
            {"session_id": str(index)} for index in range(4)]
    assert len({context.nonce for context in contexts}) == 4
    assert all(context.cancelled.is_set() for context in contexts)


def test_worker_limit_includes_clients_sending_no_frame(tmp_path):
    seen = []
    with ipc.Server(lambda m, c: seen.append(m) or {}, home=tmp_path,
                    max_clients=1, request_timeout=0.25) as server:
        with socket.create_connection(server.address, timeout=1) as stalled:
            deadline = time.monotonic() + 1
            while not server._contexts and time.monotonic() < deadline:
                time.sleep(0.005)
            assert len(server._contexts) == 1
            assert ipc.exchange({"kind": "ping"}, timeout=0.1, home=tmp_path) is None
            assert len(server._contexts) <= 1
            assert stalled.recv(1) == b""
    assert seen == []


@pytest.mark.parametrize("cause", ["deadline", "disconnect", "shutdown"])
def test_pending_handler_is_cancelled_and_cannot_return_late_allow(tmp_path, cause):
    entered, cancelled = threading.Event(), threading.Event()
    contexts = []

    def handler(message, context):
        contexts.append(context)
        entered.set()
        while context.active():
            time.sleep(0.005)
        cancelled.set()
        return {"behavior": "allow"}

    server = ipc.Server(handler, home=tmp_path,
                         request_timeout=0.1 if cause == "deadline" else 95).start()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(ipc.exchange, {"kind": "request"},
                                 timeout=0.1 if cause == "disconnect" else 2, home=tmp_path)
            assert entered.wait(timeout=1)
            if cause == "shutdown":
                server.close()
            assert future.result(timeout=2) is None
            assert cancelled.wait(timeout=1)
        assert contexts[0].cancelled.is_set()
        assert not contexts[0].delivered
    finally:
        server.close()


def test_revoked_final_reply_is_guarded_after_handler_returns(tmp_path):
    entered, finished = threading.Event(), threading.Event()
    lock = threading.RLock()
    state = {"valid": True, "delivered": None}

    def handler(message, context):
        context.reply_lock = lock
        context.reply_valid = lambda: state["valid"]

        def after_reply():
            state["delivered"] = context.delivered
            finished.set()

        context.after_reply = after_reply
        entered.set()
        return {"answer": "Labeled late fixture"}

    with ipc.Server(handler, home=tmp_path), ThreadPoolExecutor(max_workers=1) as pool:
        with lock:
            future = pool.submit(ipc.exchange, {"kind": "question"}, home=tmp_path)
            assert entered.wait(timeout=1)
            state["valid"] = False
        assert future.result(timeout=2) is None
        assert finished.wait(timeout=1)
    assert state["delivered"] is False


def test_after_reply_can_close_server_after_successful_shutdown_reply(tmp_path):
    finished = threading.Event()
    contexts = []

    def handler(message, context):
        contexts.append(context)

        def after_reply():
            server.close()
            finished.set()

        context.after_reply = after_reply
        return {"stopped": True}

    server = ipc.Server(handler, home=tmp_path).start()
    try:
        assert ipc.exchange({"kind": "shutdown"}, home=tmp_path) == {"stopped": True}
        assert finished.wait(timeout=2)
        assert contexts[0].delivered is True
        assert not server.endpoint_path.exists()
    finally:
        server.close()


@pytest.mark.parametrize("outcome", ["valid", "invalid", "refused", "exception", "disconnect"])
def test_reply_commit_and_finalize_share_lock_and_failure_guard(tmp_path, outcome):
    order = []
    finalized = threading.Event()

    class InspectableLock:
        held = False

        def __enter__(self):
            self.held = True

        def __exit__(self, *args):
            self.held = False

    lock = InspectableLock()

    def handler(message, context):
        context.reply_lock = lock
        context.reply_valid = lambda: outcome != "invalid"

        def before():
            assert lock.held
            order.append("before")
            if outcome == "exception":
                raise RuntimeError("labeled commit failure")
            if outcome == "disconnect":
                context.cancelled.set()
            return outcome != "refused"

        def finalize(delivered):
            assert lock.held
            order.append(delivered)
            finalized.set()

        context.before_reply = before
        context.reply_finalize = finalize
        return {"behavior": "allow"}

    with ipc.Server(handler, home=tmp_path):
        reply = ipc.exchange({"kind": "request"}, home=tmp_path)
        assert finalized.wait(timeout=1)
    assert reply == ({"behavior": "allow"} if outcome == "valid" else None)
    assert order == ([False] if outcome == "invalid" else ["before", outcome == "valid"])


def test_start_failure_releases_lock_and_removes_temporary_endpoint(tmp_path, monkeypatch):
    replace = os.replace

    def failure(source, target):
        raise OSError("labeled write failure")

    monkeypatch.setattr(os, "replace", failure)
    with pytest.raises(OSError):
        ipc.Server(lambda m, c: {}, home=tmp_path).start()
    assert list(tmp_path.glob(".watch-*.tmp")) == []
    monkeypatch.setattr(os, "replace", replace)
    with ipc.Server(lambda m, c: {"ready": True}, home=tmp_path):
        assert ipc.exchange({"kind": "ping"}, home=tmp_path) == {"ready": True}


def test_shutdown_with_corrupt_endpoint_still_releases_os_lock(tmp_path):
    server = ipc.Server(lambda m, c: {}, home=tmp_path).start()
    # Nested JSON can raise RecursionError rather than JSONDecodeError.
    replacement = tmp_path / "corrupt"
    private_file(replacement, b"[" * 1500 + b"]" * 1500)
    replacement.replace(server.endpoint_path)
    server.close()
    with ipc.Server(lambda m, c: {"ready": True}, home=tmp_path):
        assert ipc.exchange({"kind": "ping"}, home=tmp_path) == {"ready": True}


def test_stale_symlink_endpoint_never_writes_its_target(tmp_path):
    target = tmp_path / "untouched"
    target.write_text("preserve this fixture", encoding="utf-8")
    if os.name != "nt":
        (tmp_path / ipc.ENDPOINT_NAME).symlink_to(target)
    else:
        # Native Windows symlinks need extra privileges; a stale hardlink still
        # verifies that atomic replacement does not open/write the old target.
        os.link(target, tmp_path / ipc.ENDPOINT_NAME)
    with ipc.Server(lambda m, c: {"ready": True}, home=tmp_path):
        assert ipc.exchange({"kind": "ping"}, home=tmp_path) == {"ready": True}
    assert target.read_text(encoding="utf-8") == "preserve this fixture"
