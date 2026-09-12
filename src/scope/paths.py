"""Portable paths; merely resolving a path never creates directories."""

import hashlib
import os
from pathlib import Path
import re


def scope_home() -> Path:
    return Path(os.environ.get("SCOPE_HOME") or Path.home() / ".scope").expanduser()


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def sessions_dir() -> Path:
    return scope_home() / "sessions"


def session_log(session_id: str) -> Path:
    """Keep safe IDs readable; hash others to avoid traversal and collisions.

    Prefixing also avoids Windows device names (CON, AUX, etc.). Hashing
    noncanonical IDs avoids case-insensitive filesystem aliases.
    """
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a nonempty string")
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,119}", session_id):
        filename = "session-" + session_id
    else:
        filename = "sha256-" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return sessions_dir() / (filename + ".jsonl")
