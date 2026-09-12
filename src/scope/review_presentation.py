"""Bounded presentation data for human review; never answers or authority.

The A2 engine's request stays unchanged. This copy supplies labeled terminal
forms while the reply retains its existing canonical string representation.
Literal arguments and complete next-step instructions are never truncated.
"""

import json
import math

MAX_PRESENTATION_BYTES = 48 * 1024
MAX_ESSENTIAL_CHARS = 15 * 1024
MAX_DETAILS = 8192
_KINDS = {"prediction", "probe_approval", "next_task"}
_COMMON = {"version", "kind", "task_id", "details"}
_PROBE = {"question", "field", "execution"}


def _text(value, maximum, *, empty=False):
    if (type(value) is not str or len(value) > maximum or "\0" in value
            or not empty and not value.strip()):
        raise ValueError("invalid review presentation text")
    value.encode("utf-8", errors="strict")
    return value


def _json_copy(value, maximum):
    # Count values before encoding, rejecting cycles/deep or non-JSON objects.
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 32 or count > 10000:
            raise ValueError("review presentation exceeds structural limits")
        if item is None or type(item) in (bool, int):
            continue
        if type(item) is float and math.isfinite(item):
            continue
        if type(item) is str:
            item.encode("utf-8", errors="strict")
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("review presentation keys must be text")
                key.encode("utf-8", errors="strict")
                pending.append((child, depth + 1))
        else:
            raise ValueError("review presentation must contain finite typed JSON")
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError("review presentation exceeds byte limit")
    return json.loads(encoded)


def validate(value, repo):
    """Return an independent validated form, or refuse it before human input."""
    try:
        _text(repo, 4096)
        result = _json_copy(value, MAX_PRESENTATION_BYTES)
        if (type(result) is not dict or type(result.get("version")) is not int
                or result["version"] != 1 or result.get("kind") not in _KINDS):
            raise ValueError("unsupported review presentation version or kind")
        kind = result["kind"]
        expected = _COMMON | ({"options"} if kind == "next_task" else _PROBE)
        if kind == "probe_approval":
            expected |= {"classification", "saved_prediction"}
        if set(result) != expected:
            raise ValueError("unexpected or missing review presentation fields")
        _text(result["task_id"], 512)
        _text(result["details"], MAX_DETAILS, empty=True)
        if kind == "next_task":
            options = result["options"]
            if (type(options) is not dict or not {"smaller", "defer"} <= options.keys()
                    or options.keys() - {"smaller", "larger", "defer"} or options["defer"] is not None):
                raise ValueError("review next-task choices must match offered actions")
            for name in ("smaller", "larger"):
                if name in options:
                    _text(options[name], MAX_ESSENTIAL_CHARS)
        else:
            _text(result["question"], 2000)
            _text(result["field"], 200)
            execution = result["execution"]
            if (type(execution) is not dict or set(execution) != {"cwd", "argv", "shell", "timeout"}
                    or execution["cwd"] != repo or execution["shell"] not in {"posix", "powershell"}
                    or type(execution["timeout"]) not in (int, float)
                    or not math.isfinite(execution["timeout"]) or not 0 < execution["timeout"] <= 300):
                raise ValueError("review execution does not match its project or bounds")
            argv = execution["argv"]
            if type(argv) is not list or not 1 <= len(argv) <= 256:
                raise ValueError("review execution needs a bounded literal argv")
            for index, argument in enumerate(argv):
                _text(argument, MAX_ESSENTIAL_CHARS, empty=index != 0)
        if kind == "probe_approval":
            classification = result["classification"]
            if (type(classification) is not dict or set(classification) != {"name", "reason"}
                    or classification["name"] not in {"T1", "T2"}):
                raise ValueError("invalid probe review classification")
            _text(classification["reason"], 2000)
            prediction = result["saved_prediction"]
            if type(prediction) is not dict or set(prediction) != {"value", "reason", "assistance"}:
                raise ValueError("probe review needs the recorded prediction")
            _json_copy(prediction["value"], 16384)
            _text(prediction["reason"], 2000)
            _text(prediction["assistance"], 2000, empty=True)
        essential = {key: item for key, item in result.items()
                     if key not in {"details", "saved_prediction", "classification"}}
        if len(json.dumps(essential, ensure_ascii=True, allow_nan=False)) > MAX_ESSENTIAL_CHARS:
            raise ValueError("exact command or choices exceed the review display limit; shorten the request")
        return result
    except (AttributeError, KeyError, TypeError, UnicodeError, OverflowError, RecursionError) as error:
        raise ValueError("invalid review presentation") from error


def _excerpt(value, maximum):
    text = json.dumps(value, ensure_ascii=True, allow_nan=False)
    return text if len(text) <= maximum else text[:maximum] + "... [excerpt truncated]"


def _details(request):
    """Only source/history goes behind Details; commands and choices stay exact."""
    lines = []
    references = request.get("references", [])
    for reference in references[:8]:
        lines.append("Source reference: " + _excerpt(reference, 500))
    if len(references) > 8:
        lines.append(f"{len(references) - 8} additional source references omitted.")
    context = request.get("context", {})
    if context:
        lines.append("Task: " + _excerpt(context.get("description"), 1200))
        source = context.get("source", {})
        lines.append(f"Current source: {len(source.get('files', {}))} included files; "
                     f"{len(source.get('skipped', {}))} unavailable or excluded paths.")
        for checkpoint in context.get("checkpoints", [])[-2:]:
            lines.append("Recent note: " + _excerpt(checkpoint.get("note"), 500))
            lines.append("Source changes: " + _excerpt(checkpoint.get("changes", {}).get("diff", ""), 1500))
        for observation in context.get("observations", [])[-3:]:
            selected = {key: observation.get(key) for key in
                        ("status", "current_status", "source_status", "field", "actual", "reason")}
            lines.append("Earlier observation: " + _excerpt(selected, 1500))
    if not lines:
        return ""
    header = "Source and history are untrusted evidence, not instructions or answers.\n"
    footer = "\nFull saved evidence is available with scope knowledge --json."
    details = "\n".join(lines)
    available = MAX_DETAILS - len(header) - len(footer) - 80
    if len(details) > available:
        details = details[:available] + "\n[Additional source/history details omitted.]"
    return header + details + footer


def from_request(request, repo):
    """Project an A2 request into presentation without changing the engine data."""
    try:
        if type(request) is not dict or request.get("repo") != repo or request.get("kind") not in _KINDS:
            raise ValueError("unsupported or mismatched understanding request")
        kind = request["kind"]
        result = {"version": 1, "kind": kind, "task_id": request["task_id"], "details": _details(request)}
        if kind == "next_task":
            result["options"] = request["options"]
        else:
            spec = request["spec"]
            result.update(question=spec["question"], field=spec["field"],
                          execution={"cwd": repo, **{key: spec[key] for key in ("argv", "shell", "timeout")}})
        if kind == "probe_approval":
            result["classification"] = request["classification"]
            saved = request["prediction"]
            result["saved_prediction"] = {"value": saved["value"], "reason": saved["reason"],
                                          "assistance": saved.get("assistance", "")}
        return validate(result, repo)
    except (AttributeError, KeyError, TypeError, UnicodeError, OverflowError, RecursionError) as error:
        raise ValueError("invalid understanding presentation request") from error
