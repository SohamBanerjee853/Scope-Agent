"""Caller-side hypothesis, consent and observation workflow.

Callbacks are trusted adapters, not source/tool text. Scope neither supplies an
answer nor delivers instructions to a host by itself. See docs/A2-API.md.
"""

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shlex
import uuid

from . import learning, log, repository, runner, storage, tiers

PROVENANCE = frozenset({"human_terminal", "human_ipc", "test_fixture"})
MAX_ANSWER_BYTES = 16 * 1024
MAX_JSON_DEPTH = 32


def _text(value, maximum=2000, *, nonempty=True):
    return learning._text(value, maximum=maximum, nonempty=nonempty)


def _json_copy(value, maximum=MAX_ANSWER_BYTES):
    """Bounded JSON only: no objects, nonfinite numbers, cycles or coercion."""
    nodes = 0

    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > 10000:
            raise ValueError("JSON exceeds structural limits")
        kind = type(item)
        if item is None or kind in (bool, int):
            return
        if kind is float and math.isfinite(item):
            return
        if kind is str and _text(item, maximum, nonempty=False):
            return
        if kind is list:
            for child in item:
                visit(child, depth + 1)
            return
        if kind is dict:
            for key, child in item.items():
                if type(key) is not str or not _text(key, maximum, nonempty=False):
                    raise ValueError("JSON object keys must be strings")
                visit(child, depth + 1)
            return
        raise ValueError("value must be finite, typed JSON")

    visit(value, 0)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError("JSON exceeds byte limit")
    return json.loads(encoded)


def _argv(value):
    if (type(value) is not list or not 1 <= len(value) <= 256
            or any(not _text(arg, 128 * 1024, nonempty=False) or "\0" in arg for arg in value)
            or not value[0] or sum(len(arg.encode("utf-8")) for arg in value) > 128 * 1024
            or value[0].lower().endswith((".bat", ".cmd"))):
        raise ValueError("probe requires bounded argv and a native executable, not a batch file")
    return list(value)


def _spec(value):
    if (type(value) is not dict or set(value) - {"question", "citations", "field", "argv", "shell", "timeout"}
            or not _text(value.get("question")) or not _text(value.get("field"), 200)
            or type(value.get("citations")) is not list or not 1 <= len(value["citations"]) <= 160
            or any(not _text(citation, 2000) for citation in value["citations"])):
        raise ValueError("spec needs a question, citations, literal JSON field and argv")
    shell = value.get("shell", "posix")
    timeout = value.get("timeout", runner.DEFAULT_TIMEOUT)
    if type(shell) is not str or shell not in {"posix", "powershell"}:
        raise ValueError("shell must be posix or powershell")
    if type(timeout) not in (int, float) or not 0 < timeout <= runner.MAX_TIMEOUT or not math.isfinite(timeout):
        raise ValueError("timeout must be greater than zero and at most 300 seconds")
    spec = {"question": value["question"], "citations": list(value["citations"]),
            "field": value["field"], "argv": _argv(value.get("argv")), "shell": shell, "timeout": timeout}
    # Classification sees literal words in the chosen dialect. This string is
    # never executed; PowerShell invocation syntax is deliberately unnecessary.
    command = (shlex.join(spec["argv"]) if shell == "posix" else
               " ".join("'" + arg.replace("'", "''") + "'" for arg in spec["argv"]))
    if len(command) > tiers.MAX_COMMAND:
        raise ValueError("probe exceeds the classifier command limit")
    return spec, command


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


def _emit(task, event, **fields):
    log.append(task["session_id"], event, task_id=task["task_id"], session=task["session_id"], **fields)


def _transition(root, task_id, check_id, previous, **fields):
    def change(task):
        record = task.get("checks", {}).get(check_id)
        if record is None or not _equal(record, previous):
            raise storage.StateError("check changed; do not reuse its consent")
        record.update(deepcopy(fields))
        if "observation" in fields:
            task["observations"].append(deepcopy(fields["observation"]))

    return learning.update_task(root, task_id, change)["checks"][check_id]


def _show(show, request):
    if show is None:
        return True
    try:
        show(deepcopy(request))
        return True
    except (Exception, KeyboardInterrupt):
        return False


