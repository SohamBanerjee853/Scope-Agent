# Soham checkpoints

The current milestone is S2, recorded below. The S1 section is historical evidence
for handoff commit `54f39c769df4603e7bae8fb7b63d823657c15662`.

## S1 (historical)

Completed September 12, 2026 on `work/soham-permissions` in the new public
`SohamBanerjee853/Scope-Agent` repository. Foundation parent:
`3d6144cedf9d4c9107c9012d688a19158fc3c225`. This checkpoint covers S1 only.
The commit containing this document is the S1 handoff; resolve its full SHA with
`git rev-parse HEAD` when checking out that commit. The handoff response records
the exact pushed SHA. No feature branch has been merged into main.

## Implemented interfaces

- `tiers.classify(command, cwd, shell, description=None) -> Tier`. The frozen
  result has `name`, `reason`, `matched_rule`, `domains` (tuple) and `segments`
  (tuple of frozen `SegmentFinding(command, name, reason, matched_rule)`).
  Description text cannot change policy. Maximum command length: 32,768 characters.
- Dialects: POSIX (`posix`, `bash`, `sh`, `zsh`) and PowerShell (`powershell`,
  `pwsh`), case-insensitive shell labels with optional `.exe`. The parser runs
  either dialect on every OS. Unsupported shells cannot produce an allow.
- `wire.parse_request(bytes_or_text)` and `read_request(stream)` validate a
  bounded, strict UTF-8 JSON PermissionRequest. `decision_json(behavior, message)`
  emits the baseline envelope, accepting only allow, deny, or None (abstain).
- `scope hook` permits a valid, consistent T1 result. T2 and T3 abstain in S1.
  `scope hook --abstain` is passive. The explicit smoke-only `--always-allow`
  additionally requires `SCOPE_SMOKE=1`, validates the request/classifier result,
  and still abstains for T3. The environment gate is an S1 implementation choice
  for S3's future smoke harness to preserve; no smoke mode is installed globally.

Wire input requires nonempty session_id, cwd and tool_input.command, and tool_name
Bash or PowerShell. Missing shell uses the named tool's dialect. Conflicting or
unsupported explicit shells abstain. An omitted hook_event_name is accepted for
this dedicated baseline entry point; an explicit non-PermissionRequest event is
rejected. Optional Codex metadata is not required. Unknown fields remain data.
Duplicate keys, invalid UTF-8, nonfinite numbers, malformed fields and requests
over 128 KiB are rejected. No transcript is read or executed.

An allow is exactly these bytes, with no extra newline:

```json
{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}
```

Deny uses the same envelope with behavior deny and an optional decision.message;
it is never exit 2. Abstain is empty stdout and exit 0. Hook input, classifier,
import and diagnostic failures cannot allow. Diagnostics omit exception/request
contents and go to stderr. No Rich, PyYAML or model SDK is imported on the
automatic hook path. These are offline baseline checks, not verified native host
adapter contracts.

## Classification and boundaries

The strictest compound finding wins. The lexer handles literal quoting,
separators/pipelines, escape forms, wrappers, cwd changes and redirections.
Known dangerous basenames are still checked when an executable has a path.
Literal command words can be reconstructed from adjacent quotes/escapes;
expansion, interpreter evaluation, encoded-host invocations and unsupported
PowerShell expression syntax require a hard ask.

T3 covers tested ordinary/force/dry-run Git pushes, destructive/privileged shapes,
known secret paths, evaluated text, network writes and output paths outside the
workspace. Network config/unknown switches, PowerShell parameter abbreviations,
hidden-file search switches, shell controls, smart-quote/array forms and unknown
wrapper flags are deliberately conservative hard asks. Git command output paths
use Git's lexical cwd; shell output redirections use the shell cwd.

Deliberate T2 examples: unknown tools, custom executable paths, local writes,
local redirections, unfamiliar routine-read/test flags, outside-workspace reads,
directory changes, benign wrappers/environment prefixes, malformed quotes, plain
network GET shapes, Python script files, and local Git identity settings. These
are individually reviewable later; S1 grants no reusable cards. A temporary
directory has no write exemption. Absolute Python/script probes are not broad
interpreter grants.

Only narrow routine command shapes are T1, including `pytest -q`,
`uv run pytest -q`, `python -m pytest -q`, selected Git reads and local file reads.
Classification does not inspect executables, arbitrary user aliases, shell
profiles, symlinks, filesystem state, Git configuration, plugins or test bodies.
It is not a sandbox and cannot establish every possible command's actual effects.
Recognized tests can execute project code. Unknown custom commands remain T2;
their name is not proof of harmless behavior. S2 must reclassify before grant use
and reject broad grants for custom/dynamic executable shapes.

The two packaged YAML files use JSON syntax (a YAML subset) and are loaded with
the standard library. This avoids a PyYAML import on the hook path. They supply
routine patterns and hard-ask command/secret patterns; syntax/path safeguards
live in tiers.py. Resource lookup inspects only these bundled policy files.

