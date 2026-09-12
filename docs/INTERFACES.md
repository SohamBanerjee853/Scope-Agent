# Frozen interfaces

These shared behavioral contracts require owner coordination and checkpoint
documentation when changed.

### Commands and routing

Keep cli.py lazy. Soham's command routes are hook, stop, session-end, install,
watch, propose, install-skill, receipt and smoke. Arjun's routes are start,
checkpoint, check, knowledge, next, revoke and skill via learning_cli, plus demo
and demo-adapter. The revoke CLI belongs to Arjun; its IPC operation belongs to
Soham. Both permission and understanding cancellation use that one operation.
Integration owns codex, claude and ready, plus internal launch-agent and host-hook
routes. Reserve these in F0; implement them only in I1L after the shared workflows
work. The old manual Codex install/watch commands remain supported.

All commands must work through uv run scope COMMAND without make. Every shell
script added under scripts gets a PowerShell twin. The integration Makefile chooses
by OS. Optional popup behavior requires SCOPE_POPUP=1; the default is terminal-only.
The launchers explicitly disable popups and use two panes in the same terminal.
Shared Python scripts do not need separate shell wrappers.

### Startup and host identity

Public interface: scope codex|claude [TASK], -C/--cwd PATH, -m/--model NAME,
--permissions-only, --understanding-only, --dry-run and --manual-watch. Both
workflows are the default; mode flags are mutually exclusive. Understanding needs
a Git project. Dry-run performs no writes or host/model launch. Normal startup
requires an interactive terminal, the selected host on PATH and tmux for automatic
panes. Use an installed absolute Scope executable path, not a shell alias.

Each launch owns SCOPE_HOME/runs/<unique-id> with launch.json, host-session.json,
watch endpoint/mailbox and sessions/receipts. Pass that run directory as the
child's SCOPE_HOME and set SCOPE_LAUNCH_ID/SCOPE_HOST. Remove inherited parent
CODEX_THREAD_ID and Claude nesting/session markers before starting the host.
Supply invocation-only hooks/guidance and access to this run's record directory;
preserve host restrictions and unrelated settings. No global install is required.

Expose host_session.session_id() -> str or None: inside a launch, resolve the
native ID registered by SessionStart; missing, closed or mismatched launch/session
state is an error, not a reason to borrow another session. Outside a launch,
retain explicit-session/CODEX_THREAD_ID/task-ID behavior. Rehearsals always get
their own demo IDs. Subagent startup must not replace the main launch identity.

scope ready checks registered native startup and reachable review, and reports
permission mode as disabled, configured/waiting, or requests observed. Startup
registration, transport readiness and real permission interception are distinct.
Failure gives an actionable error; agent guidance says to stop dependent work.
The launcher must not fabricate startup events or human answers to pass this check.

The owned watcher ends when its owner process disappears, using an OS lifetime
lock rather than trusting a reusable PID. Agent exit clears grants and finalizes
only that launch's receipts; mark launcher-inferred exit separately from a native
SessionEnd. Receipt replay accepts --home PATH. Serialize receipt writers.

### Hook input and output

Read one JSON PermissionRequest on stdin. Relevant fields: session_id, turn_id,
cwd, model, permission_mode, tool_name, tool_input, transcript_path and optional
agent_id/agent_type. tool_input carries command and optional description/shell.
Treat unknown fields and tool text as untrusted; do not execute parser input.

An allow is EXACTLY:

~~~json
{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}
~~~

A deny uses behavior "deny" and may include a message inside decision.
Abstain writes no stdout and exits 0. Deny is JSON, never exit 2.
Never emit updatedInput, updatedPermissions, interrupt, continue, stopReason,
suppressOutput or systemMessage. Diagnostics go to stderr or a bounded local log.

The original docs/codex-hook-reference.md is authoritative for the baseline.
Before a live test on a different Codex release, verify the current hook contract
against official documentation/source; document differences instead of guessing.

I1L's host adapter consumes SessionStart, PermissionRequest, Stop and SessionEnd.
SessionStart registers the actual host ID and may return that host's documented
additionalContext output; this is separate from the strict permission envelope.
Stop/SessionEnd remain append-only/no-decision operations. Unsupported or malformed
events never issue permission. Understanding-only launches omit permission hooks.

