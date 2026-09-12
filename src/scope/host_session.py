"""Private launch identity, registered only by an actual native SessionStart.

Registration is independent of watcher readiness. State is local same-user
coordination, not protection from the owner deliberately rewriting their files.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import secrets
import stat
import time

from . import ipc

MAX_RECORD = 16 * 1024
_LAUNCH_KEYS = {"schema_version", "launch_id", "host", "cwd", "home", "permissions",
                "understanding", "parent_session_ids", "created_at"}
_SESSION_KEYS = {"schema_version", "launch_id", "host", "cwd", "session_id", "status",
                 "source", "agent_type", "permission_requests", "started_at", "ended_at",
                 "closure_source", "native_end_at", "inferred_end_at"}


class HostSessionError(RuntimeError):
    """The current launch cannot establish the requested native identity."""


def _text(value, limit=512):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise HostSessionError("invalid launch identity text")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise HostSessionError("launch identity is not valid UTF-8") from exc
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value):
    return isinstance(value, str) and datetime.fromisoformat(value).utcoffset() is not None


def _canonical(value):
    try:
        path = Path(_text(str(value), 4096)).expanduser()
        if not path.is_absolute():
            raise HostSessionError("launch paths must be absolute")
        return path.resolve(strict=True)
    except HostSessionError:
        raise
    except Exception as exc:
        raise HostSessionError("launch path is unavailable") from exc


def _same_path(left, right):
    return os.path.normcase(str(_canonical(left))) == os.path.normcase(str(_canonical(right)))


def _unredirected(path):
    # Launcher-created homes use canonical paths. Inspect each existing ancestor
    # before creation too, so a redirected parent cannot cause stray writes.
    if ".." in path.parts:
        raise HostSessionError("launch home cannot traverse parent directories")
    for candidate in (*reversed(path.parents), path):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise HostSessionError("launch home contains a redirected directory")


def _directory(home):
    path = Path(home).expanduser()
    if not path.is_absolute():
        raise HostSessionError("launch home must be absolute")
    _unredirected(path)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or (os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077))):
        raise HostSessionError("launch home must be a private directory without redirection")
    return path.resolve(strict=True)


def _read(path):
    fd = ipc._private_open(path)
    try:
        if os.fstat(fd).st_size > MAX_RECORD:
            raise HostSessionError("launch record exceeds its bound")
        data = os.read(fd, MAX_RECORD + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_RECORD:
        raise HostSessionError("launch record exceeds its bound")
    return ipc._json_object(data)


def _write(path, value, *, exclusive=False):
    data = ipc._encode(value)
    if len(data) > MAX_RECORD:
        raise HostSessionError("launch record exceeds its bound")
    temporary = path if exclusive else path.with_name("." + path.name + "." + secrets.token_hex(12))
    try:
        fd = ipc._private_open(temporary, create=True, exclusive=True, writable=True)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if not exclusive:
            os.replace(temporary, path)
    finally:
        if not exclusive:
            temporary.unlink(missing_ok=True)


@contextmanager
def _locked(home):
    fd = ipc._private_open(home / "host-session.lock", create=True, writable=True)
    locked = False
    deadline = time.monotonic() + 2
    try:
        while not locked:
            try:
                ipc._lock(fd)
                locked = True
            except ipc.AlreadyRunningError:
                if time.monotonic() >= deadline:
                    raise HostSessionError("native session state is busy")
                time.sleep(0.01)
        yield
    finally:
        if locked:
            ipc._unlock(fd)
        os.close(fd)


def create_launch(home, *, launch_id, host, cwd, permissions=True, understanding=True,
                  parent_session_ids=()):
    """Create one immutable launch definition; never overwrite an existing run."""
    try:
        _text(launch_id, 120)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,119}", launch_id) or host not in {"codex", "claude"}:
            raise HostSessionError("invalid launch id or host")
        if type(permissions) is not bool or type(understanding) is not bool or not (permissions or understanding):
            raise HostSessionError("at least one workflow must be enabled")
        if not isinstance(parent_session_ids, (list, tuple)) or len(parent_session_ids) > 16:
            raise HostSessionError("invalid parent identities")
        parents = list(dict.fromkeys(_text(value) for value in parent_session_ids))
        project = _canonical(cwd)
        if not project.is_dir():
            raise HostSessionError("launch project is not a directory")
        target = Path(home).expanduser()
        if not target.is_absolute():
            raise HostSessionError("launch home must be absolute")
        _unredirected(target)
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = _directory(target)
        if (target / "host-session.json").exists():
            raise HostSessionError("native state already exists in this run")
        record = {"schema_version": 1, "launch_id": launch_id, "host": host, "cwd": str(project),
                  "home": str(target), "permissions": permissions, "understanding": understanding,
                  "parent_session_ids": parents, "created_at": _now()}
        _write(target / "launch.json", record, exclusive=True)
        return record
    except HostSessionError:
        raise
    except Exception as exc:
        raise HostSessionError("launch definition could not be created") from exc


def read_launch(home):
    """Validate a private launch definition without needing native registration."""
    try:
        target = _directory(home)
        record = _read(target / "launch.json")
        if (set(record) != _LAUNCH_KEYS or type(record["schema_version"]) is not int
                or record["schema_version"] != 1 or record["host"] not in {"codex", "claude"}
                or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,119}", _text(record["launch_id"], 120))
                or type(record["permissions"]) is not bool or type(record["understanding"]) is not bool
                or not (record["permissions"] or record["understanding"])
                or not _same_path(record["home"], target) or str(target) != record["home"]
                or str(_canonical(record["cwd"])) != record["cwd"] or not Path(record["cwd"]).is_dir()
                or not _timestamp(record["created_at"])):
            raise HostSessionError("invalid or mismatched launch definition")
        parents = record["parent_session_ids"]
        if not isinstance(parents, list) or len(parents) > 16 or len(set(parents)) != len(parents):
            raise HostSessionError("invalid parent identities")
        for parent in parents:
            _text(parent)
        return record
    except HostSessionError:
        raise
    except Exception as exc:
        raise HostSessionError("launch definition is missing, invalid or inaccessible") from exc


def current_launch():
    """Return None only outside a launch; partial or mismatched markers fail."""
    launch_id, host = os.environ.get("SCOPE_LAUNCH_ID"), os.environ.get("SCOPE_HOST")
    if launch_id is None and host is None:
        return None
    if not launch_id or not host or not os.environ.get("SCOPE_HOME"):
        raise HostSessionError("incomplete launch environment; restart with scope codex or scope claude")
    record = read_launch(os.environ["SCOPE_HOME"])
    if record["launch_id"] != launch_id or record["host"] != host:
        raise HostSessionError("launch environment does not match this run")
    return record


def _launch():
    result = current_launch()
    if result is None:
        raise HostSessionError("this operation requires a Scope launch")
    return result


def _native(launch):
    try:
        record = _read(Path(launch["home"]) / "host-session.json")
    except FileNotFoundError:
        return None
    except Exception as exc:
        raise HostSessionError("native session state is inaccessible") from exc
    try:
        if (set(record) != _SESSION_KEYS or type(record["schema_version"]) is not int
                or record["schema_version"] != 1
                or any(record[key] != launch[key] for key in ("launch_id", "host", "cwd"))
                or record["status"] not in {"open", "closed"}
                or type(record["permission_requests"]) is not int or record["permission_requests"] < 0):
            raise HostSessionError("invalid native session state")
        native = record["session_id"]
        if native is not None:
            _text(native)
            if native in launch["parent_session_ids"] or not _timestamp(record["started_at"]):
                raise HostSessionError("invalid native session identity")
            sources = {"startup", "resume", "clear", "compact"} | ({"fork"} if launch["host"] == "claude" else set())
            if record["source"] not in sources:
                raise HostSessionError("invalid native startup source")
        elif (record["status"] != "closed" or record["started_at"] is not None
              or record["source"] is not None or record["permission_requests"] != 0
              or record["native_end_at"] is not None or record["closure_source"] != "launcher"):
            raise HostSessionError("missing native session identity")
        if record["agent_type"] is not None:
            _text(record["agent_type"])
        if record["status"] == "open":
            if any(record[key] is not None for key in ("ended_at", "closure_source", "native_end_at", "inferred_end_at")):
                raise HostSessionError("open native state contains closure evidence")
        elif (record["closure_source"] not in {"native", "launcher"} or not _timestamp(record["ended_at"])
              or (record["native_end_at"] is None and record["inferred_end_at"] is None)):
            raise HostSessionError("closed native state lacks closure evidence")
        for key in ("native_end_at", "inferred_end_at"):
            if record[key] is not None and not _timestamp(record[key]):
                raise HostSessionError("invalid closure timestamp")
        if record["status"] == "closed" and record["closure_source"] != (
                "native" if record["native_end_at"] else "launcher"):
            raise HostSessionError("closure source differs from its evidence")
        return record
    except HostSessionError:
        raise
    except Exception as exc:
        raise HostSessionError("invalid native session state") from exc


def _claims(launch, expected=None, *, host=None, cwd=None, descendants=False):
    if host is not None and host != launch["host"]:
        raise HostSessionError("hook host differs from the launch")
    if cwd is not None and not _same_path(cwd, launch["cwd"]):
        child, root = os.path.normcase(str(_canonical(cwd))), os.path.normcase(str(_canonical(launch["cwd"])))
        if not descendants or not Path(child).is_relative_to(Path(root)):
            raise HostSessionError("project differs from the launch")
    if expected is not None:
        _text(expected)
        if expected in launch["parent_session_ids"]:
            raise HostSessionError("parent session identity cannot be used by a launch")


def require_session(expected=None, *, host=None, cwd=None):
    """Validate a current open identity and any task/request's claimed identity."""
    launch = current_launch()
    if launch is None:
        return _text(expected) if expected is not None else None
    _claims(launch, expected, host=host, cwd=cwd, descendants=True)
    state = _native(launch)
    if state is None or state["status"] != "open":
        raise HostSessionError("native startup is missing or closed; review hooks and restart this launch")
    native = state["session_id"]
    if expected is not None and expected != native:
        raise HostSessionError("session differs from this launch's native identity")
    if launch["host"] == "codex" and os.environ.get("CODEX_THREAD_ID") not in (None, native):
        raise HostSessionError("current Codex thread differs from registered native identity")
    return native


