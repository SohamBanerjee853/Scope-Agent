"""Bounded subprocess capture in the caller's environment, never a sandbox.

Consent and command classification belong to the calling workflow. This primitive
does not grant permission or accept command strings for shell evaluation.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

MAX_OUTPUT_BYTES = 64 * 1024
DEFAULT_TIMEOUT = 20
MAX_TIMEOUT = 300
_WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class RunResult:
    argv: list[str]
    cwd: str
    timestamp: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: str | None = None
    cleanup_incomplete: bool = False

    @property
    def succeeded(self) -> bool:
        return (self.exit_code == 0 and not self.timed_out and not self.error
                and not self.stdout_truncated and not self.stderr_truncated
                and not self.cleanup_incomplete)

    def to_dict(self) -> dict:
        return asdict(self)


class _Capture:
    def __init__(self):
        self.data = bytearray()
        self.truncated = False
        self.error = None
        self.lock = threading.Lock()

    def drain(self, stream) -> None:
        try:
            while chunk := stream.read(8192):
                with self.lock:
                    remaining = MAX_OUTPUT_BYTES - len(self.data)
                    self.data.extend(chunk[:remaining])
                    self.truncated |= len(chunk) > remaining
        except OSError as exc:
            self.error = type(exc).__name__
        finally:
            stream.close()

    def snapshot(self) -> tuple[str, bool]:
        with self.lock:
            # Replacement characters must not expand the returned UTF-8 cap.
            encoded = bytes(self.data).decode("utf-8", errors="replace").encode("utf-8")
            return encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore"), self.truncated or len(encoded) > MAX_OUTPUT_BYTES


def _terminate_tree(process: subprocess.Popen) -> bool:
    """Best effort only: a descendant can escape a process group/tree."""
    success = True
    if _WINDOWS:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        taskkill = os.path.join(system_root, "System32", "taskkill.exe")
        try:
            result = subprocess.run(
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=2,
            )
            success = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            success = False
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            success = False
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            success = False
    return success


def run(argv: list[str], *, cwd: str | Path, timeout: float = DEFAULT_TIMEOUT) -> RunResult:
    if (not isinstance(argv, list) or not argv or len(argv) > 256
            or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
            or not argv[0] or sum(len(arg.encode("utf-8")) for arg in argv) > 128 * 1024):
        raise ValueError("argv must be a bounded nonempty list of strings without NUL bytes")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError("timeout must be greater than zero and at most 300 seconds")
    # Windows can invoke batch files via a shell even with shell=False.
    if argv[0].lower().endswith((".bat", ".cmd")):
        raise ValueError("batch executables are unsupported; use an explicitly classified native executable")
    argv = list(argv)
    directory = str(Path(cwd).expanduser().resolve())
    timestamp = datetime.now(timezone.utc).isoformat()
    base = {"argv": argv, "cwd": directory, "timestamp": timestamp}
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if _WINDOWS else {"start_new_session": True}
    try:
        process = subprocess.Popen(
            argv, cwd=directory, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, **options,
        )
    except OSError as exc:
        return RunResult(**base, exit_code=None, stdout="", stderr="", error=f"spawn failed: {type(exc).__name__}")
    captures = [_Capture(), _Capture()]
    threads = [threading.Thread(target=capture.drain, args=(stream,), daemon=True)
               for capture, stream in zip(captures, (process.stdout, process.stderr))]
    for thread in threads:
        thread.start()
    timed_out = False
    cleanup_incomplete = False
    error = None
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        cleanup_incomplete = not _terminate_tree(process)
    except BaseException:
        _terminate_tree(process)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        raise
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        cleanup_incomplete = True
        error = "child did not exit after cleanup"
    deadline = time.monotonic() + 0.5
    for thread in threads:
        thread.join(max(0, deadline - time.monotonic()))
    if any(thread.is_alive() for thread in threads):
        cleanup_incomplete |= not _terminate_tree(process)
        deadline = time.monotonic() + 0.5
        for thread in threads:
            thread.join(max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            cleanup_incomplete = True
            error = "output pipes remained open after cleanup; observation is incomplete"
    if any(capture.error for capture in captures):
        error = "output stream read failed"
    stdout, stdout_truncated = captures[0].snapshot()
    stderr, stderr_truncated = captures[1].snapshot()
    return RunResult(**base, exit_code=process.returncode, stdout=stdout, stderr=stderr,
                     timed_out=timed_out, stdout_truncated=stdout_truncated,
                     stderr_truncated=stderr_truncated, error=error,
                     cleanup_incomplete=cleanup_incomplete)