Codex uses invocation-specific hook configuration and its normal /hooks trust.
The reference Codex 0.154.0 dispatches SessionStart at the first task, not merely
when its terminal opens. First use requires reviewing/trusting Scope hooks and
restarting; definitions and executable paths should remain stable across launches.
Claude Code uses a run-local --settings file and appended system guidance, never
a replacement system prompt. Verify installed versions against official docs
before implementing either adapter. Normalize supported Claude Bash/PowerShell
requests into the shared classifier; do not guess its shell from a Codex transcript.
Do not require Codex-only fields that Claude does not supply.

Detect existing Scope hooks in user/project settings and give a migration error
without editing them. Do not claim an exhaustive audit of managed/plugin hooks.
Both hosts can allow commands before hooks run. Claude sandbox network prompts
can bypass PermissionRequest; leave its native-rule/transcript coverage unknown.
Never claim that every command/edit or every native human answer is observed.

### Classification and cards

Expose classify(command, cwd, shell, description=None) returning a Tier-like
value with name ("T1", "T2", "T3"), reason, matched_rule, domains and segment findings.
It is deterministic lexical analysis, not filesystem access or shell execution.

T1: recognized routine reads/tests. T2: reviewable local writes, unknown commands
and ambiguous shapes. T3: destructive/publishing/secret/escalation/network-write
and evaluation/obfuscation shapes. Every git push is T3, not just force pushes.

A proposal card has exactly summary, commands, domains, budget. Summary: 1-300
characters; commands: 1-20 patterns; domains: at most 20 exact lowercase hostnames;
budget: integer 1-100 (bool is not an integer budget).
Patterns consist of literal alphanumeric/underscore/hyphen words and an optional
trailing wildcard. No shell syntax, paths, arbitrary globs or broad interpreters.
Reject T3 patterns. Command-shape matching must not turn an individually reviewed
custom executable/path into a reusable broad grant.

A grant is identified by session, agent, normalized cwd and shell, has a fresh
grant ID, expires after 900 seconds using a monotonic clock, and consumes a finite
budget atomically. Store grants/proposals in memory only. Reclassify requests before
using grants. A new proposal never replaces a human-approved grant silently.

### Human IPC seam

Expose ipc.exchange(message, timeout=105, *, home=None) -> dict or None.
Failure returns None. Use bounded newline-delimited JSON over TCP 127.0.0.1;
a private port file under SCOPE_HOME holds the port and random authentication
token. Validate the endpoint, token, message size and per-request nonce echoed in
the reply. Cap messages at 128 KiB and the port file at 4096 bytes. Never log tokens.

The following describes logical bodies before transport token/nonce fields:

| kind | Request fields | Reply |
| --- | --- | --- |
| ping | none | ready boolean |
| proposal | session_id, optional agent_id, cwd, card | accepted boolean |
| request | request: original PermissionRequest object; optional internal request_id | behavior: allow, deny or null (abstain); optional message |
| question | repo, context and prompt strings; launched callers also supply native session_id | answer string, or error |
| notice | message string | received boolean |
| revoke | none; clears all watcher grants/proposals and pending answers | revoked boolean |
| stop | optional session_id | received boolean |
| shutdown | none; stops the owned watcher and cancels grants/pending answers | stopped boolean |

If the two implementations need an additional field, agree and test it before
changing the contract. Successful transport is never permission by itself.

I1L adds a launch-only file mailbox fallback using the same authenticated messages
and watcher handler when TCP connection establishment is blocked. Do not fall back
after sending a TCP request: retrying could repeat a question or spend a grant twice.
Validate tokens/nonces, suppress replay, bound payloads and active work, and clean
up timed-out requests/responses. Both transports retain disconnect/cancellation
rules. This is local same-user coordination, not tamper-proof storage or a way to
run probes outside their sandbox. Never enable broad network access to make it work.

Only the watcher reads the human terminal, serialized across prompts. Permission
review returns grant, deny, once or abstain plus an optional edited card.
Questions return a human answer or explicit error; absence is never fabricated.
Use a roughly 95-second human deadline inside the 105-second caller deadline.
Disconnects/timeouts/revocation must invalidate pending answers. Do not let a late
answer approve a later request. A noninteractive watcher refuses human decisions.

