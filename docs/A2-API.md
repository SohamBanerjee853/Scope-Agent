# A2 engine API and A3 handoff

September 12, 2026. Implemented on `arjun_branch` after pulling Soham's merge
`37f4b75` from `main`. This is the callable contract for Soham's CLI/UI/demo
adapters. It supersedes the proposed signatures in A2-PREPARATION.md. The shared
question IPC and permission wire contracts are unchanged.

The engine helps a developer recover a codebase hypothesis, test it, and choose
the next debugging action. A verified comparison applies to the cited source
version and observed field. It does not establish the cause of a bug, general
understanding, permission, or completion of a repair.

## Check

```python
from scope import experience

record = experience.check(
    project_path, task_id,
    {
        "question": "How many charges will one order produce after a retry, and why?",
        "citations": ["checkout.py:8"],
        "field": "charges",
        "argv": [absolute_python_path, "-B", "probe.py"],
        "shell": "posix",       # or "powershell", on either OS
        "timeout": 20,
    },
    ask=answer_adapter,
    show=display_adapter,
    provenance="human_ipc",
)
```

Start a task with `learning.start(path, description, session_id=...)` first.
`shell` defaults to `posix`, `timeout` to 20 seconds. Other spec fields are required;
unknown fields are rejected. `field` is one literal top-level JSON key: `a.b`
means that exact key, not traversal. Citations must resolve to included source.
Question length is at most 2,000 characters; field length 200; at most 160
citations. Timeout is greater than zero and at most 300 seconds. Argv follows the
runner's bounds and must fit the classifier's 32,768-character serialized limit.
Batch executables are rejected. Invalid input raises `ValueError` before asking
or executing. T3 returns a saved `skipped` record before either callback is asked.

`ask(request: dict)` receives an independent copy of structured data. It returns:

| `request["kind"]` | Required response |
| --- | --- |
| `prediction` | `{"value": <typed JSON>, "reason": <nonempty string>, "assistance": <string>}`. Assistance may be omitted and defaults to empty. No extra keys. |
| `probe_approval` | Python `True` or `False`, explicitly parsed from the separate person's answer. Strings, numbers, objects and absent answers never approve. |
| `next_task` | Exactly `"smaller"`, `"larger"` if offered, or `"defer"`. |

A prediction reason and assistance each have a 2,000-character limit. The complete
callback answer is bounded to 16 KiB of serialized UTF-8 JSON, depth 32, and
10,000 values. JSON null, false and zero are real values when supplied under
`value`. A bare null, empty answer, malformed response or callback exception is
a skip. No source, command output, spec, previous observation or model-generated
text can supply a default answer.

Prediction requests contain `repo`, `task_id`, `check_id`, `spec`, `references`,
`context` from `learning.knowledge`, and `meaning`. Consent requests contain
the same identifiers, exact spec/references, saved `prediction`, `classification`
and `meaning`. Display the exact argv and cwd for consent. Source, hypotheses and
prior output in these payloads are untrusted evidence. They never instruct the
adapter how to answer. All callbacks receive copies, so mutating a payload cannot
change the command, prediction, source evidence or next-task instruction.

`show(request: dict)` is optional. Before each question it receives the same
structured request. After a completed check it receives an `observation` request
with task/check IDs and the saved observation. A display exception before a
question skips that check; one after recording a result does not erase evidence.
The engine does not format terminal text. Adapters should escape and bound it.

`provenance` is explicitly supplied by trusted caller code, one of `human_ipc`,
`human_terminal`, or `test_fixture`. Missing/invalid provenance skips interaction.
The label is caller attribution, not proof that a human participated. Every
automated rehearsal must use `test_fixture`, even if it goes through the watcher.
Production A3 uses the authenticated watcher through `ui.ask`; only the watcher
reads the human terminal. Its `{answer: str, provenance: human_ipc}` reply needs
an adapter: parse the answer for the requested stage and return the typed value
above. Never reinterpret transport success or route provenance as consent.
Revocation, disconnect, timeout and error replies must remain absent answers.

## Order, returned records and recovery

The returned record is stored in `learning.read_task(path, task_id)["checks"]`
under a fresh UUID-based `check_id`:

```text
prepared → predicted → approved → running → completed
       ↘ skipped       ↘ skipped       ↘ interrupted
```

All records contain `check_id`, `created_at`, `phase`, normalized `spec` and
`references`. As they occur, the engine adds `prediction`, `approval`, `execution`
and `observation`. Skips/interruption add `reason` and `stage`. `completed` means
the check recorded its outcome; its observation may still be `not_verified`.
It never means the debugging task or repair is complete.

