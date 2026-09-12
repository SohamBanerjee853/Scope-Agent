"""Authenticated, bounded loopback transport for human decisions.

This module transports JSON only: handlers own every logical decision. A token is
same-user coordination, not a boundary against another process owned by that user.
No sent request is retried. Valid launches may use a private file mailbox when
TCP connection establishment fails. The lifetime lock is never unlinked;
removing a lock inode would allow two watchers to own different locks at once.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import select
import socket
import stat
import threading
import time
from typing import Any, Callable

from scope.paths import scope_home

MAX_FRAME = 128 * 1024
MAX_ENDPOINT = 4096
MAX_SEEN_NONCES = 8192
CONTROL_FRAME_TIMEOUT = 0.5
ENDPOINT_NAME = "watch.json"
LOCK_NAME = "watch.lock"
_TOKEN = re.compile(r"[0-9a-f]{64}\Z")
_NONCE = re.compile(r"[0-9a-f]{32}\Z")


class AlreadyRunningError(RuntimeError):
    """Another watcher holds this home's lifetime lock."""


def _home(home: str | os.PathLike[str] | None) -> Path:
    return scope_home() if home is None else Path(home).expanduser()


def _json_object(data: bytes) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("nonfinite JSON value")

    result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                        parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError("JSON object required")
    return result