The watcher MUST NOT execute commands received through this channel. Arjun's probe
runs in the calling process through the bounded runner after separate consent.

I1L binds launched questions to the registered native session and task project.
The watcher rejects missing/foreign identities inside a launch and rechecks native
session lifetime before returning an answer. Legacy standalone questions retain
their original body. Task reads and mutations also enforce this identity, so an
existing task ID cannot bypass a launched caller's session boundary.

Next-task selection returns the instruction's UTF-8 SHA-256 digest while retaining
delivery_status=not_attempted. A receiving coding host may explicitly call
`scope next TASK --acknowledge HANDOFF --received-sha256 DIGEST` after reading it.
The adapter validates the selected handoff, digest and open native task identity,
then uses A2's unchanged one-attempt dispatch contract. A supplemental
host_handoff_acknowledged event records caller_reported provenance. This is a
same-user receiver report; it proves neither independent caller authorship nor
an automatic upstream host send, execution, permission or task completion.

### Logs, sessions and receipt projection

Use the foundation log.append/read. Correlation IDs belong in internal events,
not new Codex wire fields. Permission request/decision/grant events must correlate
rather than double-count watcher and hook records.

Understanding state should preserve task start/checkpoints, saved prediction and
reason, assistance/provenance, execution consent, observed output/error, comparison,
next-task choice, dispatch, skip/defer and revocation. Baseline event names include
task_start, prediction, probe_approval, execution, observation, next_task, dispatch,
agent_process, check_skipped, next_task_deferred and scopes_revoked. Observation
events contain an observation object; next_task events contain a handoff object.
Arjun freezes detailed event fields in docs/UNDERSTANDING-EVENTS.md during A1;
Soham consumes the pure projection and must not reinterpret these records.

Arjun provides understanding_summary.summarize(events) -> dict, a pure projection.
It must expose tasks, predictions, observations, executions, next_tasks, dispatches,
skipped_checks, deferred_tasks, revocations and meaning. Include evidence and clear
status, not unsupported mastery claims. Soham's receipt calls it at integration.
Tests may inject an empty summary beforehand; production must not silently drop
understanding evidence after integration.

Keep schema_version 1 and the permission actions/counts structure. Add a separate
understanding object. Permission counts include requests, auto_allowed,
allowed_once, denied, hard_asks and scopes_granted. Older schema-1 receipts lacking
understanding remain readable. Rebuilding from events can add it.
Launched receipts may also include a separate launch evidence section containing
native startup, readiness, receiver acknowledgment and native/inferred exit events.
Its caller-report and coverage limits do not alter permission or A2 counts.

Launched task/proposal sessions use the registered host ID through the shared
resolver. Outside a launch, use an explicit session or CODEX_THREAD_ID when
available, otherwise a generated task ID. Log host_connected/client_ready and host
names separately from permission events; connection notices never count as allows.
Rehearsals always use a NEW demo UUID, even inside an existing coding session.
Never append a demo session-end to a real coding session. Label Claude native
handling as Claude; do not run Codex's coverage audit against Claude records.

## Foundation implementation conventions

Paths are resolved without creating directories. Session logs use a `session-`
prefix for safe lowercase IDs and a `sha256-` prefix for other IDs. Consumers must
use `paths.session_log()` rather than constructing filenames.

`log.read(session_id)` returns a list of records shaped as
`{"ts": "UTC ISO-8601", "event": "name", "fields": {}}`. Workflow event attributes
belong inside `fields`; they cannot overwrite the timestamp or event name.
Readers skip malformed records and an unterminated final line. Cross-process
workflow ordering and bounded receipt reads remain later milestone work.

Feature modules expose `main(argv) -> int | None`. `learning_cli` receives the
command name first for all its routes; `launch` does likewise. `install-skill`
routes to `install.main(["install-skill", ...])`; `install` passes trailing args
only. Other routes pass trailing args only. Feature parsers own command help.
F0 reserves routes but invoking an absent feature (including its help) fails with
exit 1 and an explicit stderr diagnostic. F0 is not an installed permission hook.
