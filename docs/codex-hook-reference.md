# Baseline PermissionRequest contract

This document defines the package baseline. It does not establish compatibility
with an installed host or claim independent verification of a host release.

Input is a JSON PermissionRequest with session_id, turn_id, cwd, model,
permission_mode, tool_name, tool_input, transcript_path and optional agent fields.
Unknown fields and tool text are untrusted. The exact allow envelope is:

```json
{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}
```

Deny uses `behavior: "deny"`, optionally with `message` inside `decision`.
Abstain emits empty stdout and exits 0; deny never uses exit 2. Diagnostics go to
stderr or bounded local logs. Never output updatedInput, updatedPermissions,
interrupt, continue, stopReason, suppressOutput or systemMessage. All hook errors
abstain. Startup additionalContext, if supported, is a separate event contract.

Before implementing live adapters or running a live test, verify the installed
host version, CLI flags, trust behavior and event envelopes against official
documentation/source. Record actual sources and differences then; this file does
not claim official verification or invent citations. See [INTERFACES.md](INTERFACES.md).