def session_id():
    """Resolve only registered launch identity; legacy fallback stays with callers."""
    return require_session()


def register(session_id, *, host, cwd, source="startup", agent_id=None, agent_type=None):
    launch = _launch()
    _text(session_id)
    _claims(launch, session_id, host=host, cwd=cwd)
    sources = {"startup", "resume", "clear", "compact"} | ({"fork"} if host == "claude" else set())
    if source not in sources or agent_id is not None:
        raise HostSessionError("unsupported startup or subagent cannot own launch identity")
    if agent_type is not None:
        _text(agent_type)
    home = Path(launch["home"])
    with _locked(home):
        previous = _native(launch)
        if previous is not None:
            if previous["status"] != "open" or previous["session_id"] != session_id:
                raise HostSessionError("another or closed native session already owns this run")
            return previous
        record = {"schema_version": 1, "launch_id": launch["launch_id"], "host": host,
                  "cwd": launch["cwd"], "session_id": session_id, "status": "open", "source": source,
                  "agent_type": agent_type, "permission_requests": 0, "started_at": _now(),
                  "ended_at": None, "closure_source": None, "native_end_at": None, "inferred_end_at": None}
        _write(home / "host-session.json", record)
        from . import log
        log.append_at(home, session_id, "host_connected", host=host, launch_id=launch["launch_id"],
                      cwd=launch["cwd"], source=source, native=True)
        return record


