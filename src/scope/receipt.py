"""Bounded receipt replay: decisions, execution evidence and understanding differ."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import time

from . import paths

MAX_LOG_BYTES = 32 * 1024 * 1024
MAX_RECEIPT_BYTES = 32 * 1024 * 1024
COUNTS = ("requests", "auto_allowed", "allowed_once", "denied", "hard_asks", "scopes_granted")
SUMMARY_FIELDS = {"tasks", "predictions", "observations", "executions", "next_tasks", "dispatches",
                  "skipped_checks", "deferred_tasks", "revocations", "meaning"}
MEANING = ("Scope permission decisions are not proof of execution. Native approval answers and commands "
           "outside Scope's observed requests remain unknown. Counts describe the available records only.")


def _home(home):
    return paths.scope_home() if home is None else Path(home).expanduser()


def _log_path(session_id, home=None):
    return _home(home) / "sessions" / paths.session_log(session_id).name


def receipt_path(session_id, *, home=None):
    return _home(home) / "receipts" / (paths.session_log(session_id).stem + ".json")


def _json(data):
    def invalid(value):
        raise ValueError("invalid JSON constant")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate evidence key")
            result[key] = value
        return result
    result = json.loads(data, parse_constant=invalid, object_pairs_hook=unique)
    # Reject overflowed floats and escaped surrogate data as well.
    json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return result


def _bounded_read(path, limit):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or Path(path).is_symlink():
            raise ValueError("expected a regular evidence file")
        return stream.read(limit + 1)


def read_events(session_id, *, home=None):
    """Apply the foundation record schema with an actual bounded read.

    log.read has neither a size cap nor a home override. This reader preserves
    its schema without changing the frozen shared module or process environment.
    """
    status = {"status": "complete", "malformed_records": 0, "max_bytes": MAX_LOG_BYTES}
    try:
        raw = _bounded_read(_log_path(session_id, home), MAX_LOG_BYTES)
    except FileNotFoundError:
        return [], {**status, "status": "missing"}
    except (OSError, ValueError):
        return [], {**status, "status": "unavailable"}
    if len(raw) > MAX_LOG_BYTES:
        status["status"] = "truncated"
    raw = raw[:MAX_LOG_BYTES]
    events = []
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            status["malformed_records"] += 1
            continue
        try:
            event = _json(line)
            if (not isinstance(event, dict) or not isinstance(event.get("ts"), str)
                    or not isinstance(event.get("event"), str) or not event["event"]
                    or not isinstance(event.get("fields"), dict)):
                raise ValueError("invalid event")
            if event["event"] != "receipt_written":
                events.append(event)
        except (ValueError, UnicodeError, RecursionError):
            status["malformed_records"] += 1
    return events, status


def _understanding(events, summarize=None):
    if summarize is None:
        try:
            module = importlib.import_module("scope.understanding_summary")
        except ModuleNotFoundError as exc:
            if exc.name != "scope.understanding_summary":
                raise
            return {"status": "pending_integration",
                    "meaning": "Arjun's understanding projection is unavailable. Understanding evidence has not been summarized."}
        summarize = module.summarize
    result = summarize(events)
    if not isinstance(result, dict) or not SUMMARY_FIELDS <= result.keys():
        raise ValueError("understanding projection does not satisfy the frozen contract")
    _json(json.dumps(result, allow_nan=False))
    return result


def _one(records, key):
    values = [record.get(key) for record in records if key in record]
    if not values:
        return None
    return values[0] if all(value == values[0] for value in values) else None


def _conflicting(records, key):
    values = [record.get(key) for record in records if key in record]
    return bool(values) and any(value != values[0] for value in values)


def project(session_id, events, *, understanding=None):
    """Pure permission projection, correlated by internal request/grant IDs."""
    groups = {}
    grants = {}
    ended = False
    last_status = "unknown"
    for index, event in enumerate(events):
        name, fields = event["event"], event["fields"]
        if name == "session_end":
            ended, last_status = True, "ended"
        elif name == "stop" and not ended:
            last_status = "turn_stopped"
        if name == "grant_created" and isinstance(fields.get("grant_id"), str):
            grants.setdefault(fields["grant_id"], fields)
        if name not in {"permission_request", "permission_decision", "permission_review",
                        "permission_review_decision", "permission_review_delivery"}:
            continue
        request_id = fields.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            # Never guess a correlation from matching command text or adjacency.
            if name != "permission_request":
                continue
            request_id = f"uncorrelated-{index}"
        groups.setdefault(request_id, {}).setdefault(name, []).append(fields)

    actions = []
    failed_deliveries = set()
    for request_id, group in groups.items():
        if any(record.get("delivered") is False for record in group.get("permission_review_delivery", [])):
            failed_deliveries.add(request_id)
        requests = group.get("permission_request", group.get("permission_review", []))
        if not requests:
            continue
        canonical = group.get("permission_decision", [])
        reviews = group.get("permission_review_decision", [])
        decisions = canonical if canonical else reviews
        behavior = _one(decisions, "behavior")
        if not isinstance(behavior, str) or behavior not in {"allow", "deny"} or request_id.startswith("uncorrelated-"):
            behavior = "unknown"
        contexts = group.get("permission_request", []) + group.get("permission_review", [])
        if any(_conflicting(contexts, key) for key in ("command", "cwd", "shell", "agent_id", "tier")):
            behavior = "unknown"
        if not canonical and request_id in failed_deliveries:
            behavior = "unknown"
        tier = _one(requests, "tier")
        review_action = _one(reviews, "action")
        automatic = tier == "T1" or review_action == "scope_allow" or _one(canonical, "smoke") is True
        category = ("allowed_once" if behavior == "allow" and review_action == "allowed_once" else
                    "auto_allowed" if behavior == "allow" and automatic else "denied" if behavior == "deny" else
                    "hard_ask" if tier == "T3" else "unknown")
        actions.append({"request_id": request_id, "command": _one(requests, "command"),
                        "cwd": _one(requests, "cwd"), "shell": _one(requests, "shell"),
                        "agent_id": _one(requests, "agent_id"), "tier": tier,
                        "decision": behavior, "action": category,
                        "decision_source": "hook" if canonical else "watcher" if reviews else "unknown",
                        "delivery": "failed" if request_id in failed_deliveries else "unknown",
                        "execution": "unknown", "native_decision": "unknown"})
    counts = dict.fromkeys(COUNTS, 0)
    counts["requests"] = len(actions)
    for action in actions:
        for category in ("auto_allowed", "allowed_once", "denied"):
            counts[category] += int(action["action"] == category)
        counts["hard_asks"] += int(action["tier"] == "T3")
    counts["scopes_granted"] = sum(isinstance(grant.get("request_id"), str)
                                  and grant["request_id"] not in failed_deliveries for grant in grants.values())
    return {"schema_version": 1, "session_id": session_id, "status": last_status,
            "counts": counts, "actions": actions, "understanding": _understanding(events, understanding),
            "meaning": MEANING}


def build(session_id, *, home=None, understanding=None, audit=False, transcript_path=None, rules=(), host="codex"):
    events, input_status = read_events(session_id, home=home)
    result = project(session_id, events, understanding=understanding)
    result["input"] = input_status
    result["host"] = host
    if audit:
        from .coverage import audit as audit_coverage
        result["coverage"] = audit_coverage(events, transcript_path=transcript_path, rules=rules, host=host)
    else:
        result["coverage"] = {"status": "not_requested", "meaning": "Native handling and execution remain unknown; no policy audit was run."}
    digest_input = {"session_id": session_id, "events": events, "input": input_status, "understanding": result["understanding"],
                    "coverage": result["coverage"], "host": host}
    result["event_fingerprint"] = hashlib.sha256(json.dumps(digest_input, sort_keys=True, ensure_ascii=True,
                                                          allow_nan=False).encode()).hexdigest()
    return result


def load(path):
    """Read existing schema-1 receipts, including those without understanding."""
    raw = _bounded_read(Path(path), MAX_RECEIPT_BYTES)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("receipt too large")
    value = _json(raw)
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or not isinstance(value.get("actions"), list) or not isinstance(value.get("counts"), dict)
            or not all(isinstance(action, dict) for action in value["actions"])):
        raise ValueError("invalid schema-1 receipt")
    if any(type(count) is not int or count < 0 for count in value["counts"].values()):
        raise ValueError("invalid receipt counts")
    return value


@contextmanager
def _writer_lock(path):
    from .ipc import _private_open, _lock, _unlock, AlreadyRunningError

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = _private_open(path, create=True, writable=True)
    locked = False
    deadline = time.monotonic() + 2
    try:
        while not locked:
            try:
                _lock(fd)
                locked = True
            except AlreadyRunningError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("receipt writer busy")
                time.sleep(0.01)
        yield
    finally:
        if locked:
            _unlock(fd)
        os.close(fd)


def write(session_id, *, home=None, understanding=None, **options):
    destination = receipt_path(session_id, home=home)
    with _writer_lock(destination.with_suffix(".lock")):
        result = build(session_id, home=home, understanding=understanding, **options)
        try:
            cached = load(destination)
        except (OSError, ValueError):
            cached = None
        if cached and {key: value for key, value in cached.items() if key != "written_at"} == result:
            return cached
        result["written_at"] = datetime.now(timezone.utc).isoformat()
        data = (json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode()
        if len(data) > MAX_RECEIPT_BYTES:
            raise ValueError("receipt too large")
        temporary = destination.with_name("." + destination.name + "." + secrets.token_hex(8) + ".tmp")
        from .ipc import _private_open

        try:
            fd = _private_open(temporary, create=True, exclusive=True, writable=True)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        # This is a projection file; raw event evidence is never rewritten here.
        return result


def render(result):
    from .watch_ui import display_text

    counts = result["counts"]
    lines = [f"Scope receipt: {display_text(result.get('session_id', 'unknown'))}",
             f"Requests: {counts.get('requests', 0)}; automatic allows: {counts.get('auto_allowed', 0)}; "
             f"allowed once: {counts.get('allowed_once', 0)}; denied: {counts.get('denied', 0)}; "
             f"hard asks: {counts.get('hard_asks', 0)}; scopes granted: {counts.get('scopes_granted', 0)}",
             display_text(result.get("meaning", MEANING))]
    for action in result["actions"]:
        lines.append(f"  {display_text(action.get('decision', 'unknown'))}: {display_text(action.get('command', 'unknown'))} — execution unknown")
    understanding = result.get("understanding")
    lines.append("Understanding: " + display_text(understanding.get("meaning", "see JSON evidence")
                                                if isinstance(understanding, dict) else "absent in this legacy receipt"))
    lines.append("Evidence input: " + display_text(result.get("input", {}).get("status", "legacy/unknown")))
    lines.append("Native coverage: " + display_text(result.get("coverage", {}).get("meaning", "unknown")))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scope receipt", description=__doc__)
    parser.add_argument("session_id", nargs="?", default=os.environ.get("CODEX_THREAD_ID"))
    parser.add_argument("--home", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--rebuild", action="store_true", help="rebuild even when only a legacy receipt is available")
    parser.add_argument("--audit", action="store_true", help="estimate current native policy using explicit inputs")
    parser.add_argument("--transcript", type=Path)
    parser.add_argument("--rules", type=Path, action="append", default=[])
    parser.add_argument("--host", choices=("codex", "claude", "unknown"), default="codex")
    args = parser.parse_args(argv)
    if not args.session_id:
        parser.error("session_id or CODEX_THREAD_ID is required")
    if (args.transcript or args.rules) and not args.audit:
        parser.error("--transcript/--rules require --audit")
    try:
        legacy_path = receipt_path(args.session_id, home=args.home)
        if not args.rebuild and not args.audit and not _log_path(args.session_id, args.home).exists() and legacy_path.exists():
            result = load(legacy_path)
            if result.get("session_id") != args.session_id:
                raise ValueError("stored receipt belongs to another session")
        else:
            result = write(args.session_id, home=args.home, audit=args.audit, understanding=None,
                           transcript_path=args.transcript, rules=args.rules, host=args.host)
        print(json.dumps(result, ensure_ascii=True, indent=2) if args.json else render(result))
        return 0
    except Exception:
        print("scope receipt: evidence unavailable or receipt could not be built", file=sys.stderr)
        return 1
