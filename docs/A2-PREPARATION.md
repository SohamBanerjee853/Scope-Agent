# A2 preparation: recover context, test a hypothesis, choose the next action

September 12, 2026. This is a proposed implementation brief, not implemented or
tested A2 functionality. Arjun directed implementation to begin after Soham merges
both branches into `main` and this checkout pulls that merge into `arjun_branch`.
The actual classifier must include tested S1 commit
`54f39c769df4603e7bae8fb7b63d823657c15662` in its history.

## Outcome and current foundation

Help a developer recover a useful model of the codebase and get out of a debugging
loop. The existing agent supplies explanations and candidate actions; Scope
records the person's working assumption, consent and observed evidence. See
[UNDERSTANDING-PURPOSE.md](UNDERSTANDING-PURPOSE.md).

A1 already supplies source snapshots/citations, task persistence, bounded command
execution and stale-evidence checks. Its automated retry test recovers an
observation in a fresh process and verifies a repair. That test writes the repair
itself and seeds a labeled observation; it does not implement A2's human workflow.

## Example interaction to implement

- Recover context: “The saved automated probe reported two charges. There was no
  human prediction in that record. Its cited source used a different key for each
  attempt; changed source makes this historical evidence.”
- Separate the hypothesis: “Different retry keys may explain the duplicate
  charges. The saved output alone does not establish that cause.”
- Ask: “For this source version, how many charges will one order produce after
  one retry, and why?” Save the person's actual answer, or record a skip.
- Separately ask permission for the displayed probe. Report its real result and
  any limits on comparison; do not assume the person approves or predicts two.
- Let the result guide the next action. If evidence supports the retry-key
  hypothesis, offer the narrow stable-key repair and regression. A contradiction
  calls for investigation; an unverifiable result calls for repairing the probe
  or collecting missing evidence. The person can defer any next task.

The interface must respond to observations rather than always suggesting the
same predetermined fix. A later regression provides evidence that a repair worked.

## Reusable offline assets prepared

[The fixture directory](../tests/fixtures/arjun_debugging/README.md) contains a
small fake service, the buggy checkout, a JSON probe and a regression script.
Copy it into a disposable directory before editing. `checkout.py:8` is the faulty
retry key; changing it to `key = order_id` preserves one key per order.

The probe runs as `python -B probe.py`; the regression runs as
`python -B regression.py`. Future A2 should use the interpreter's absolute path
and an argv list with explicit cwd. The scripts perform no network or payment
operations and keep their ledger in memory.

The fixture distinguishes three behaviors: the original bug records two charges,
the stable order key records one and preserves distinct orders, and an incorrect
constant key looks fixed in the single-order probe but fails the distinct-order
regression. This prevents a superficially successful probe from establishing a
complete repair. Tests edit disposable copies and verify source assets and
unrelated configuration files remain unchanged.

Verification: all **179 local tests passed, 0 skipped**, on both macOS Python
3.14.7 (20.14 seconds) and Python 3.11.16 (19.51 seconds), including the three new
fixture cases. Both environments use noneditable installs. `uv build` passed;
all five fixture assets matched their source-distribution bytes, while production
modules matched the installed package and wheel. These fixtures are test assets,
not installed demo commands.

## Proposed callable boundaries

Implement the orchestration in Arjun's `experience.py`. Extend owned task-state
helpers in `learning.py` as needed. Keep the existing event names and fields in
[UNDERSTANDING-EVENTS.md](UNDERSTANDING-EVENTS.md).

| Callable | Responsibility |
| --- | --- |
| `check(path, task_id, spec, *, ask, show, provenance)` | Classify, capture a prediction, request consent, run the probe and save the comparison. |
| `compare(prediction, execution, current_source)` | Pure validation and typed comparison; no questions, storage or execution. |
| `choose_next(path, task_id, *, smaller, larger=None, ask, show, provenance)` | Record a selected debugging instruction or an explicit deferral. |
| `dispatch(path, task_id, handoff_id, *, deliver)` | Attempt delivery to the current agent and record the actual outcome separately from selection. |

These signatures are proposals for owned code, not changes to the shared IPC
contract. `spec` contains a question, citation(s), literal JSON field name, argv,
shell and bounded timeout. The predicted value comes only from the answer
callback. A trusted UI adapter supplies provenance; source, tool output and spec
text cannot provide an answer or choose its authorship label.

Use a literal top-level JSON key initially: `charges` selects that key; a dot in
a field name is literal, not an expression or guessed traversal. Missing input
is distinct from valid JSON `null`, `false` and `0`. Callback payloads and accepted
answers are copied so mutation cannot alter the classified command or consent.

## Required order and recovery behavior

1. Resolve the saved task, validate the spec/citations, and classify the exact argv
   using `scope.tiers.classify`. Reject T3 before asking a person or executing.