def observe_permission(expected, *, host=None, cwd=None):
    launch = _launch()
    with _locked(Path(launch["home"])):
        require_session(expected, host=host, cwd=cwd)
        state = _native(launch)
        if not launch["permissions"]:
            raise HostSessionError("permission workflow is disabled")
        state["permission_requests"] += 1
        _write(Path(launch["home"]) / "host-session.json", state)
        return state


def close_session(expected=None, *, host=None, cwd=None, inferred=False, reason=None, home=None):
    """Close once per evidence source; inferred closure never invents a native ID."""
    launch = _launch() if home is None else read_launch(home)
    _claims(launch, expected, host=host, cwd=cwd, descendants=True)
    if type(inferred) is not bool or (reason is not None and not isinstance(reason, str)):
        raise HostSessionError("invalid closure evidence")
    home = Path(launch["home"])
    with _locked(home):
        state = _native(launch)
        if state is None:
            if not inferred or expected is not None:
                raise HostSessionError("native startup is not registered")
            state = {"schema_version": 1, "launch_id": launch["launch_id"], "host": launch["host"],
                     "cwd": launch["cwd"], "session_id": None, "status": "closed", "source": None,
                     "agent_type": None, "permission_requests": 0, "started_at": None,
                     "ended_at": None, "closure_source": None, "native_end_at": None, "inferred_end_at": None}
        if expected is not None and state["session_id"] != expected:
            raise HostSessionError("closure belongs to another native session")
        if not inferred and state["session_id"] is None:
            raise HostSessionError("native closure cannot fabricate startup identity")
        key = "inferred_end_at" if inferred else "native_end_at"
        if state[key] is not None:
            return state
        ended = _now()
        state.update(status="closed", ended_at=state["ended_at"] or ended)
        state[key] = ended
        state["closure_source"] = "native" if state["native_end_at"] else "launcher"
        _write(home / "host-session.json", state)
        if state["session_id"] is not None:
            from . import log
            log.append_at(home, state["session_id"], "agent_process" if inferred else "session_end",
                          host=launch["host"], launch_id=launch["launch_id"],
                          status="launcher_inferred_exit" if inferred else "ended", native=not inferred)
        return state


def status(*, home=None):
    launch = _launch() if home is None else read_launch(home)
    state = _native(launch)
    count = state["permission_requests"] if state else 0
    return {"launch_id": launch["launch_id"], "host": launch["host"],
            "session_id": state["session_id"] if state else None,
            "status": state["status"] if state else "waiting", "permission_requests": count,
            "permission_status": ("disabled" if not launch["permissions"] else
                                  "requests observed" if count else "configured/waiting"),
            "closure_source": state["closure_source"] if state else None}
