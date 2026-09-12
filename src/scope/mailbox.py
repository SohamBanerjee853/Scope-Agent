"""Launch-local authenticated file transport when loopback cannot connect.

The watcher remains the only handler. Files are bounded private coordination
between same-user processes, not tamper-proof storage or a sandbox escape.
Requests use the IPC server's authentication, replay set, worker slots, Context,
and final reply/rollback callbacks. Publishing a response, like socket sendall,
does not establish that a host consumed it or executed anything.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import threading
import time

from . import ipc

MAX_PENDING = 128
MAX_FILES = MAX_PENDING * 4 + 8
MAX_GENERATIONS = 8
POLL_SECONDS = 0.02
_ID = re.compile(r"[0-9a-f]{32}\Z")
_FILE = re.compile(r"[0-9a-f]{32}\.(?:lease|request|response|request\.tmp|response\.tmp)\Z")


def _directory(path: Path, *, create=False) -> Path:
    """Reject links/reparse points in the complete canonical directory chain."""
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    for directory in (path, *path.parents):
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise OSError("mailbox directory must not contain links")
    info = path.lstat()
    if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise OSError("mailbox directory must be owned and private")
    return path


def location(home: Path, generation: str) -> Path:
    if not isinstance(generation, str) or not _ID.fullmatch(generation):
        raise ValueError("invalid mailbox generation")
    return _directory(_directory(home / "mailbox") / generation)


def _entries(directory: Path) -> list[str]:
    _directory(directory)
    result = []
    with os.scandir(directory) as entries:
        for entry in entries:
            result.append(entry.name)
            if len(result) > MAX_FILES:
                raise OSError("mailbox file capacity exceeded")
    return result


def _read(path: Path) -> bytes:
    _directory(path.parent)
    fd = ipc._private_open(path)
    try:
        if os.fstat(fd).st_size > ipc.MAX_FRAME:
            raise ValueError("mailbox frame exceeds limit")
        data = os.read(fd, ipc.MAX_FRAME + 1)
    finally:
        os.close(fd)
    if len(data) > ipc.MAX_FRAME or not data.endswith(b"\n") or data.count(b"\n") != 1:
        raise ValueError("mailbox requires one bounded complete frame")
    return data


def _write(path: Path, data: bytes) -> None:
    if len(data) > ipc.MAX_FRAME:
        raise ValueError("mailbox frame exceeds limit")
    _directory(path.parent)
    temporary = path.with_name(path.name + ".tmp")
    try:
        fd = ipc._private_open(temporary, create=True, exclusive=True, writable=True)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
        _directory(path.parent)
        os.replace(temporary, path)
    finally:
        try:
            _directory(path.parent)
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _remove(directory: Path, nonce: str, *, lease=True) -> None:
    if not _ID.fullmatch(nonce):
        return
    try:
        _directory(directory)
        for suffix in ("request", "response", "request.tmp", "response.tmp", *(("lease",) if lease else ())):
            (directory / (nonce + "." + suffix)).unlink(missing_ok=True)
    except OSError:
        pass


def lease_alive(path: Path) -> bool:
    """An OS lifetime lock, not a reusable PID or stale file, proves a waiter."""
    try:
        _directory(path.parent)
        fd = ipc._private_open(path, writable=True)
        try:
            try:
                ipc._lock(fd)
            except ipc.AlreadyRunningError:
                return True
            ipc._unlock(fd)
            return False
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return False


def exchange(home: Path, endpoint: dict, frame: bytes, nonce: str, deadline: float) -> dict | None:
    """Publish one request, hold its lifetime lease, then read one response."""
    if not isinstance(nonce, str) or not _ID.fullmatch(nonce):
        raise ValueError("invalid mailbox nonce")
    directory = location(home, endpoint["mailbox"])
    lease = directory / (nonce + ".lease")
    lease_fd = None
    admission_fd = ipc._private_open(directory / "admission.lock", create=True, writable=True)
    admitted = False
    try:
        while time.monotonic() < deadline:
            try:
                ipc._lock(admission_fd)
                admitted = True
                break
            except ipc.AlreadyRunningError:
                time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))
        if not admitted:
            return None
        try:
            if sum(name.endswith(".lease") for name in _entries(directory)) >= MAX_PENDING:
                return None
            lease_fd = ipc._private_open(lease, create=True, exclusive=True, writable=True)
            ipc._lock(lease_fd)
            _write(directory / (nonce + ".request"), frame)
        finally:
            ipc._unlock(admission_fd)
            admitted = False
        response = directory / (nonce + ".response")
        while time.monotonic() < deadline:
            try:
                return ipc._json_object(_read(response))
            except FileNotFoundError:
                # Removal is a completed failure, cancellation or watcher exit.
                if not (directory / (nonce + ".request")).exists():
                    return None
            time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))
        return None
    finally:
        if admitted:
            ipc._unlock(admission_fd)
        os.close(admission_fd)
        if lease_fd is not None:
            # Keep the OS lock until the answer is read or the caller ends.
            _remove(directory, nonce, lease=False)
            ipc._unlock(lease_fd)
            os.close(lease_fd)
            _remove(directory, nonce)


class Listener:
    """Bounded poller; all execution is delegated to the shared IPC dispatcher."""

    def __init__(self, server, generation: str):
        self.server = server
        base = _directory(server.home / "mailbox", create=True)
        with os.scandir(base) as entries:
            for count, _ in enumerate(entries, start=1):
                if count >= MAX_GENERATIONS:
                    raise OSError("mailbox generation capacity reached; inspect stale launch files")
        self.directory = base / generation
        self.directory.mkdir(mode=0o700)
        _directory(self.directory)
        self.closed = threading.Event()
        self.thread = None
        self.active: set[str] = set()
        self.completed: set[str] = set()
        self.mutex = threading.Lock()

    def start(self):
        self.thread = threading.Thread(target=self._serve, name="scope-mailbox", daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.closed.is_set() and not self.server._closed.is_set():
            try:
                with self.mutex:
                    completed = tuple(self.completed)
                for nonce in completed:
                    if not lease_alive(self.directory / (nonce + ".lease")):
                        # A publisher holds admission while creating then locking
                        # its lease. Do not collect that brief pre-lock window.
                        if lease_alive(self.directory / "admission.lock"):
                            continue
                        _remove(self.directory, nonce)
                        with self.mutex:
                            self.completed.discard(nonce)
                entries = _entries(self.directory)
                for name in entries:
                    if not _FILE.fullmatch(name):
                        continue
                    nonce = name.split(".", 1)[0]
                    with self.mutex:
                        if nonce in self.active:
                            continue
                        completed = nonce in self.completed
                    if not lease_alive(self.directory / (nonce + ".lease")):
                        if lease_alive(self.directory / "admission.lock"):
                            continue
                        _remove(self.directory, nonce)
                        with self.mutex:
                            self.completed.discard(nonce)
                        continue
                    if completed:
                        continue
                    if not name.endswith(".request"):
                        continue
                    try:
                        message = ipc._json_object(_read(self.directory / name))
                    except Exception:
                        _remove(self.directory, nonce, lease=False)
                        continue
                    # A filename is correlation, not authentication. The common
                    # dispatcher validates token and reserves the nonce as well.
                    if message.get("nonce") != nonce:
                        _remove(self.directory, nonce, lease=False)
                        continue
                    slot = self.server._slots
                    control_only = False
                    if not slot.acquire(blocking=False):
                        slot = self.server._control_slot
                        control_only = True
                        if message.get("kind") not in self.server.control_kinds or not slot.acquire(blocking=False):
                            _remove(self.directory, nonce, lease=False)
                            continue
                    started = time.monotonic()
                    context = ipc.Context(started + self.server.request_timeout, started, nonce,
                                          _alive=lambda n=nonce: self._alive(n))
                    with self.server._mutex:
                        if self.server._closed.is_set():
                            slot.release()
                            return
                        self.server._contexts.add(context)
                    with self.mutex:
                        self.active.add(nonce)
                    try:
                        threading.Thread(target=self._handle, args=(message, context, slot, control_only),
                                         name="scope-mailbox-client", daemon=True).start()
                    except Exception:
                        self._finish(context, slot)
            except Exception:
                # Never print authentication material or filesystem request data.
                pass
            self.closed.wait(POLL_SECONDS)

    def _alive(self, nonce):
        return (not self.closed.is_set() and not self.server._closed.is_set()
                and (self.directory / (nonce + ".request")).exists()
                and lease_alive(self.directory / (nonce + ".lease")))

    def _handle(self, message, context, slot, control_only):
        try:
            self.server._dispatch(message, context, control_only,
                                  lambda frame: _write(self.directory / (context.nonce + ".response"), frame))
        except Exception:
            pass
        finally:
            self._finish(context, slot)

    def _finish(self, context, slot):
        context.cancelled.set()
        if not context.delivered:
            _remove(self.directory, context.nonce, lease=False)
        with self.mutex:
            if context.delivered:
                self.completed.add(context.nonce)
            self.active.discard(context.nonce)
        self.server._finish_context(context, slot)

    def close(self):
        self.closed.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=0.5)
        # A shutdown response was just published before after_reply closed the
        # server. Give the bounded polling client a chance to consume that frame,
        # just as socket close leaves already-sent bytes available to its peer.
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            with self.mutex:
                completed = tuple(self.completed)
            if not any(lease_alive(self.directory / (nonce + ".lease")) for nonce in completed):
                break
            time.sleep(POLL_SECONDS)
        try:
            # Delete only this server generation's recognized artifacts.
            for name in _entries(self.directory):
                if _FILE.fullmatch(name) or name == "admission.lock":
                    (self.directory / name).unlink(missing_ok=True)
            self.directory.rmdir()
        except OSError:
            pass