## Actual validation

macOS 14.8.4, CPython 3.11.16, uv 0.12.13:

- `.tools/bin/uv run pytest -q`: **429 passed, 0 skipped**.
- Classifier table/review files: **120 POSIX, 129 PowerShell**, plus **4 unsupported
  shell** cases. Both dialects ran on this Mac; none were skipped by host OS.
- Wire/hook tests: **103 passed**. Independent fixture policy verifies exact wire
  output; separate subprocess checks exercise real T1/T2/T3 classification.
- Policy resource/bounds regressions: **14 passed**; foundation tests: **59 passed**.
- `.tools/bin/uv build`: sdist and wheel succeeded, wheel built from sdist.
- Installed the wheel noneditably in `.tools/wheel-check`, verified imports from
  site-packages, then ran all tests: **429 passed, 0 skipped**.
- Installed `rules/allow.yaml`: **999 bytes**. Installed `rules/hard-ask.yaml`:
  **1,333 bytes**. Both parsed as JSON and YAML and were read from the wheel install.

An independent review contributed 79 regression cases within the counts above.
All adversarial commands remained inert test strings. No actual push/destruction,
fake human approval in a live session, authenticated host run or global hook
installation was used as a test. This milestone's repository push is the intended
normal Git handoff, separate from synthetic command-classification fixtures.

The foundation missing-feature CLI test now injects an absent module explicitly,
so it continues testing the same error behavior after hook.py exists. Its assertion
was retained. No production foundation/root files or frozen contracts changed.

## Unverified and next dependency

Native Windows execution and real POSIX/PowerShell shells were not used to execute
the table strings. Live native hook fields, host trust/configuration, terminal
interaction, timing and coverage remain unverified. The current baseline hook
reference must be checked against installed host versions before any live run.
No approvals, grants, watcher, receipts, understanding workflow or launchers are
implemented by this milestone.

Soham's next milestone is S2 on this branch after checkpoint review. Arjun can
merge this exact tested S1 commit (preserving history) into his separate branch
before A2; A1 remains independent. No IPC fields or frozen callable contract
changes are proposed in S1.

## S2: bounded grants and the human watcher

Implemented September 12, 2026 on `work/soham-permissions`, on top of S1
`54f39c769df4603e7bae8fb7b63d823657c15662`. The commit containing this addition is
the tested S2 handoff; the handoff response records its exact pushed SHA.

### Behavior and public commands

`uv run scope watch` starts one authenticated IPv4 loopback listener under
SCOPE_HOME and owns terminal review. Another watcher using that home fails
without replacing the active endpoint. Ctrl-C or authenticated shutdown clears
grants and pending answers, removes the owned endpoint and releases its lifetime
lock. A noninteractive watcher refuses human decisions and questions.

`uv run scope propose --summary "Create local notes" --command "touch *" --budget 2 --session SESSION`
submits a proposal. Repeat `--command` and `--domain` as needed; `--agent` is
optional, and omitted `--session` uses CODEX_THREAD_ID. Submission is not approval.
These command arguments work through the same Python CLI on either shell; no
shell wrapper files were added. This checkout's uv executable is `.tools/bin/uv`.

The terminal displays the complete request, session/agent/cwd/shell, description
and proposed card as escaped data. A human chooses grant, edited grant, once,
deny, abstain or revoke. Editing requires the complete card JSON. Every input
requires the displayed sequence-and-random reply tag; edited-card input gets its
own tag. Queued/partial data from cancelled prompts cannot answer a later prompt.
There is one reader, with bounded polling and no abandoned input threads.
Only terminal review is implemented; no popup is launched.

T1 remains a local hook decision. T2 goes to the watcher. T3 returns empty stdout
for native handling before any grant lookup. Missing listeners, invalid replies,
timeouts, transport failures and invalid classifier results do not allow.
The strict S1 wire envelope remains unchanged.

### Card and authority bounds

`grants.validate_card(raw) -> Card` enforces exactly summary/commands/domains/budget,
1–300 summary characters, 1–20 patterns, up to 20 exact lowercase hostnames and an
integer budget of 1–100 (excluding bool). Patterns use literal words and an optional
final standalone `*`; each pattern is additionally limited to 300 characters.
Interpreters, broad command roots and unknown/custom executables cannot become
reusable patterns. Known families include local file tools, narrow Git operations,
pytest, `uv run pytest`, and `scope demo-adapter` for the later rehearsal.

Command reuse is stricter than classification: quoted, escaped, compound,
redirected, custom-path and dynamic shapes require individual review. Every grant
use reclassifies first. Network grants require all exact destinations to match
the card; redirects and unrecognized endpoint syntax cannot reuse them.