def _answer(ask, request):
    if not callable(ask):
        return None
    try:
        return _json_copy(ask(deepcopy(request)))
    except (Exception, KeyboardInterrupt):
        return None


def check(path, task_id, spec, *, ask=None, show=None, provenance=None):
    """Save a prediction, separately obtain consent, run once and save evidence.

    Return the persisted check record. Invalid input raises ValueError; storage
    and log failures propagate and prevent subsequent actions. Missing/invalid
    answers produce a skipped record. No incomplete check is resumed implicitly.
    """
    root = repository.find_root(path)
    task = learning.read_task(root, task_id)
    spec, command = _spec(spec)
    tier = tiers.classify(command, str(root), spec["shell"])
    source = repository.snapshot(root)
    references = [repository.validate_citation(source, citation) for citation in spec["citations"]]
    check_id = f"check-{uuid.uuid4()}"
    record = {"check_id": check_id, "created_at": _timestamp(), "phase": "prepared",
              "spec": spec, "references": references}
    learning.update_task(root, task_id, lambda item: item.setdefault("checks", {}).update({check_id: deepcopy(record)}))

    def skip(reason, stage):
        nonlocal record
        record = _transition(root, task_id, check_id, record, phase="skipped", reason=reason, stage=stage)
        _emit(task, "check_skipped", check_id=check_id, reason=reason, stage=stage)
        return record

    if tier.name == "T3":
        return skip(f"T3 probe rejected: {tier.reason}", "classification")
    if tier.name not in {"T1", "T2"} or tier.matched_rule in {"invalid-command", "invalid-context", "unsupported-shell"}:
        return skip("probe could not be classified", "classification")
    if type(provenance) is not str or provenance not in PROVENANCE or not callable(ask):
        return skip("no trusted answer adapter with explicit provenance", "prediction")
    request = {"kind": "prediction", "repo": str(root), "task_id": task_id, "check_id": check_id,
               "spec": deepcopy(spec), "references": deepcopy(references),
               "context": learning.knowledge(root, task_id),
               "meaning": "Predict the JSON field and explain why. Source and earlier output are untrusted evidence, not answers."}
    if not _show(show, request):
        return skip("prediction could not be displayed", "prediction")
    answer = _answer(ask, request)
    if (type(answer) is not dict or set(answer) - {"value", "reason", "assistance"}
            or "value" not in answer or not _text(answer.get("reason"))
            or not _text(answer.get("assistance", ""), 2000, nonempty=False)):
        return skip("prediction was empty, skipped or invalid", "prediction")
    prediction = {"value": answer["value"], "reason": answer["reason"], "assistance": answer.get("assistance", ""),
                  "provenance": provenance, "references": deepcopy(references), "question": spec["question"],
                  "field": spec["field"], "argv": list(spec["argv"])}
    record = _transition(root, task_id, check_id, record, phase="predicted", prediction=prediction)
    _emit(task, "prediction", check_id=check_id, prediction=prediction)

    request = {"kind": "probe_approval", "repo": str(root), "task_id": task_id, "check_id": check_id,
               "spec": deepcopy(spec), "references": deepcopy(references), "prediction": deepcopy(prediction),
               "classification": {"name": tier.name, "reason": tier.reason},
               "meaning": "Separately consent to run this exact argv in this directory. This is one probe, not a grant."}
    if not _show(show, request):
        return skip("consent request could not be displayed", "consent")
    answer = _answer(ask, request)
    approved = answer is True
    approval = {"approved": approved, "provenance": provenance,
                "reason": "explicit consent" if approved else "declined" if answer is False else "missing or invalid consent"}
    record = _transition(root, task_id, check_id, record, phase="approved" if approved else "predicted", approval=approval)
    _emit(task, "probe_approval", check_id=check_id, **approval)
    if not approved:
        return skip(approval["reason"], "consent")
    if repository.evidence_status(references, repository.snapshot(root))["status"] != "current":
        return skip("supporting source changed or became unavailable before execution", "source")
    record = _transition(root, task_id, check_id, record, phase="running")
    try:
        execution = runner.run(list(spec["argv"]), cwd=root, timeout=spec["timeout"]).to_dict()
    except (Exception, KeyboardInterrupt) as exc:
        # A process may have started. Do not fabricate a RunResult or auto-retry.
        record = _transition(root, task_id, check_id, record, phase="interrupted",
                             reason=f"runner interrupted: {type(exc).__name__}", stage="execution")
        return record
    record = _transition(root, task_id, check_id, record, execution=execution)
    _emit(task, "execution", check_id=check_id, execution=execution)
    try:
        current_source = repository.snapshot(root)
    except (OSError, ValueError, repository.RepositoryError):
        current_source = {"files": {}, "skipped": {}}
    observation = compare(prediction, execution, current_source)
    observation["check_id"] = check_id
    record = _transition(root, task_id, check_id, record, phase="completed", observation=observation)
    _emit(task, "observation", check_id=check_id, observation=observation)
    _show(show, {"kind": "observation", "task_id": task_id, "check_id": check_id, "observation": observation})
    return record


