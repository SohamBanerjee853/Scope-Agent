"""Atomic per-project JSON with OS locks, conservative reads and no database."""

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

from .paths import scope_home

MAX_STATE_BYTES = 8 * 1024 * 1024
_WINDOWS = os.name == "nt"


class StateError(RuntimeError):
    """State cannot be trusted; preserve the original for explicit recovery."""


def project_id(root: str | Path) -> str:
    identity = os.path.normcase(str(Path(root).resolve()))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def project_dir(root: str | Path) -> Path:
    return scope_home() / "projects" / project_id(root)


def _try_lock(stream) -> None:
    if _WINDOWS:
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(stream) -> None:
    if _WINDOWS:
        import msvcrt
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def project_lock(root: str | Path, *, timeout: float = 5):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
        raise ValueError("lock timeout must be finite and nonnegative")
    directory = project_dir(root)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Lock a persistent sidecar, not the state inode replaced by os.replace.
    fd = os.open(directory / "state.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+b", buffering=0) as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"\0")
        deadline = time.monotonic() + timeout
        while True:
            try:
                _try_lock(stream)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise StateError("project state lock is busy or unavailable") from exc
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            _unlock(stream)


def _read(root: str | Path) -> dict | None:
    path = project_dir(root) / "state.json"
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_STATE_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StateError("project state is unavailable") from exc
    if len(data) > MAX_STATE_BYTES:
        raise StateError("project state exceeds 8 MiB")
    try:
        state = json.loads(data, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise StateError("corrupt project state; original file preserved") from exc
    if (not isinstance(state, dict) or type(state.get("schema_version")) is not int
            or state["schema_version"] != 1 or state.get("project_id") != project_id(root)):
        raise StateError("incomplete, unsupported or mismatched project state")
    return state


def _write(root: str | Path, state: dict) -> None:
    if state.get("project_id") != project_id(root) or type(state.get("schema_version")) is not int or state["schema_version"] != 1:
        raise StateError("state identity/schema mismatch")
    data = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_STATE_BYTES:
        raise StateError("project state exceeds 8 MiB; previous state preserved")
    directory = project_dir(root)
    fd, temporary = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / "state.json")
        if os.name != "nt":
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def load(root: str | Path) -> dict | None:
    with project_lock(root):
        return _read(root)


def update(root: str | Path, change, *, timeout: float = 5) -> dict:
    """Run change(old_or_none) under one lock, then atomically save its result."""
    with project_lock(root, timeout=timeout):
        state = change(_read(root))
        if not isinstance(state, dict):
            raise StateError("state update must return an object")
        _write(root, state)
        return state