Store.propose and Store.approve are separate operations. Approval is invoked only
by the human review path. Grants have fresh IDs, normalized session/agent/cwd/shell
bindings, monotonic 900-second expiry and atomically consumed budgets. A proposal
never replaces an approved grant. Proposals and grants are memory-only, capped at
1,024 contexts each; the watcher also caps tracked session identities at 1,024.

Revocation increments a generation and clears grants/proposals without waiting
for the terminal reader. Already queued grant, once and question answers are
invalidated. Responses recheck that generation under the same lock immediately
before sending. Fresh grants are committed at that final boundary and rolled back
under the lock if transmission fails or is cancelled. Another request cannot use
a pending, undelivered new grant. Existing budgets may be conservatively spent
when a caller disconnects; they never increase or reset themselves.

### IPC and events

The frozen `ipc.exchange(message, timeout=105, *, home=None) -> dict | None`
supports ping, proposal, request, question, notice, revoke, stop and shutdown with
the logical fields in INTERFACES.md. `watch.json` contains only port/token;
`watch.lock` holds an OS lifetime lock and intentionally remains after shutdown
to avoid replacing a lock inode while another process still owns it.

Messages are bounded UTF-8 newline-delimited JSON, at most 128 KiB including
authentication/framing. Endpoint files are at most 4,096 bytes. Tokens and random
per-request nonces are validated; the authenticated reply echoes the nonce.
Only 127.0.0.1 is connected/listened to. There is one message per connection, no
retry or resend, a bounded accept backlog and at most 16 active workers including
slow frame readers. The server's 95-second deadline starts on accept, leaving
room inside the client's 105-second deadline. Token values never enter logs.

POSIX files require private owned regular files without links; Windows uses an
owner-only protected DACL and a byte-range lifetime lock. Native Windows execution
of that code is still unverified. This is local same-user coordination, not a
boundary against another process owned by the same user. The launch-only mailbox
and its replay suppression remain integration work; they were not added in S2.

Internal transport callbacks for admission and guarded reply commit/finalization
carry no new logical IPC or host wire fields. The watcher runs no command or probe.
Questions return only human answers or explicit errors, with no grant semantics.
Arjun's caller remains responsible for recording prediction/consent and running
any separately approved probe.

Hook events `permission_request` and `permission_decision` share a fresh internal
request_id. Watcher `permission_review`, `permission_review_decision`,
`grant_created` and `permission_review_delivery` correlate with it. Proposal and
revocation events are `proposal_created` and `scopes_revoked`. Use the foundation
record shape `{ts, event, fields}`. S3 receipts must join by request_id, not count
watcher and hook records as separate requests. A watcher decision records review;
the hook's final decision records the returned envelope. `delivered` only means
socket sendall succeeded, not host consumption or command execution. None of
these events proves the command ran or reveals an unobserved native decision.

### Validation and remaining work

On macOS 14.8.4 / CPython 3.11.16 / uv 0.12.13:

- Full suite: **759 passed, 0 skipped**.
- Existing S1/foundation tests: 429; grant cases: 197; transport cases: 92;
  real TCP watcher/CLI cases: 33; terminal reply-tag/input cases: 8.
- Both shell tables remain evaluated on this Mac. Tests use isolated SCOPE_HOME,
  CODEX_HOME and CLAUDE_CONFIG_DIR plus explicitly labeled scripted human fixtures.
- `uv build`: source distribution and wheel succeeded. Installed that wheel
  noneditably in `.tools/wheel-check`: **759 passed, 0 skipped** in 11.52 seconds;
  imports resolved to site-packages and bundled policy resources were readable.

The test count reflects parameterized cases, including malformed inputs and both
shell dialects. It is not a count of executed shell commands. Socket and process
tests exercise local transport, hook entry points and lifetime locking, without
live hosts or model calls. Terminal polling branches are tested with injected
descriptor/console fixtures. Actual human terminal interaction, native Windows
ACL/console behavior, live hook trust and real-host approvals remain unverified.

No global hooks, agent skills, service accounts or paid integrations were installed.
Arjun's modules and the production foundation files remain unchanged. The next
milestone is S3: installation, permission skill, lifecycle hooks, receipts and the
offline smoke harness. Understanding integration remains pending Arjun's work.

### Sponsor guidance adopted

Optional Exa explanation guidance in the permission skill remains a later
addition after the permission workflow.

Any such retrieval belongs to the current coding agent, outside hook.py, tiers.py
and watcher decisions. Only generic public command concepts may be searched;
source, transcripts, private paths, environment values and full sensitive commands
must stay local. Retrieved text is untrusted, an unavailable explanation must not
change permission behavior, and Git push remains T3. MCP calls are outside this
shell PermissionRequest coverage. No Exa call or account setup was performed.

CopilotKit/web review, Ambiguous exports, new UI/transport/events and spending
remain separately coordinated later proposals, not S2 scope. No React framework,
model SDK or remote approval engine was introduced. The linked event page returned
HTTP 403 when checked; event rules and sponsor claims were not independently
verified in this milestone.
