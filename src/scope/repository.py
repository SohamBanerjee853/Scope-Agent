"""Bounded, privacy-filtered source evidence from a Git working tree.

All source, symbols and diffs are untrusted data, never agent instructions.
Unavailable files are not treated as deleted. Privacy filtering is conservative,
but cannot identify arbitrary secrets embedded in otherwise ordinary source.
"""

import ast
from datetime import datetime, timezone
import difflib
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat

from .runner import run

MAX_FILE_BYTES = 32 * 1024
MAX_TOTAL_BYTES = 512 * 1024
MAX_FILES = 160
MAX_SKIPPED = 2048
MAX_DIFF_BYTES = 64 * 1024
_EXCLUDED_PARTS = {
    ".git", ".hg", ".svn", ".ssh", ".aws", ".azure", ".config", ".codex",
    ".claude", ".agents", ".scope", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    "dist", "build", "coverage", ".next", ".nuxt", "vendor", "site-packages",
    "private", "secrets", "credentials",
}
_TEXT_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json",
    ".toml", ".yaml", ".yml", ".md", ".txt", ".rst", ".sh", ".ps1",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".java", ".go", ".rs", ".rb",
    ".php", ".swift", ".kt", ".sql", ".html", ".css", ".scss", ".vue",
    ".svelte", ".xml", ".ini", ".cfg", ".r", ".ex", ".exs", ".erl",
}
_TEXT_NAMES = {"dockerfile", "makefile", "cmakelists.txt", "justfile", ".gitignore", ".gitattributes"}
_PRIVATE_NAME = re.compile(r"(^|[._-])(secret|secrets|credential|credentials|password|token|private)([._-]|$)", re.I)
_SECRET_CONTENT = re.compile(
    r"-----BEGIN (?:[A-Z ]*PRIVATE KEY|OPENSSH PRIVATE KEY)-----"
    r"|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"
    r"|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
    r"|(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[\"']?\s*[:=]\s*[\"'][^\"'\r\n]{8,}[\"']",
    re.I,
)


class RepositoryError(RuntimeError):
    pass


def _git(directory: Path, *args):
    # An agent launched by Git may inherit GIT_DIR/GIT_WORK_TREE or an alternate
    # index/config. Bind inspection to the requested directory, without mutating
    # the process environment used by unrelated caller-side commands.
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("GIT_")}
    return run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
                "-C", str(directory), *args], cwd=directory, env=environment)


def find_root(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    result = _git(candidate, "rev-parse", "--show-toplevel")
    if not result.succeeded or not result.stdout.strip():
        raise RepositoryError("a readable Git working tree is required")
    return Path(result.stdout.rstrip("\r\n")).resolve()


def _path_reason(name: str) -> str | None:
    if not isinstance(name, str) or not name:
        return "unsafe_path"
    path = PurePosixPath(name)
    windows = PureWindowsPath(name)
    if (not path.parts or path.is_absolute() or windows.is_absolute() or windows.drive
            or ".." in path.parts or "\\" in name or any(ord(c) < 32 for c in name)):
        return "unsafe_path"
    parts = [part.lower() for part in path.parts]
    base = parts[-1]
    if any(part in _EXCLUDED_PARTS or part.endswith(".egg-info") for part in parts):
        return "excluded_directory"
    if (base.startswith(".env") or _PRIVATE_NAME.search(base)
            or base in {"id_rsa", "id_ed25519", ".npmrc", ".pypirc", ".netrc"}
            or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".jks", ".kdbx"}):
        return "private_path"
    if (base.endswith((".lock", "-lock.json", ".min.js", ".min.css", ".map", ".generated.py",
                       "-prompt.md", "_prompt.md", "-prompts.md", "_prompts.md"))
            or base in {"pnpm-lock.yaml", "poetry.lock", "cargo.lock", "go.sum"}):
        return "generated_or_local_noise"
    if path.suffix.lower() not in _TEXT_SUFFIXES and base not in _TEXT_NAMES:
        return "unsupported_source_type"
    return None


def _read_source(root: Path, name: str) -> tuple[bytes | None, str | None]:
    reason = _path_reason(name)
    if reason:
        return None, reason
    path = root / name
    try:
        cursor = root
        for part in PurePosixPath(name).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                return None, "symlink"
        if not path.resolve().is_relative_to(root):
            return None, "outside_repository"
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            return None, "not_regular_file"
        if before.st_size > MAX_FILE_BYTES:
            return None, "file_size_limit"
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                return None, "changed_during_read"
            data = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = path.stat()
        if not path.resolve().is_relative_to(root):
            return None, "outside_repository"
        if ((opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
            return None, "changed_during_read"
    except (OSError, ValueError):
        return None, "unavailable"
    if len(data) > MAX_FILE_BYTES:
        return None, "file_size_limit"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "binary_or_non_utf8"
    if "\0" in text:
        return None, "binary_or_non_utf8"
    if _SECRET_CONTENT.search(text):
        return None, "possible_secret_content"
    return data, None


def _symbols(text: str, name: str) -> list[dict]:
    if not name.endswith((".py", ".pyi")):
        return []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    symbols = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append({"name": node.name, "kind": type(node).__name__, "line": node.lineno,
                            "end_line": node.end_lineno, "citation": f"{name}:{node.lineno}"})
            if len(symbols) == 80:
                break
    return symbols


def source_lines(text: str, *, keepends: bool = False) -> list[str]:
    """Editor/Python source lines: only CRLF, CR and LF delimit lines.

    str.splitlines also splits Unicode paragraph separators and form feeds,
    which can make a citation point beyond the physical source file.
    """
    if keepends:
        return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text) if line]
    lines = re.split(r"\r\n|\r|\n", text)
    return lines[:-1] if not lines[-1] else lines