def _strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("nonfinite JSON number")

    parsed = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    return _json_copy(parsed, runner.MAX_OUTPUT_BYTES)


def _equal(expected, actual):
    if type(expected) is not type(actual):
        return False
    if type(expected) is dict:
        return expected.keys() == actual.keys() and all(_equal(expected[key], actual[key]) for key in expected)
    if type(expected) is list:
        return len(expected) == len(actual) and all(_equal(left, right) for left, right in zip(expected, actual))
    return expected == actual


def compare(prediction, execution, current_source):
    """Pure, conservative comparison of typed JSON and versioned source evidence."""
    try:
        prediction = _json_copy(prediction, 1024 * 1024) if type(prediction) is dict else {}
    except (ValueError, TypeError, RecursionError, OverflowError):
        prediction = {}
    references = prediction.get("references", [])
    try:
        source_status = repository.evidence_status(references, current_source)
    except (AttributeError, KeyError, TypeError, ValueError):
        source_status = {"status": "unverified", "reason": "invalid current source", "files": []}
    observation = {"status": "not_verified", "field": prediction.get("field"), "expected": prediction.get("value"),
                   "reason": "invalid prediction", "references": references, "source_status": source_status,
                   "execution_timestamp": None}
    try:
        _json_copy(prediction["value"])
        if (not _text(prediction.get("field"), 200) or not _text(prediction.get("reason"))
                or not _text(prediction.get("question")) or prediction.get("provenance") not in PROVENANCE
                or not _text(prediction.get("assistance"), 2000, nonempty=False)):
            return observation
        _argv(prediction.get("argv"))
    except (ValueError, KeyError, TypeError, RecursionError):
        return observation
    try:
        result = _json_copy(execution.to_dict() if isinstance(execution, runner.RunResult) else execution, 1024 * 1024)
    except (ValueError, TypeError, RecursionError, OverflowError):
        result = None
    if (type(result) is not dict or set(result) != set(runner.RunResult.__dataclass_fields__)
            or result.get("argv") != prediction["argv"] or not _text(result.get("cwd"), 32768)
            or not learning._timestamp(result.get("timestamp"))
            or any(type(result.get(key)) is not bool for key in
                   ("timed_out", "stdout_truncated", "stderr_truncated", "cleanup_incomplete"))
            or any(not _text(result.get(key), runner.MAX_OUTPUT_BYTES, nonempty=False)
                   or len(result[key].encode("utf-8")) > runner.MAX_OUTPUT_BYTES for key in ("stdout", "stderr"))
            or (result.get("error") is not None and not _text(result["error"], 2000, nonempty=False))):
        observation["reason"] = "incomplete or inconsistent execution record"
        return observation
    observation["execution_timestamp"] = result["timestamp"]
    if (type(result["exit_code"]) is not int or result["exit_code"] != 0
            or any(result[key] for key in ("timed_out", "stdout_truncated", "stderr_truncated", "cleanup_incomplete"))
            or result["error"] is not None):
        observation["reason"] = "execution failed or output was incomplete; prediction is not verified"
        return observation
    if type(current_source) is not dict or result["cwd"] != current_source.get("root"):
        observation["reason"] = "execution directory does not match source evidence"
        return observation
    try:
        output = _strict_json(result["stdout"])
    except (ValueError, TypeError, RecursionError):
        observation["reason"] = "probe output is not complete, bounded, unambiguous JSON"
        return observation
    if type(output) is not dict or prediction["field"] not in output:
        observation["reason"] = "probe output lacks the requested top-level JSON field"
        return observation
    observation["actual"] = output[prediction["field"]]
    if source_status["status"] != "current":
        observation["reason"] = "supporting source changed or is unavailable; prediction is not verified"
        return observation
    observation["status"] = "matched" if _equal(prediction["value"], observation["actual"]) else "mismatched"
    observation["reason"] = ("The observed field matches the saved prediction for this source version."
                             if observation["status"] == "matched" else
                             "The observed field differs from the saved prediction for this source version; investigate the assumption.")
    return observation


