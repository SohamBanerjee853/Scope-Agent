# Soham checkpoints

The permission-side handoff is S4, recorded below. S1 and S2 are historical evidence
for handoff commits `54f39c769df4603e7bae8fb7b63d823657c15662` and
`20b2024ff1e0609c1833d73e947eb2d4d0e762e2` respectively.

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

## S2 (historical): bounded grants and the human watcher

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

## S3 (historical): installation, receipts and offline smoke

Completed September 12, 2026 on `work/soham-permissions`, based on S2 commit
`20b2024ff1e0609c1833d73e947eb2d4d0e762e2`. The commit containing this section is
the S3 checkpoint; its pushed SHA is recorded in the handoff response. This does
not merge the feature branch into main or complete the S4 review.

### Manual installation and permission skill

`scope install --dry-run` prints the proposed hooks without creating files. The
default destination is `CODEX_HOME/hooks.json`; `--project PATH` explicitly
targets that project's `.codex/hooks.json`. Actual installation uses an absolute
Scope console script from the running Python environment, with independently
quoted POSIX `command` and PowerShell `commandWindows` strings. PermissionRequest
matches Bash/PowerShell with a 110-second timeout; Stop and SessionEnd use three
seconds. No executable path assumes a checkout named `.venv`.

Installation preserves unrelated hooks, backs up exact prior bytes, and replaces
the configuration atomically under an installer lock. An identical installation
is unchanged. An exact generated installation can switch to passive `--abstain`;
edited, older, mixed or duplicate Scope entries require explicit migration.
Visible user/project JSON and TOML configurations are checked for duplicates;
this is not an exhaustive managed/plugin hook audit. Nonregular files, links,
ambiguous JSON and replacement during reading are refused. A stale installer
lock after a crash requires inspection; it is not removed using a PID guess.
Persistent `--always-allow` installation is refused.

