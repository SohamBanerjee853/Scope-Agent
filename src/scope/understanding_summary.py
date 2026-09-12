"""Pure, historical receipt projection; no filesystem, grants or execution."""

from copy import deepcopy

_EVENTS = {
    "task_start": ("tasks", ("task_id", "session", "baseline")),
    "prediction": ("predictions", ("task_id", "check_id", "prediction")),
    "observation": ("observations", ("task_id", "check_id", "observation")),
    "execution": ("executions", ("task_id", "check_id", "execution")),
    "next_task": ("next_tasks", ("task_id", "handoff")),
    "dispatch": ("dispatches", ("task_id", "handoff_id", "status")),
    "check_skipped": ("skipped_checks", ("task_id", "check_id", "reason")),
    "next_task_deferred": ("deferred_tasks", ("task_id", "reason")),
    "scopes_revoked": ("revocations", ("session", "revoked")),
}


def summarize(events) -> dict:
    result = {value[0]: [] for value in _EVENTS.values()}
    result["meaning"] = (
        "Historical source-specific evidence, not general mastery. Predictions are not "
        "execution consent or grants. An execution record must be inspected for exit, "
        "timeout, truncation and errors; selection and dispatch do not prove completion. "
        "Current source validity requires a fresh knowledge check."
    )
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            continue
        route = _EVENTS.get(event["event"])
        if route is None:
            continue
        field, required = route
        # Soham's published F0 uses {ts, event, fields}; the independently
        # authorized local bootstrap used flat fields. Preserve either envelope.
        body = event.get("fields", event)
        if not isinstance(body, dict) or any(key not in body for key in required):
            continue
        result[field].append(deepcopy(event))
    return result
