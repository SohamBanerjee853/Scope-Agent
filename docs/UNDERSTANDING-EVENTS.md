# Understanding event contract, version 1

Owned by Arjun; frozen for Soham’s receipt integration review at A1. This adds
detail to [INTERFACES.md](INTERFACES.md) without changing its wire or IPC fields.
Only `task_start` and `task_checkpoint` are emitted by A1. The other events below
are reserved contracts for A2/A3, not claims of completed behavior.

The workflow's purpose is to help a developer recover codebase context and get
out of debugging loops. See [UNDERSTANDING-PURPOSE.md](UNDERSTANDING-PURPOSE.md)
for the intended hypothesis, probe and repair flow.

## Common fields and session identity

Use `scope.log.append(session_id, event, **fields)` and `scope.log.read(session_id)`.
The foundation adds `ts` as a UTC ISO-8601 timestamp and `event` as a string.
Soham’s published F0 wraps keyword fields under `fields`; the independently
authorized local bootstrap used flat fields. The projection accepts and preserves
both envelopes. The bodies below refer to the keyword fields in either form.
Understanding callers include `session` with the resolved session ID; the field
is deliberately named `session` because `session_id` is already an argument of
the foundation logger. Task state itself uses `session_id`.

Use `task_id` for task correlation, `check_id` for a single understanding check,
`checkpoint_id` for source checkpoints, and `handoff_id` for a next-task choice.
IDs are internal records and are never added to the host hook wire envelope.
Receipt callers supply records for one session and preserve their recorded order.
Task IDs and checkpoint IDs are UUID-based. No email addresses, authentication
tokens or provider credentials belong in events.

`learning.resolve_session(explicit, task_id=...)` is the single identity seam.
Outside launches: explicit ID, then CODEX_THREAD_ID, then the new task UUID.
Empty or invalid explicit IDs fail. An A1 process with SCOPE_LAUNCH_ID fails until
I1L supplies native registration; it never borrows a parent host ID.

## Event bodies

| Event | Fields in addition to `ts`, `event`, `session` | Meaning |
| --- | --- | --- |
| `task_start` | `task_id`, `project_id`, `repo`, `description`, `baseline` | A real bounded working-tree snapshot was stored for this task. |
| `task_checkpoint` | `task_id`, `checkpoint` | An atomic task update stored the captured changes. `checkpoint` contains `checkpoint_id`, `task_id`, `timestamp`, `note`, `changes`, `source`. |
| `prediction` | `task_id`, `check_id`, `prediction` | Save before requesting separate execution consent. No human answer is invented. |
| `probe_approval` | `task_id`, `check_id`, `approved`, `provenance`, `reason` | A one-check decision. `approved` is boolean; it is never a reusable grant. |
| `execution` | `task_id`, `check_id`, `execution` | Actual caller-side runner result. Not emitted for declined/unstarted commands. |
| `observation` | `task_id`, `check_id`, `observation` | Recorded JSON-field comparison or explicit inability to verify it. |
| `next_task` | `task_id`, `handoff` | A person selected a next task. No completion or permission is implied. |
| `dispatch` | `task_id`, `handoff_id`, `status`, `target`, `instruction` | `status` is `dispatched` or `failed`; `target` is `current_agent` by default. Delivery is not completion. |
| `agent_process` | `task_id`, `handoff_id`, `status`, `provenance` | Optional separately confirmed existing-host process launch; not model work performed by Scope. |
| `check_skipped` | `task_id`, `check_id`, `reason`, `stage` | Empty answer, defer, missing reviewer, rejected command or another explicit skip. |
| `next_task_deferred` | `task_id`, `reason` | The person deferred or no valid choice was available. |
| `scopes_revoked` | `revoked`, `reason` | Outcome of the shared revoke operation. False/unknown does not claim successful clearing. |

### Source evidence