def _listing(result) -> tuple[list[str], bool]:
    if result.exit_code != 0 or result.timed_out or result.error or result.cleanup_incomplete:
        raise RepositoryError("Git source listing failed; no complete snapshot is available")
    # An incomplete final filename must not be interpreted as an actual path.
    names = result.stdout.split("\0")[:-1]
    return names, not (result.stdout_truncated or result.stderr_truncated)


def snapshot(path: str | Path) -> dict:
    root = find_root(path)
    names, complete = _listing(_git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"))
    ignored, ignored_complete = _listing(_git(root, "ls-files", "--cached", "--ignored", "--exclude-standard", "-z"))
    ignored_untracked, untracked_complete = _listing(_git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"))
    ignored_set = set(ignored)
    files, skipped = {}, {}
    omitted_skips = 0
    total = 0

    def skip(name, reason):
        nonlocal omitted_skips
        if len(skipped) < MAX_SKIPPED:
            skipped[name] = reason
        else:
            omitted_skips += 1

    for name in sorted(set(names)):
        if name in ignored_set:
            skip(name, "gitignored")
            continue
        if not ignored_complete:
            skip(name, "ignore_status_unavailable")
            continue
        reason = _path_reason(name)
        if reason:
            skip(name, reason)
            continue
        if len(files) >= MAX_FILES:
            skip(name, "file_count_limit")
            continue
        data, reason = _read_source(root, name)
        if reason:
            skip(name, reason)
            continue
        if total + len(data) > MAX_TOTAL_BYTES:
            skip(name, "total_size_limit")
            continue
        text = data.decode("utf-8")
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "text": text,
                       "bytes": len(data), "line_count": len(source_lines(text)),
                       "symbols": _symbols(text, name)}
        total += len(data)
    for name in sorted(set(ignored_untracked) - set(skipped)):
        skip(name, "gitignored")
    return {"root": str(root), "timestamp": datetime.now(timezone.utc).isoformat(),
            "files": files, "skipped": skipped, "skipped_overflow": omitted_skips,
            "listing_complete": complete and ignored_complete and untracked_complete,
            "total_bytes": total, "limits": {"file_bytes": MAX_FILE_BYTES,
            "total_bytes": MAX_TOTAL_BYTES, "files": MAX_FILES}, "untrusted_source": True}


def validate_citation(source: dict, citation: str) -> dict:
    """Return a versioned reference only for fully included source lines."""
    if not isinstance(citation, str):
        raise ValueError("citation must be file:line or file:start-end")
    match = re.fullmatch(r"(.+):(\d+)(?:-(\d+))?", citation)
    if not match:
        raise ValueError("citation must be file:line or file:start-end")
    name, first, last = match.groups()
    first, last = int(first), int(last or first)
    file = source.get("files", {}).get(name)
    if not file or not 1 <= first <= last <= file["line_count"]:
        raise ValueError("citation is outside included source")
    return {"path": name, "start_line": first, "end_line": last, "sha256": file["sha256"], "citation": citation}


def evidence_status(references: list[dict], source: dict) -> dict:
    if not isinstance(references, list) or not references:
        return {"status": "unverified", "reason": "no supporting source evidence", "files": []}
    findings = []
    for reference in references:
        if (not isinstance(reference, dict) or not isinstance(reference.get("path"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", str(reference.get("sha256", "")))):
            findings.append({"path": None, "status": "unverified", "reason": "invalid source reference"})
            continue
        name = reference["path"]
        file = source.get("files", {}).get(name)
        if not file:
            status, reason = "unverified", source.get("skipped", {}).get(name, "source unavailable in current snapshot")
        elif file["sha256"] != reference["sha256"]:
            status, reason = "stale", "supporting source hash changed"
        elif (type(reference.get("start_line")) is not int or type(reference.get("end_line")) is not int
              or not 1 <= reference["start_line"] <= reference["end_line"] <= file["line_count"]):
            status, reason = "unverified", "invalid source citation"
        else:
            try:
                citation = validate_citation(source, reference.get("citation"))
            except ValueError:
                citation = None
            if citation is None or any(citation[key] != reference[key] for key in ("path", "start_line", "end_line", "sha256")):
                status, reason = "unverified", "citation text does not match its source reference"
            else:
                status, reason = "current", "supporting source hash and lines match"
        findings.append({"path": name, "status": status, "reason": reason})
    statuses = {finding["status"] for finding in findings}
    overall = "unverified" if "unverified" in statuses else "stale" if "stale" in statuses else "current"
    return {"status": overall, "files": findings}


def changes(before: dict, after: dict) -> dict:
    """Diff captured source only; never read excluded content through git diff."""
    previous, current = before.get("files", {}), after.get("files", {})
    result = {"added": [], "modified": [], "unavailable": [], "diff": "", "diff_truncated": False}
    diff = bytearray()
    for name in sorted(previous.keys() | current.keys()):
        old, new = previous.get(name), current.get(name)
        if new is None:
            result["unavailable"].append({"path": name, "reason": after.get("skipped", {}).get(name, "not included")})
            continue
        if old and old["sha256"] == new["sha256"]:
            continue
        result["modified" if old else "added"].append(name)
        lines = difflib.unified_diff(source_lines(old["text"] if old else "", keepends=True),
                                     source_lines(new["text"], keepends=True), fromfile=f"before/{name}", tofile=f"after/{name}")
        for line in lines:
            data = line.encode("utf-8")
            remaining = MAX_DIFF_BYTES - len(diff)
            diff.extend(data[:remaining])
            if len(data) > remaining:
                result["diff_truncated"] = True
                break
    result["diff"] = diff.decode("utf-8", errors="ignore")
    return result