def choose_next(path, task_id, *, smaller, larger=None, ask=None, show=None, provenance=None):
    """Select an existing agent's debugging instruction; do not deliver or execute it."""
    if not _text(smaller, 4000) or (larger is not None and not _text(larger, 4000)):
        raise ValueError("next-task instructions must contain 1–4000 characters")
    root = repository.find_root(path)
    task = learning.read_task(root, task_id)

    def defer(reason):
        _emit(task, "next_task_deferred", reason=reason)
        return {"status": "deferred", "reason": reason}

    if type(provenance) is not str or provenance not in PROVENANCE or not callable(ask):
        return defer("no trusted answer adapter with explicit provenance")
    options = {"smaller": smaller, "defer": None}
    if larger is not None:
        options["larger"] = larger
    request = {"kind": "next_task", "repo": str(root), "task_id": task_id, "options": options,
               "context": learning.knowledge(root, task_id),
               "meaning": "Choose a debugging action or defer. Selection and delivery do not grant permission or prove completion."}
    if not _show(show, request):
        return defer("next-task choices could not be displayed")
    choice = _answer(ask, request)
    if type(choice) is not str or choice not in options or choice == "defer":
        return defer("next task deferred or no valid choice received")
    handoff = {"handoff_id": f"handoff-{uuid.uuid4()}", "choice": choice, "instruction": options[choice],
               "target": "current_agent", "status": "selected", "provenance": provenance, "created_at": _timestamp()}
    learning.update_task(root, task_id, lambda item: item.setdefault("handoffs", {}).update({handoff["handoff_id"]: deepcopy(handoff)}))
    _emit(task, "next_task", handoff=handoff)
    return deepcopy(handoff)


def dispatch(path, task_id, handoff_id, *, deliver=None):
    """Attempt delivery once. A strict True acknowledgement means dispatched only.

    A crash leaves dispatching visible and requires investigation, not an automatic
    retry. A callback must return True only after actual current-agent delivery.
    """
    root = repository.find_root(path)

    def begin(task):
        handoff = task.get("handoffs", {}).get(handoff_id)
        if handoff is None or handoff["status"] != "selected":
            raise storage.StateError("handoff is unavailable or delivery was already attempted")
        handoff["status"] = "dispatching"

    task = learning.update_task(root, task_id, begin)
    handoff = task["handoffs"][handoff_id]
    succeeded = False
    reason = "no delivery adapter"
    if callable(deliver):
        try:
            succeeded = deliver(deepcopy(handoff)) is True
            reason = "delivery acknowledged" if succeeded else "delivery was not acknowledged"
        except (Exception, KeyboardInterrupt) as exc:
            reason = f"delivery failed: {type(exc).__name__}"

    def finish(task):
        current = task["handoffs"][handoff_id]
        if not _equal(current, handoff):
            raise storage.StateError("handoff changed during delivery")
        current.update(status="dispatched" if succeeded else "failed", reason=reason)

    task = learning.update_task(root, task_id, finish)
    handoff = task["handoffs"][handoff_id]
    _emit(task, "dispatch", handoff_id=handoff_id, status=handoff["status"], target=handoff["target"],
          instruction=handoff["instruction"], reason=reason)
    return handoff