def _encode(message: dict[str, Any]) -> bytes:
    if not isinstance(message, dict):
        raise ValueError("JSON object required")
    data = json.dumps(message, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8") + b"\n"
    if len(data) > MAX_FRAME:
        raise ValueError("message too large")
    return data


def _private_open(path: Path, *, create: bool = False, exclusive: bool = False,
                  writable: bool = False) -> int:
    """Open an owned regular file without following links, checking privacy."""
    if os.name == "nt":
        return _windows_open(path, create=create, exclusive=exclusive,
                             writable=writable)
    flags = os.O_RDWR if writable else os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if create:
        flags |= os.O_CREAT
    if exclusive:
        flags |= os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        named = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(named.st_mode)
                or info.st_nlink != 1 or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)):
            raise OSError("endpoint must be an owned private regular file")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_endpoint(home: Path) -> dict[str, Any]:
    fd = _private_open(home / ENDPOINT_NAME)
    try:
        if os.fstat(fd).st_size > MAX_ENDPOINT:
            raise ValueError("endpoint too large")
        data = os.read(fd, MAX_ENDPOINT + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_ENDPOINT:
        raise ValueError("endpoint too large")
    endpoint = _json_object(data)
    launched = set(endpoint) == {"port", "token", "launch_id", "mailbox"}
    if (set(endpoint) != {"port", "token"} and not launched
            or not (type(endpoint.get("port")) is int and 1 <= endpoint["port"] <= 65535
                    or launched and endpoint.get("port") is None)
            or not isinstance(endpoint["token"], str)
            or not _TOKEN.fullmatch(endpoint["token"])):
        raise ValueError("invalid endpoint")
    if launched and (not isinstance(endpoint["launch_id"], str) or not 1 <= len(endpoint["launch_id"]) <= 512
                     or not isinstance(endpoint["mailbox"], str) or not _NONCE.fullmatch(endpoint["mailbox"])):
        raise ValueError("invalid launch endpoint")
    return endpoint


def _receive(sock: socket.socket, deadline: float,
             cancelled: threading.Event | None = None) -> dict[str, Any]:
    data = bytearray()
    while len(data) < MAX_FRAME:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or (cancelled is not None and cancelled.is_set()):
            raise TimeoutError("request ended")
        sock.settimeout(min(remaining, 0.1))
        try:
            chunk = sock.recv(min(8192, MAX_FRAME - len(data)))
        except socket.timeout:
            continue
        if not chunk:
            raise ConnectionError("disconnected")
        data.extend(chunk)
        if b"\n" in chunk:
            frame, rest = bytes(data).split(b"\n", 1)
            if rest:
                raise ValueError("one frame per connection required")
            return _json_object(frame)
    raise ValueError("message too large")


def exchange(message: dict[str, Any], timeout: float = 105, *,
             home: str | os.PathLike[str] | None = None) -> dict[str, Any] | None:
    """Send once and validate the authenticated reply; all failures return None."""
    try:
        if (not isinstance(message, dict) or {"token", "nonce"} & message.keys()
                or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            return None
        deadline = time.monotonic() + min(timeout, 105)
        directory = _home(home)
        endpoint = _read_endpoint(directory)
        launched = "launch_id" in endpoint
        if launched or "SCOPE_LAUNCH_ID" in os.environ or "SCOPE_HOST" in os.environ:
            from . import host_session

            launch = host_session.current_launch()
            if (not launched or launch is None or launch["launch_id"] != endpoint["launch_id"]
                    or Path(launch["home"]) != directory.absolute()):
                return None
        token = endpoint["token"]
        nonce = secrets.token_hex(16)
        frame = _encode({**message, "token": token, "nonce": nonce})
        sock = None
        try:
            if endpoint["port"] is None:
                raise ConnectionError("launch watcher has no TCP listener")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(max(0.001, min(1.0 if launched else 105, deadline - time.monotonic())))
            sock.connect(("127.0.0.1", endpoint["port"]))
        except OSError:
            if sock is not None:
                sock.close()
            if not launched:
                return None
            from . import mailbox

            reply = mailbox.exchange(directory, endpoint, frame, nonce, deadline)
        else:
            # Crossing this point forbids all retry/fallback, even if sendall
            # raises after sending only part of the frame or the response fails.
            with sock:
                sock.settimeout(max(0.001, deadline - time.monotonic()))
                sock.sendall(frame)
                reply = _receive(sock, deadline)
        if not isinstance(reply, dict):
            return None
        if (not isinstance(reply.get("token"), str)
                or not isinstance(reply.get("nonce"), str)
                or not hmac.compare_digest(reply["token"], token)
                or not hmac.compare_digest(reply["nonce"], nonce)):
            return None
        return {key: value for key, value in reply.items()
                if key not in {"token", "nonce"}}
    except Exception:
        # Hook callers must always be able to abstain on any transport failure.
        return None


@dataclass(eq=False)
class Context:
    """Request lifetime shared with the handler and its human UI.

    ``state`` is the optional admission callback's result. ``reply_lock`` and
    ``reply_valid`` let a handler serialize its final reply with revocation.
    ``before_reply`` commits a decision under that lock; ``reply_finalize`` can
    discard an undelivered grant before releasing it. ``delivered`` means sendall
    succeeded, not that a human or host consumed or executed the response.
    ``after_reply`` runs after socket cleanup. All callbacks must be fast and
    must never read human input.
    """

    deadline: float
    started_at: float
    nonce: str
    cancelled: threading.Event = field(default_factory=threading.Event)
    state: Any = None
    reply_lock: Any = None
    reply_valid: Callable[[], bool] | None = None
    before_reply: Callable[[], bool] | None = None
    reply_finalize: Callable[[bool], None] | None = None
    after_reply: Callable[[], None] | None = None
    delivered: bool = False
    _socket: socket.socket | None = field(default=None, repr=False)
    _alive: Callable[[], bool] | None = field(default=None, repr=False)

    def active(self) -> bool:
        if self.cancelled.is_set() or time.monotonic() >= self.deadline:
            self.cancelled.set()
            return False
        if self._alive is not None:
            try:
                if not self._alive():
                    self.cancelled.set()
                    return False
            except Exception:
                self.cancelled.set()
                return False
        if self._socket is not None:
            try:
                readable, _, exceptional = select.select(
                    [self._socket], [], [self._socket], 0)
                if exceptional or readable:
                    # EOF is a disconnect; extra input is an invalid second frame.
                    if readable:
                        self._socket.recv(1, socket.MSG_PEEK)
                    self.cancelled.set()
                    return False
            except (OSError, ValueError):
                self.cancelled.set()
                return False
        return True


class Server:
    """Single-instance listener with a bounded number of daemon workers.

    The worker bound includes clients still sending a frame. There is no work
    queue. An opt-in control reader permits one additional authenticated revoke
    or shutdown when normal workers are full; its frame read is capped at 0.5 s.
    Other bodies never reach the handler through that reader. This preserves
    cancellation during ordinary saturation, not availability against flooding.
    The accept backlog is bounded too; excess clients are closed. A handler
    must poll Context.active() while awaiting its UI and before mutating grants.
    close() cancels contexts without waiting for an uncooperative human reader.
    """

    def __init__(self, handler: Callable[[dict[str, Any], Context], dict[str, Any]], *,
                 home: str | os.PathLike[str] | None = None,
                 request_timeout: float = 95, max_clients: int = 16,
                 admission: Callable[[dict[str, Any], Context], Any] | None = None,
                 control_kinds: frozenset[str] = frozenset(), launch_id: str | None = None):
        if (isinstance(request_timeout, bool)
                or not isinstance(request_timeout, (int, float))
                or not math.isfinite(request_timeout) or not 0 < request_timeout <= 95):
            raise ValueError("request_timeout must be in (0, 95]")
        if type(max_clients) is not int or not 1 <= max_clients <= 128:
            raise ValueError("max_clients must be an integer in [1, 128]")
        if (not isinstance(control_kinds, (set, frozenset))
                or not control_kinds <= {"revoke", "shutdown"}):
            raise ValueError("overflow controls may only be revoke or shutdown")
        self.handler = handler
        self.admission = admission
        self.home = _home(home)
        self.endpoint_path = self.home / ENDPOINT_NAME
        self.request_timeout = request_timeout
        self.max_clients = max_clients
        self.control_kinds = frozenset(control_kinds)
        self.launch_id = launch_id
        self.address: tuple[str, int] | None = None
        self._token = secrets.token_hex(32)
        self._lock_fd: int | None = None
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._mutex = threading.RLock()
        self._contexts: set[Context] = set()
        self._slots = threading.BoundedSemaphore(max_clients)
        self._control_slot = threading.BoundedSemaphore(1)
        self._mailbox = None
        self._seen_nonces: set[str] = set()

    def start(self) -> Server:
        with self._mutex:
            if self._lock_fd is not None:
                return self
            if self._closed.is_set():
                raise RuntimeError("a closed server cannot restart")
            if self.launch_id is not None:
                from . import host_session

                launch = host_session.read_launch(self.home)
                if launch["launch_id"] != self.launch_id or Path(launch["home"]) != self.home.absolute():
                    raise ValueError("mailbox requires the matching private launch home")
            self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
            info = self.home.lstat()
            if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or (os.name != "nt" and
                        (info.st_uid != os.getuid() or info.st_mode & 0o022))):
                raise OSError("watch home must be an owned directory without shared writes")
            self._lock_fd = _private_open(self.home / LOCK_NAME,
                                          create=True, writable=True)
            try:
                _lock(self._lock_fd)
            except BaseException:
                os.close(self._lock_fd)
                self._lock_fd = None
                raise
            temporary = self.home / (".watch-" + secrets.token_hex(16) + ".tmp")
            try:
                try:
                    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self._listener = listener
                    if os.name == "nt":
                        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    listener.bind(("127.0.0.1", 0))
                    listener.listen(self.max_clients)
                    listener.settimeout(0.1)
                    self.address = listener.getsockname()
                except OSError:
                    if self._listener is not None:
                        self._listener.close()
                        self._listener = None
                    if self.launch_id is None:
                        raise
                endpoint = {"port": self.address[1] if self.address is not None else None,
                            "token": self._token}
                if self.launch_id is not None:
                    from . import mailbox

                    generation = secrets.token_hex(16)
                    self._mailbox = mailbox.Listener(self, generation)
                    endpoint.update(launch_id=self.launch_id, mailbox=generation)
                fd = _private_open(temporary, create=True, exclusive=True, writable=True)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(_encode(endpoint))
                    stream.flush()
                    os.fsync(stream.fileno())
                # Only the lock holder may replace a stale endpoint. Replacing a
                # symlink removes the link itself; no target is opened or written.
                os.replace(temporary, self.endpoint_path)
                if self._listener is not None:
                    self._thread = threading.Thread(target=self._serve,
                                                    name="scope-ipc", daemon=True)
                    self._thread.start()
                if self._mailbox is not None:
                    self._mailbox.start()
                return self
            except BaseException:
                try:
                    temporary.unlink(missing_ok=True)
                finally:
                    self.close()
                raise

    def _serve(self) -> None:
        listener = self._listener
        assert listener is not None
        while not self._closed.is_set():
            try:
                client, peer = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            started_at = time.monotonic()
            if peer[0] != "127.0.0.1":
                client.close()
                continue
            slot, control_only = self._slots, False
            if not slot.acquire(blocking=False):
                slot, control_only = self._control_slot, True
                if not self.control_kinds or not slot.acquire(blocking=False):
                    client.close()
                    continue
            timeout = min(self.request_timeout, CONTROL_FRAME_TIMEOUT) if control_only else self.request_timeout
            context = Context(started_at + timeout, started_at, "",
                              _socket=client)
            with self._mutex:
                if self._closed.is_set():
                    client.close()
                    slot.release()
                    return
                self._contexts.add(context)
            try:
                threading.Thread(target=self._handle, args=(client, context, slot, control_only),
                                 name="scope-ipc-client", daemon=True).start()
            except Exception:
                client.close()
                with self._mutex:
                    self._contexts.discard(context)
                slot.release()

    def _handle(self, client: socket.socket, context: Context,
                slot: threading.BoundedSemaphore, control_only: bool) -> None:
        try:
            message = _receive(client, context.deadline, context.cancelled)

            def send(frame):
                client.settimeout(min(0.5, max(0.001, context.deadline - time.monotonic())))
                client.sendall(frame)

            self._dispatch(message, context, control_only, send)
        except Exception:
            # Authentication material and untrusted bodies must never be logged.
            pass
        finally:
            context.cancelled.set()
            client.close()
            self._finish_context(context, slot)

    def _dispatch(self, message, context, control_only, send):
        """One authentication/admission/reply path shared by both transports."""
        token, nonce = message.pop("token", None), message.pop("nonce", None)
        if (not isinstance(token, str) or not _TOKEN.fullmatch(token)
                or not hmac.compare_digest(token, self._token)
                or not isinstance(nonce, str) or not _NONCE.fullmatch(nonce)
                or not context.active()):
            return
        context.nonce = nonce
        if control_only:
            if message.get("kind") not in self.control_kinds:
                return
            context.deadline = context.started_at + self.request_timeout
        with self._mutex:
            # Keep lifetime tombstones, not a TTL/LRU that permits an old frame
            # to become a fresh approval. Capacity exhaustion fails closed.
            if nonce in self._seen_nonces or len(self._seen_nonces) >= MAX_SEEN_NONCES:
                return
            self._seen_nonces.add(nonce)
        if self.admission is not None:
            context.state = self.admission(message, context)
        if not context.active():
            return
        reply = self.handler(message, context)
        lock = context.reply_lock if context.reply_lock is not None else nullcontext()
        with lock:
            try:
                if (not context.active() or (context.reply_valid is not None and not context.reply_valid())):
                    return
                if not isinstance(reply, dict) or {"token", "nonce"} & reply.keys():
                    return
                frame = _encode({**reply, "token": self._token, "nonce": nonce})
                if context.before_reply is not None and not context.before_reply():
                    return
                if not context.active():
                    return
                send(frame)
                context.delivered = True
            finally:
                if context.reply_finalize is not None:
                    context.reply_finalize(context.delivered)

    def _finish_context(self, context, slot):
        with self._mutex:
            self._contexts.discard(context)
        slot.release()
        if context.after_reply is not None:
            try:
                context.after_reply()
            except Exception:
                pass

    def close(self) -> None:
        with self._mutex:
            self._closed.set()
            if self._listener is not None:
                self._listener.close()
                self._listener = None
            for context in tuple(self._contexts):
                context.cancelled.set()
                if context._socket is not None:
                    try:
                        context._socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    context._socket.close()
            if self._lock_fd is not None:
                try:
                    if _read_endpoint(self.home)["token"] == self._token:
                        self.endpoint_path.unlink(missing_ok=True)
                except Exception:
                    pass
                finally:
                    fd = self._lock_fd
                    self._lock_fd = None
                    try:
                        _unlock(fd)
                    finally:
                        os.close(fd)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=0.5)
        if self._mailbox is not None:
            self._mailbox.close()

    def __enter__(self) -> Server:
        return self.start()

    def __exit__(self, *args) -> None:
        self.close()