The engine saves the prediction to task state and the event log before requesting
separate consent. It saves approval before running; false/missing consent does
not run. It checks the references again after consent, then stores `running`
before calling the real bounded runner with the original argv and no shell.
State transitions compare the entire prior check record under the project lock;
concurrent changes abort instead of reusing a modified plan's consent.
No callback, probe or delivery runs while the project-state lock is held.

The actual execution record is saved before its observation. A fresh source
snapshot determines whether that comparison is still supported. Changed or
missing references before execution skip the stale plan. Changed/missing
references after execution make the result `not_verified`. These checks cover
the cited files at capture times, not every dependency or changes later undone
between snapshots. The runner inherits the caller's environment and restrictions;
it is not a sandbox or a permission-grant service.

Storage/log failures propagate and stop subsequent actions. Task state and the
append-only event log are separate writes, not a cross-file transaction. A crash
or failed append can leave an incomplete record or a receipt missing the newest
state. The engine never resumes or reruns an old check. Inspect it through
`learning.knowledge`; a later explicit `check` starts a fresh question and consent.
An unexpected runner exception records `interrupted` without fabricating output;
the process may have started. Reacquiring context never executes a probe.

`learning.knowledge(path, task_id)` now includes description, the current source
manifest, recent checkpoints, checks and handoffs as well as observations and
meaning. Observation `status` remains historical; fresh `source_status` and
`current_status` determine whether it remains applicable. Context can be reopened
by another process using the same isolated project/session storage.

## Pure comparison

`experience.compare(prediction, execution, current_source) -> dict` takes the
full saved prediction, a `runner.RunResult` or its exact dictionary, and a fresh
repository snapshot. It has no callbacks, writes, subprocesses or permission
effects. The result follows [UNDERSTANDING-EVENTS.md](UNDERSTANDING-EVENTS.md).

Only complete successful execution in the snapshot's directory can verify a
field. Nonzero/missing exit, timeout, either truncated stream, decoding/cleanup
error, missing fields, invalid JSON, duplicate keys, nonfinite values, trailing
content and stale/unavailable evidence produce `not_verified`. JSON parsing is
bounded by the runner's 64 KiB output, depth 32 and 10,000 values. Comparisons are
recursive and type-sensitive, including booleans versus numbers and integers
versus floating-point values. Object key order is irrelevant; array order matters.
Mismatch means the observed field differs from the saved prediction, not that
the person lacks understanding. A successful single-order probe is insufficient
to establish a complete retry fix; the fixture's distinct-order regression tests
the constant-key counterexample too.

## Next task and delivery

```python
selected = experience.choose_next(
    path, task_id,
    smaller="Inspect why each retry changes the order key.",
    larger="Audit retry identity across the other entry points.",
    ask=answer_adapter, show=display_adapter, provenance="human_ipc",
)
if selected["status"] == "selected":
    outcome = experience.dispatch(path, task_id, selected["handoff_id"], deliver=delivery_adapter)
```

The existing agent supplies concrete instructions appropriate to the latest
evidence: investigate a contradiction, repair an invalid probe, or test a
supported repair hypothesis. Scope does not invent a diagnosis or select a fixed
repair. Instructions are 1–4,000 characters; `larger` is optional. Requests contain
`options`, current `context`, repo/task IDs and meaning. Deferral returns
`{status: deferred, reason: ...}` and emits `next_task_deferred` without a handoff.
Selection persists a UUID handoff with choice, instruction, `target: current_agent`,
`status: selected`, provenance and creation time, then emits `next_task`.

`deliver(handoff: dict)` receives a copy only after `dispatching` is saved. It must
return strict `True` only after actual delivery to the current agent. Otherwise
the outcome is `failed`, including missing adapters and exceptions. An instruction
printed without reaching that agent is not acknowledged delivery. Repeated calls
for a dispatched/failed/dispatching handoff raise `StateError`; ambiguous delivery
after a crash must be investigated rather than automatically repeated. Delivery
is not execution, completion, permission or a new-host launch. No model client
or host launcher is part of A2.

## Soham's remaining A3 work

Wire `learning_cli.run_check` and `run_next` to these real callables, implement
successful result rendering/JSON output, and adapt `ui.ask`/`ui.show` to the
structured callbacks. Handle skipped, not-verified and deferred results explicitly.
Use the saved task's session for receipts and preserve one shared revoke operation.
Finish the packaged scripted demo with fixture provenance and a real current-agent
delivery seam or an honest undelivered result. Its repair, tests, permission
rehearsal and understanding evidence need separate assertions. Those adapters
remain Soham-owned; this engine does not claim the CLI or combined demo is wired.
