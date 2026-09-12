"""Resolve local record paths without creating directories during lookup."""

import hashlib
import os
from pathlib import Path


def scope_home() -> Path:
    return Path(os.environ.get("SCOPE_HOME", "~/.scope")).expanduser().resolve()


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()


def sessions_dir() -> Path:
    return scope_home() / "sessions"


def session_log(session_id: str) -> Path:
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 512:
        raise ValueError("session_id must be a nonempty string of at most 512 characters")
    # A digest avoids traversal, sanitization collisions, and Windows device names.
    key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return sessions_dir() / f"{key}.jsonl"
