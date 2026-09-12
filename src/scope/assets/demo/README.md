# Disposable payment retry fixture

This project contains a deliberate retry bug and an in-memory fake payment
service. It uses no real payment account, authentication, network, or persistent
ledger. Every probe starts with a fresh service. It is a debugging rehearsal,
not production payment code.

The first request commits a charge, then loses its acknowledgement. The caller
retries the same order. Inspect `retry_key` and `checkout` in `payment.py` and
form a hypothesis about the key used on each attempt. The probe reports actual
local calls as JSON, including `charge_count`, `charged_cents`, `attempt_keys`,
and `lost_acknowledgements`.

Use this project only after `scope demo --prepare-only` has populated a new or
empty destination. Preparation must leave existing directories with content
untouched. It initializes a separate Git repository and copies the understanding
skill into `.agents/skills/scope-understand/SKILL.md`. It does not launch a coding
host or claim that anyone has answered a question.

For a human debugging session, start Scope's task record before editing. Have the
current agent explain the relevant code and cite the current source lines. Give
your own concrete prediction and reason, then separately decide whether to run
the proposed probe. If the A2 check/next workflow is unavailable, stop that part
of the rehearsal and report the missing dependency; never fabricate its events.

These local commands can be used from either POSIX or PowerShell when the Python
environment has pytest installed:

```text
python payment.py
python -m pytest -q
```

For a fresh Scope source checkout, keep the original checkout path: this generated
project has no virtual environment of its own. Use the original environment's
absolute Python and Scope executables, or stay in the Scope checkout and run
`uv run scope demo-adapter probe -C ../scope-payment-demo` (adjust the demo path).
From this project, POSIX uses `/path/to/Scope-Agent/.venv/bin/python` and
`/path/to/Scope-Agent/.venv/bin/scope`. PowerShell uses
`& 'C:\path\to\Scope-Agent\.venv\Scripts\python.exe'` and
`& 'C:\path\to\Scope-Agent\.venv\Scripts\scope.exe'`. Append the command's
arguments; no global installation or execution-policy change is needed.

Run them only under the task's actual execution authorization. The probe executes
the fake service code in the calling process's environment. A watcher transports
human answers and permission decisions; it does not run the probe.

The retry regression intentionally fails in the prepared version. Repair the key
policy so retries use the order's stable identity, then rerun the probe and the
tests. Preserve the test assertions: the initial failure is evidence of the bug.
Recheck cited source after editing; an observation of the old version is stale.

Any scripted rehearsal must label fixture answers explicitly, use a fresh demo
session rather than an inherited real session, and preserve captured output as
evidence. Scripted success does not establish human understanding or time saved.
A synthetic `git push` permission request may demonstrate a hard ask; it must
never execute a push. Broad Python or interpreter permission cards are unsuitable;
the packaged `scope demo-adapter` family supplies a narrow demonstration seam.