def _lock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        raise AlreadyRunningError("a watcher already owns this Scope home") from error


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def _windows_open(path: Path, *, create: bool, exclusive: bool, writable: bool) -> int:
    """Create/check a protected owner-only DACL using Windows APIs, never chmod.

    Security descriptors are attached at creation, before any token is written.
    GetSecurityInfo reads the opened handle so checks do not follow another path.
    See https://learn.microsoft.com/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo
    """
    import ctypes as c
    from ctypes import wintypes as w
    import msvcrt

    kernel = c.WinDLL("kernel32", use_last_error=True)
    security = c.WinDLL("advapi32", use_last_error=True)
    pointer = c.c_void_p
    ppointer = c.POINTER(pointer)

    def api(dll, name, args, result=w.BOOL):
        function = getattr(dll, name)
        function.argtypes, function.restype = args, result
        return function

    current_process = api(kernel, "GetCurrentProcess", [], w.HANDLE)
    close_handle = api(kernel, "CloseHandle", [w.HANDLE])
    free = api(kernel, "LocalFree", [pointer], pointer)
    open_token = api(security, "OpenProcessToken", [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)])
    token_info = api(security, "GetTokenInformation",
                     [w.HANDLE, c.c_int, pointer, w.DWORD, c.POINTER(w.DWORD)])
    sid_string = api(security, "ConvertSidToStringSidW", [pointer, c.POINTER(w.LPWSTR)])
    descriptor_from_string = api(security, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                                 [w.LPCWSTR, w.DWORD, ppointer, c.POINTER(w.DWORD)])
    get_security = api(security, "GetSecurityInfo",
                        [w.HANDLE, c.c_int, w.DWORD, ppointer, ppointer, ppointer,
                         ppointer, ppointer], w.DWORD)
    control = api(security, "GetSecurityDescriptorControl",
                   [pointer, c.POINTER(w.WORD), c.POINTER(w.DWORD)])
    equal_sid = api(security, "EqualSid", [pointer, pointer])
    get_ace = api(security, "GetAce", [pointer, w.DWORD, ppointer])

    class Attributes(c.Structure):
        _fields_ = [("length", w.DWORD), ("descriptor", pointer), ("inherit", w.BOOL)]

    create_file = api(kernel, "CreateFileW", [w.LPCWSTR, w.DWORD, w.DWORD,
                       c.POINTER(Attributes), w.DWORD, w.DWORD, w.HANDLE], w.HANDLE)
    token = w.HANDLE()
    descriptor, actual_descriptor = pointer(), pointer()
    text_sid = w.LPWSTR()
    handle = None
    fd = None
    try:
        if not open_token(current_process(), 0x0008, c.byref(token)):
            raise c.WinError(c.get_last_error())
        size = w.DWORD()
        token_info(token, 1, None, 0, c.byref(size))
        data = c.create_string_buffer(size.value)
        if not token_info(token, 1, data, size, c.byref(size)):
            raise c.WinError(c.get_last_error())
        sid = c.cast(data, ppointer).contents
        if not sid_string(sid, c.byref(text_sid)):
            raise c.WinError(c.get_last_error())
        sddl = "O:" + text_sid.value + "D:P(A;;FA;;;" + text_sid.value + ")"
        if not descriptor_from_string(sddl, 1, c.byref(descriptor), None):
            raise c.WinError(c.get_last_error())
        attributes = Attributes(c.sizeof(Attributes), descriptor, False)
        disposition = (1 if exclusive else 4) if create else 3  # CREATE_NEW/OPEN_ALWAYS/OPEN_EXISTING
        access = 0x80000000 | 0x00020000  # GENERIC_READ + READ_CONTROL
        if writable:
            access |= 0x40000000
        handle = create_file(str(path), access, 7, c.byref(attributes), disposition,
                              0x00200000, None)  # FILE_FLAG_OPEN_REPARSE_POINT
        if handle == c.c_void_p(-1).value:
            handle = None
            raise c.WinError(c.get_last_error())
        owner, dacl = pointer(), pointer()
        error = get_security(handle, 1, 0x00000005, c.byref(owner), None,
                              c.byref(dacl), None, c.byref(actual_descriptor))
        if error:
            raise c.WinError(error)
        flags, revision = w.WORD(), w.DWORD()
        if (not owner or not dacl or not equal_sid(owner, sid)
                or not control(actual_descriptor, c.byref(flags), c.byref(revision))
                or not flags.value & 0x1000):  # SE_DACL_PROTECTED
            raise OSError("endpoint must have a protected owner-only DACL")
        # ACL header has AceCount at byte 4. A single noninherited allow ACE for
        # the current owner is the exact descriptor Scope creates.
        count = c.cast(dacl.value + 4, c.POINTER(w.WORD)).contents.value
        ace = pointer()
        if count != 1 or not get_ace(dacl, 0, c.byref(ace)):
            raise OSError("endpoint DACL grants access beyond its owner")
        header = c.string_at(ace, 4)
        if (header[0] != 0 or header[1] != 0
                or c.cast(ace.value + 4, c.POINTER(w.DWORD)).contents.value != 0x001F01FF
                or not equal_sid(ace.value + 8, sid)):
            raise OSError("endpoint DACL grants access beyond its owner")
        fd = msvcrt.open_osfhandle(handle, (os.O_RDWR if writable else os.O_RDONLY) | os.O_BINARY)
        handle = None  # The file descriptor now owns the handle.
        info, named = os.fstat(fd), path.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(named.st_mode)
                or getattr(named, "st_file_attributes", 0) & 0x400
                or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)):
            raise OSError("endpoint must be an owned private regular file")
        result, fd = fd, None
        return result
    finally:
        if fd is not None:
            os.close(fd)
        if handle is not None:
            close_handle(handle)
        if actual_descriptor:
            free(actual_descriptor)
        if descriptor:
            free(descriptor)
        if text_sid:
            free(c.cast(text_sid, pointer))
        if token:
            close_handle(token)
