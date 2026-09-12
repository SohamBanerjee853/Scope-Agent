# Integration review before A2

September 12, 2026. Arjun's branch remains separate while Soham prepares the main
merge. This review checked the published integration code and actual hosted CI;
it does not claim A2 is implemented.

## Arjun's code survived reconciliation

At `b13cf4cea546346ab56762fe60c71b0a6f6619cf`, the five Arjun production modules
and `docs/UNDERSTANDING-EVENTS.md` exactly match tested A1 commit `c808a3b`.
The integration includes both the A1 and tested S4 histories. Its deliberately
missing check/next adapters and scripted rehearsal are partial A3 work awaiting
Arjun's actual A2 APIs, not completed functionality.

## Hosted CI and the Arjun-owned Windows correction

The [first integration CI run](https://github.com/SohamBanerjee853/Scope-Agent/actions/runs/34705915038)
failed before any jobs ran because `runner.temp` appeared in job-level `env`.
GitHub permits that context in steps, not in job-level environment definitions;
see [context availability](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability).
A temporary alternative patch passed actionlint 1.7.12 and a real Python setup-step
check. It was discarded when Soham independently published `638a217`, using
the supported `github.workspace` context. No duplicate CI change was pushed.

The [next run](https://github.com/SohamBanerjee853/Scope-Agent/actions/runs/34706044344)
passed the macOS job. Windows ran its tests and reported **1,216 passed,
10 failed and 6 errors**. Its build/smoke stages did not run after the test failure.
Those are the results for that specific hosted revision, not current pass claims.

One failure is Arjun's lock-test readiness assertion: Windows emitted
`b"locked\r\n"`, while the test expected `b"locked\n"`. The test now reads text
with universal newline handling and explicitly exercises LF and CRLF messages.
Both cases still verify that a competing owner cannot acquire the lock and that
the lock is released after the child is killed. This changes the test, not the
production lock. Native Windows confirmation requires the corrected revision to
run in Windows CI.

After this correction, the full local suite passed **180 tests, 0 skipped** on
macOS Python 3.14.7 (20.67 seconds) and Python 3.11.16 (20.62 seconds). Both newline
cases exercised real competing processes and real OS locks.

Other failures belong to permission/A3 integration: shell-path test assumptions,
oversized pytest parameter IDs entering Windows environment variables, offline
smoke, and newline assumptions in skill-installation tests. These need owner-side
review; this branch does not edit those implementations or weaken their tests.

## Reproduced cancellation gap to resolve before wiring A2

The direct terminal path in `ui.ask` observes a caller-local cancellation event;
the watcher uses its own revocation generation. A confirmed `scope revoke` changes
the latter but does not invalidate an already pending direct terminal question.
Source references at the reviewed revision:

- [Direct question context and terminal path](https://github.com/SohamBanerjee853/Scope-Agent/blob/b13cf4cea546346ab56762fe60c71b0a6f6619cf/src/scope/ui.py#L21).
- [CLI revocation through the watcher](https://github.com/SohamBanerjee853/Scope-Agent/blob/b13cf4cea546346ab56762fe60c71b0a6f6619cf/src/scope/learning_cli.py#L96).
- [Watcher generation cancellation](https://github.com/SohamBanerjee853/Scope-Agent/blob/b13cf4cea546346ab56762fe60c71b0a6f6619cf/src/scope/watch.py#L27).

An offline reproduction used a real localhost watcher and actual tagged prompt
logic with explicitly labeled simulated input. Revocation was confirmed and the
watcher generation advanced **0 to 1**. The direct question stayed active and
returned the fixture answer afterwards. Only `scopes_revoked` was logged; zero
probes or child processes executed. This demonstrates a question-cancellation
gap, not an execution bypass or a real person's consent.

The [portable diagnostic](diagnostics/reproduce_direct_question_revoke.py) accepts
an explicit reconciled source checkout via `--source`. It creates disposable
homes, simulates tagged input, runs real local revocation and forbids probes.
Exit 1 identifies the reproduced late-answer behavior, 0 identifies rejection
of the late answer in the exercised flow, and 2 means the diagnostic could not
establish a result. It is a manual diagnostic, not part of the ordinary test suite.

Before A2 consumes these callbacks, reconcile direct questions with shared
revocation, or route the interactive workflow through the watcher. Verification
must show a pending answer becomes unavailable after shared revoke, including
an answer arriving late. A prediction, a consent response and the current source
must all remain valid before the caller runs any probe.

## A2/A3 handoff details

`ui.ask` takes a prompt plus repo/context and returns an answer string with its
route provenance, or an error. The A2 adapter must parse and validate the answer;
an error or missing field is never consent. Scripted adapters must label responses
`test_fixture`: injecting a fixture transport into the ordinary UI still labels
the route `human_ipc`, which by itself does not establish a real human answer.

Publish the real A2 request/result/callback schemas for Soham's adapters. The
current CLI intentionally lacks successful check/next result handling as well as
the callables, so replacing only the placeholder calls would be incomplete.
Keep A2 execution caller-side and preserve separate selection, dispatch and
completion evidence. None of this requires an independent model client.