A baseline or checkpoint `source` manifest contains `timestamp`, `files`,
`skipped`, `listing_complete` and `skipped_overflow`. Each included path maps to
`sha256`, `bytes` and `line_count`. Full source is stored only in the task’s latest
snapshot; manifests/events do not repeat entire source bodies. The task retains
its initial hash manifest and most recent five checkpoints.

A reference is `{path, start_line, end_line, sha256, citation}`. Citations use
`file:line` or `file:start-end` and must resolve to fully included source. A missing
or excluded file makes evidence `unverified`; a changed hash makes it `stale`.
The citation text must agree with its path, line range and source hash. Lines use
physical CRLF, CR or LF boundaries; Unicode paragraph separators inside source
do not add citation lines.
Neither means deletion, forgotten knowledge, or a wrong human answer. `added`
in a checkpoint means newly included source relative to the previous snapshot.

### Prediction, execution and observation

`prediction` contains `value` (typed JSON), `reason`, `assistance` (description or
empty string), `provenance` (`human_terminal`, `human_ipc` or explicitly labeled
`test_fixture`), `references`, `question`, `field`, and `argv`.
Empty/skipped input produces `check_skipped`, not an invented prediction.

`execution` is exactly the serializable `runner.RunResult` fields: `argv`, `cwd`,
`timestamp`, `exit_code`, `stdout`, `stderr`, `timed_out`, `stdout_truncated`,
`stderr_truncated`, `error`, `cleanup_incomplete`. Missing launch returns a null
exit code and an explicit error. `timestamp` is the actual start attempt time.

`observation` contains `status` (`matched`, `mismatched`, `not_verified`), `field`,
`actual` when available, `expected`, `reason`, `references`, `source_status`, and
`execution_timestamp`. It is computed only from captured execution, saved typed
prediction and current source evidence. JSON true and 1 remain different types.
Timeouts, nonzero exits, either truncated stream, invalid/missing JSON fields,
invalid UTF-8, forced output-pipe cleanup, cleanup errors and changed/missing
source prevent a verified comparison. A zero exit code alone is insufficient.

`learning.version_observation` keeps the historical `status` but adds a fresh
`source_status` and `current_status`. An old comparison cannot be reused as
current evidence when its source is stale or unavailable. A1 supplies versioning;
A2 must implement prediction collection, consent, execution and comparison.

### Next-task handoff

`handoff` contains `handoff_id`, `choice` (`smaller` or `larger`), `instruction`,
`target` (normally `current_agent`), `status` (`selected`) and `provenance`.
Deferral has its own event. Optional new-host launch must have distinct explicit
consent and fake launchers in tests; selecting the larger task is not that consent.

## Pure receipt projection

`understanding_summary.summarize(events) -> dict` has exactly these top-level
fields:

```json
{
  "tasks": [],
  "predictions": [],
  "observations": [],
  "executions": [],
  "next_tasks": [],
  "dispatches": [],
  "skipped_checks": [],
  "deferred_tasks": [],
  "revocations": [],
  "meaning": "Historical debugging evidence tied to source versions, not general mastery..."
}
```

Each list preserves full copies of the corresponding event envelopes. Unknown
events, nonobjects and records missing required projection keys are ignored.
`task_checkpoint`, `probe_approval` and `agent_process` remain in the source event
log but do not have additional top-level lists; review the log for their detail.
No count, permission decision or completion is inferred. Historical observation
statuses are preserved, not revalidated by the pure receipt projector.

The projection does no I/O, launches no processes, imports no permission engine,
and never mutates its inputs. Soham’s receipt will place it under the separate
schema-1 `understanding` object at I1. Keep existing permission counts independent.

## Persistence limits

Task state uses an 8 MiB bounded JSON file and a persistent OS-lock sidecar under
SCOPE_HOME/projects/<repository-path-hash>. State replacement and the session
event append are separate operations: a crash after saving state can leave a
missing event. A caller receives an error if event append fails; consumers must
not invent the absent receipt record. Cross-file transactional recovery and
receipt-writer serialization remain integration work.

These are local same-user records, not tamper-proof proof of human authorship.
Test fixtures may seed synthetic observations only when explicitly labeled.
