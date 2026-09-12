# Baseline permission wire contract

This document defines the package baseline. It does not establish compatibility
with an installed host or claim independent verification of a host release.


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

Before a live test on a different Codex release, verify the current hook contract
against official documentation/source; document differences instead of guessing.


Before implementing live adapters or running a live hook test, inspect the installed
host version and verify its hook fields, configuration, and behavior against
current official documentation/source. Record URLs, revisions, versions and any
differences at that time. F0 implements no hook or host adapter.