The installed Codex CLI reported **0.154.0**. Its local help and the current
[official hook documentation](https://learn.chatgpt.com/docs/hooks) were checked.
All matching permission hooks contribute: a denial takes precedence, otherwise
an allow can permit the request. A passive Scope hook cannot cancel another
hook's allow. Hooks run only for requests needing native approval. Installation
does not establish trust: review the exact definitions in `/hooks` and restart
as required. Native hook execution was not tested during S3.

`scope install-skill --dry-run` previews the bundled permission skill; the default
`CODEX_HOME/skills/scope-permissions/SKILL.md` is retained as a compatibility
location. `--project PATH` uses `.agents/skills/scope-permissions/SKILL.md`, matching
the project location in the current
[official skill documentation](https://learn.chatgpt.com/docs/build-skills).
Native discovery of either installed skill was not exercised; check `/skills`
before relying on it. Existing edited skills are preserved. The skill explains
bounded proposals, actual session identity, separate human approval, T3 limits
and untrusted source/tool/transcript excerpts. It introduces no model client.

Safe preview and offline commands, valid from either shell:

```text
uv run scope install --dry-run
uv run scope install --project . --abstain --dry-run
uv run scope install-skill --project . --dry-run
uv run scope smoke --dry-run
uv run scope smoke --shell powershell --dry-run
uv run scope smoke --json
uv run scope smoke --shell powershell --json
```

### Lifecycle and receipt evidence

Stop reads bounded lifecycle JSON, appends `stop` with `status=turn_stopped`, and
attempts a watcher notice with a 0.25-second deadline. SessionEnd only appends
`session_end` with `status=ended`; it performs no network, rendering, transcript
read or native policy evaluation. Neither hook emits a decision. All hook
exceptions abstain with empty stdout and exit zero. These events use the native
input's session_id, never an inherited parent session identifier.

`scope receipt SESSION --home PATH [--json]` writes/replays a schema-1 projection.
Requests and grants correlate by their internal IDs; hook and watcher records
are not counted twice. Conflicting context/decisions remain unknown. An observed
T2 allow with missing review provenance remains an allow with unknown category,
instead of inventing automatic reuse or a one-time choice. Failed watcher grant
delivery is excluded. Native decisions and command execution remain unknown.
Lifecycle status is independent of permission counts.

The understanding object calls `understanding_summary.summarize(events)` through
the frozen ten-field contract. In this branch the module is absent, so receipts
explicitly say `pending_integration`. Tests inject the complete pure projection;
a broken projection is an error, not silently discarded evidence. Schema-1
receipts without understanding remain readable; `--rebuild` can add the summary
from available events after integration. A stored receipt for another session is
rejected.

Receipt reads cap the session log at 32 MiB and skip malformed/incomplete records.
The reader preserves the foundation `{ts,event,fields}` schema in a bounded helper:
the frozen `log.read` has no size cap or home override. This is documented local
adaptation, not a shared module change. Writer locks serialize atomic file
replacement; raw evidence is never rewritten. Cached projections ignore
`receipt_written`, include session identity, and are compared with the fresh
projection before reuse, preventing stale/corrupted counts from surviving replay.
Missing/truncated evidence is explicitly labeled.

### Conservative native policy audit

Audit is optional: `scope receipt SESSION --audit --transcript FILE --rules FILE`.
Rules may be repeated; no native configuration or transcript discovery occurs.
Use `--host claude` to label Claude receipts; Claude's Codex-specific coverage is
always unknown and does not read those inputs. Without audit, native coverage is
explicitly not requested.

Transcript reads cap one regular file at 16 MiB, records at 128 KiB, and candidate
commands at 1,000. Only supported shell tool requests with structured argv can
reach `codex execpolicy check`; plain command strings and embedded code-mode
JavaScript remain unknown. The candidate argv is data after `--`, never executed.
The checker uses explicitly supplied current rules, with a shared five-second
policy budget, capped stdout/stderr and bounded cleanup overhead. Unsupported
native match formats, timeouts and missing helpers produce unknown findings.
Candidates from log/transcript sources can overlap; audit counts are estimates,
not permission counts or evidence of historical native answers/execution.

The syntax was checked against local Codex help and the
[official rules documentation](https://learn.chatgpt.com/docs/agent-configuration/rules).
A read-only native check against one newly created synthetic `git status` rule
returned an allow with matching prefix evidence under isolated homes. It did not
run Git or launch a coding session. Native Windows requires a discoverable
`codex.exe`; `.cmd` wrappers are refused to avoid implicit shell execution.

### Actual verification and next dependency

On macOS 14.8.4 / CPython 3.11.16 / uv 0.12.13:

- Full suite: **952 passed, 0 skipped** in 14.88 seconds.
- S3 additions: 44 installer, 27 lifecycle/receipt, 104 transcript/policy/audit,
  and 18 smoke cases; the previous 759 cases remain. Parameterization checks
  concrete malformed inputs and both shell dialects, not a desired test total.
- `uv build` produced a source distribution and wheel. Noneditable wheel install
  in `.tools/wheel-check`: **952 passed, 0 skipped** in 14.69 seconds, importing
  from site-packages. Installed assets: allow.yaml 999 bytes, hard-ask.yaml 1,333
  bytes, permission SKILL.md 3,345 bytes. The skill frontmatter validator passed.
- Installed hook/skill dry-runs resolved the wheel environment's console script
  and created no files. Both installed offline smoke dialects passed. The POSIX
  wrapper and its dry-run were exercised; the PowerShell wrapper is provided,
  but no native `pwsh` executable was available on this Mac.

Each smoke uses a new demo UUID and disposable homes in a separate worker,
removing inherited host/launch identities. The real TCP watcher and actual hook
subprocesses observed four requests, two automatic allows, one scope grant and
one synthetic T3 hard ask. Revocation blocked reuse with one budget unit still
unused. Requested commands were never executed. SessionEnd and receipt replay
used only that synthetic session; temporary homes were removed. Scripted fixture
answers prove neither human understanding nor elapsed-time savings.

`scope smoke --live --dry-run` prints the manual prerequisites: check the installed
host, use on-request approval, explicit temporary prompt rules, invocation-only
hooks and normal hook trust in a disposable project. `--live` alone refuses;
native launch automation belongs to integration. No live/model session, global
hook, skill, account or sponsor service was installed or launched during S3.
Windows quoting/parser tables passed on macOS; native Windows hooks, ACLs,
PowerShell wrapper execution, helper cleanup and sandbox behavior remain
unverified. Host trust and actual permission interception remain human checks.

Both private planning guides remain gitignored and excluded from package assets.
The sponsor workflow still prioritizes S1–S4. Optional Exa explanation guidance
is deferred until afterward; CopilotKit/web review, Ambiguous exports, new events
and spending require later coordination. No sponsor dependency or SDK was added.

Next: S4 permission-side review and final handoff. Arjun's understanding projection
and the combined receipt remain I1 dependencies. Host launch cleanup, actual
session registration and invocation-local Codex/Claude adapters remain I1L work.

## S4: final permission-side review

Reviewed September 12, 2026 on `work/soham-permissions`, after S3
`f66d188160078942eeba57694abc7dcd163360a1`. This commit is the final S4 permission
handoff; its full pushed SHA is recorded in the handoff and reconciliation
checkpoint. Earlier S1 handoff: `54f39c769df4603e7bae8fb7b63d823657c15662`.
Reconciliation into main follows S4. Soham owns A3 while Arjun works on A2. Those integration changes are separate from
this owner-branch checkpoint.

The review confirmed the strict allow/deny/empty-abstention envelope, T3 before
grant lookup, final reclassification before human grant commit, finite atomic
budgets, monotonic expiry, context binding and revocation generation checks.
Questions carry human answers without granting permission, and the watcher never
executes probes. Existing exact wire assertions remain unchanged.

Two owned defects were fixed with focused regressions:

- POSIX `<>` is a read/write redirection. The lexer now recognizes that operator
  as one token and treats an outside-workspace target as T3; a local target stays
  reviewable T2. Quoted literal text remains data.
- A full normal worker pool could prevent revoke/shutdown from arriving while
  approvals waited. Watcher now reserves one additional authenticated control
  reader for those two operations. Its frame-read deadline is 0.5 seconds; other
  kinds cannot enter handler/UI work through that slot. The default bound is
  16 normal workers plus one control reader. Generic IPC Server retains its
  original bound unless this option is enabled. Saturated once/grant/question
  requests are cancelled by successful revoke/shutdown, and late answers cannot
  authorize a response. This does not guarantee availability under socket flooding.

Public CLI commands remain hook, stop, session-end, install, watch, propose,
install-skill, receipt and smoke, with the options recorded in S2/S3 above.
`ipc.exchange(message, timeout=105, *, home=None)` and all logical message fields
in INTERFACES.md remain unchanged. The added Server control reader is an internal,
opt-in transport option; no native wire or cross-owner callable changed.

On macOS 14.8.4 / CPython 3.11.16 / uv 0.12.13, the final full suite passed:
**969 passed, 0 skipped**, 15.48 seconds. Classifier table/review coverage is now
**126 POSIX, 129 PowerShell and 4 unsupported-shell cases**. Added tests are six
redirection regressions, six saturated cancellation cases and five overflow
authentication/kind/bound cases. These are inert command strings or explicitly
scripted local transport fixtures, not live shell commands or human approvals.

`uv build` produced both archives. Rules remain 999 bytes (allow.yaml) and 1,333
bytes (hard-ask.yaml); the permission skill remains 3,345 bytes. Installed-resource
inspection and both offline smoke dialects passed. Each smoke observed four
requests, two allows, one grant and one synthetic hard ask, with revocation before
the remaining budget was spent and cleanup of its isolated homes. Both local
planning guides are absent from the wheel and source archive.

One shared packaging change is proposed for reconciliation: add `/scripts` to
the source-distribution include list so both tracked smoke wrapper files ship
there. The installed Python smoke CLI and wheel resources already work. S4 did
not modify shared packaging or any Arjun module to imply integration.

Classifier limits remain lexical: executable contents, aliases, shell profiles,
filesystem links and the behavior inside recognized test tools are not inspected.
The host's sandbox, executable resolution and native approval policy remain
authoritative. Native answers, actual execution, managed/plugin hook coverage,
human understanding and timing savings are never inferred from fixture allows.

Native Windows hook execution, ACL/console behavior, live host trust, actual
PermissionRequest interception and real-human review remain unverified. No global
hooks, model calls, services or sponsor SDKs were added. The optional Exa guidance
proposal stays deferred while the explicitly requested A3/reconciliation proceeds.
Arjun's available A1 projection and the missing A2 interfaces must be assessed on
the integration branch; A2 consent and observation behavior is not claimed here.