2. Recover current source evidence and earlier observations for context. Clearly
   distinguish a recorded observation from an untested explanation of its cause.
3. Ask for the predicted value and reason. Save both task state and the prediction
   event before requesting execution consent. Missing/empty answers become skips.
4. Ask separately whether to execute this exact probe. Save the decision before
   execution; decline, callback failure or persistence failure prevents execution.
5. Recheck source after consent. A changed or unavailable reference invalidates
   this probe plan. Run the unchanged argv through the caller-side A1 runner.
6. Save the actual execution, recheck source, and compare only complete, valid
   JSON against the saved prediction. Record uncertainty explicitly.
7. Present a concrete smaller debugging action, an optional larger action, or
   deferral. Record selection, delivery and eventual completion as separate facts.

Never hold the project-state lock while waiting for a person, executing a probe,
or delivering an instruction. Persist a fresh check ID and phase transitions so
a process restart can display an unfinished attempt. Do not automatically rerun
an interrupted attempt or reuse its consent. Recovery should expose the task
description, saved hypothesis, actual observations and source validity together.

## Planned acceptance cases

These are tests to implement after the merge, not passing-test claims.

| Case | Required evidence |
| --- | --- |
| Prediction/consent ordering | The consent callback can read the already saved prediction and event; the runner can read approval. |
| Skip, decline, missing reviewer | Explicit skip/refusal records; no subprocess and no invented answer. |
| Persistence or callback failure | Execution does not start when prerequisite recording or consent fails. |
| Interrupted process | Context survives; incomplete attempts stay incomplete and never rerun automatically. |
| Changed source during consent or execution | No stale-plan execution before the run; `not_verified` if source changes during the run. |
| Strict JSON comparison | Reject duplicate keys, nonfinite values, trailing content and missing fields; distinguish nested booleans from numbers. |
| Runner failures | Nonzero exit, timeout, either truncated stream, invalid UTF-8 and forced cleanup all prevent a verified comparison. |
| Command classification | Test real POSIX/PowerShell quoting, paths with spaces and apostrophes, invalid shells, and T3 rejection. |
| Untrusted text/mutating callback | Source instructions and callback mutations cannot replace argv, consent, references or recorded prediction. |
| Next debugging action | Smaller/larger choices and defer are distinct; failed delivery never claims dispatch or completion. |
| Receipt replay | Recorded predictions/executions/observations project separately from permission counts. |
| Debugging recovery | Observe two fake charges, reopen context in a fresh process, choose the narrow repair, then observe one charge and a passing regression. |

Use disposable repositories and isolated SCOPE_HOME, CODEX_HOME and
CLAUDE_CONFIG_DIR. Label automated answers `test_fixture`. Use script-file probes:
the real classifier treats interpreter `-c` evaluation as T3. These tests do not
establish human understanding, saved debugging time or native Windows behavior.

## Integration checks before implementation

Correction to the initial draft: Soham's S3 and S4 receipt readers have a 32 MiB
total-log limit, not a 128 KiB per-record limit. The earlier note confused receipt
reading with other bounded inputs. Direct inspection found no receipt-size
conflict at the runner's two 64 KiB stream limits. Actual subprocess/replay checks
on S4 preserved both streams exactly: ASCII produced a 131,833-byte event and
133,587-byte receipt; NUL output with JSON escaping produced a 787,197-byte event
and 788,951-byte receipt. Both had complete input and no malformed records.
No size-contract change is needed on the basis of the original concern.

For classification, use a display string that preserves the original argv's
literal words for the selected dialect, then execute the original argv without a
shell. An isolated exact-S4 check passed 16 inert examples: eight each for POSIX
`shlex.join` and PowerShell single-quote escaping. Script arguments with spaces,
apostrophes, literal semicolons and dollar signs stayed T2; interpreter `-c` and
Git push stayed T3. This display is policy input, not a command to execute in
PowerShell. The check executed no classified commands and establishes no native
Windows or PowerShell execution claim.

An isolated archive of exact S4
`20d6ce03ecfbf792b9515c0088e9ae5326de3928`, overlaid with Arjun's five existing
modules and 137 existing tests, passed **1,106 tests, 0 skipped**, in 33.70 seconds
on macOS arm64 / CPython 3.11.16. All 55 original S4 files remained byte-identical;
the installed Arjun modules matched this checkout. The newly prepared fixture
tests were excluded while being written. This is compatibility evidence from a
disposable archive, not a merge into this branch or verification of A2.

After the merge, verify both histories and the tested S1 ancestor, read the merged
working agreement, reinstall the package and establish the combined baseline.
Then implement A2, run the full suite, build, checkpoint, commit and push
`arjun_branch`. CLI/skill/demo packaging belongs to A3; launchers remain later
integration work.
