# I1L startup and terminal integration

September 12, 2026. The user authorized continuation after A2/A3 and the local
ignore cleanup. Work remains on `integration/scope` until the final installed
package checks and both hosted CI jobs pass. Both feature histories are preserved.

## Implemented behavior

`scope codex` and `scope claude` provide invocation-only startup with the selected
bundled workflows. They accept task, project, model, mode flags, `--dry-run` and
`--manual-watch`. The installed Scope directory is added to the child PATH.
Unrelated host settings, authentication homes, sandbox, network policy, approval
policy and hook trust are retained. No normal startup copies fixture answers.
Visible existing Scope hooks cause an actionable migration error without edits;
managed/plugin settings are not exhaustively audited.

Automatic startup creates an isolated tmux server outside tmux or a new window
inside an existing server. It uses actual pane IDs, with agent left and review
right. The manual mode prints a run-specific watcher command and waits up to
30 seconds. Each run owns a private directory, short-lived environment handoff,
immutable launch definition, registered native session state and receipts.

Only actual SessionStart input registers identity. Parent, foreign, closed and
subagent startup cannot take over the main run. Both supported shell tools route
through the existing deterministic classifier and exact permission envelope.
Understanding-only mode omits permission hooks. Proposals, existing task reads
and mutations, and question replies all check the current native identity.
Question messages carry a native session ID only inside a launch; legacy
standalone behavior remains available.

`scope ready` distinguishes registered native startup, reachable human review
and actual observed permission requests. Startup registration is not interception
proof. A kernel-owned lifetime lock lets review detect owner crashes without
trusting a reusable PID. Native SessionEnd and launcher-inferred exit remain
distinct evidence. Shutdown revokes pending decisions, removes the owned endpoint
and ephemeral environment handoff, and preserves known host exit codes even if
a receipt write fails. If the exit record itself cannot be saved, an outer process
cannot recover an unknown exit status; it reports that limit instead.

Launch-only mailbox fallback occurs solely before a TCP send attempt. It shares
authentication, replay suppression, cancellation and bounded decision workers
with TCP. Frames are at most 128 KiB, pending client leases at most 128, and
normal workers share one extra revoke/shutdown control slot. The 8,192-entry
nonce history fails closed at capacity until watcher restart. At most eight
leftover generation directories are tolerated before explicit inspection is
required. Publication or socket send success proves no caller consumption or
command execution. This is same-user local coordination, not a tamper-proof
boundary against another process with the user's filesystem access.

Next-task selection still reports delivery unattempted. A coding host that has
received the result can explicitly acknowledge its exact instruction digest using
`scope next TASK --acknowledge HANDOFF --received-sha256 DIGEST`. It must match
the same open native session and selected A2 handoff. The unchanged one-attempt
dispatch engine records the outcome; a supplemental event and receipt launch
section identify the acknowledgment as `caller_reported`. It proves no automatic
upstream host send, independent authorship, execution or completion.

## Verified host interfaces

Local version/help checks observed Codex CLI **0.154.0**, Claude Code **2.1.267**,
and tmux **3.7c**, on macOS **14.8.4** with Python **3.11.16**.
The launcher uses documented inline Codex hook groups and preserves exact hook
trust; see [OpenAI's hook reference](https://learn.chatgpt.com/docs/hooks) and
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
Codex first-use `/hooks` review remains a human action; its first task triggers
native startup in the recorded baseline.

Claude uses run-local `--settings` plus appended guidance. Its list-merging
behavior retains other configured hooks. Windows hooks explicitly select the
documented PowerShell shell; supported Bash/PowerShell inputs use their own tool
identity. See [Claude's CLI reference](https://code.claude.com/docs/en/cli-reference),
[settings precedence](https://code.claude.com/docs/en/settings), and
[hook reference](https://code.claude.com/docs/en/hooks). Managed constraints and
host decisions before PermissionRequest remain authoritative. Claude sandbox
network prompts and native rule/transcript coverage remain outside this adapter.

## Observed offline evidence

The installed `scripts/check-launcher.py` rehearsal passed all four combinations:
Codex and Claude, each inside and outside a real tmux server. These executables
are explicitly guarded fake hosts that parse the generated configuration and
invoke the actual installed adapters. They use disposable homes, no real account
credential variables, and three labeled terminal answers per run.

Each flow recorded three permission requests, two automatic scope-backed allows,
one T3 hard ask, one grant, and zero denies or allow-once decisions. It performed
one actual prediction/consent/probe/observation cycle, preserved host exit 0 and
the correct receipt, and removed the owned panes, endpoint and private environment
handoff. Inside-tmux runs used pane indexes 11/12 and preserved an unrelated
sentinel window. All temporary rehearsal trees were removed.

The rehearsal exposed a persistent stale-endpoint race at normal exit. The
watcher's main thread now waits for concurrent close cleanup, and the owner waits
for endpoint removal and watcher-lock release before destroying its window.
The complete four-flow installed rehearsal passed after those fixes.

Focused tests also exercised actual owner process death, blocked TCP with real
file fallback, disconnected clients, cross-transport nonce replay, saturation,
late answers, native identity mismatch, source mutation after closure, receipt
isolation and known host exit status through a receipt-write failure.

Final local validation used a refreshed locked noneditable install, with all 45
installed package files compared byte-for-byte against the source:

- `UV_NO_EDITABLE=1 uv run pytest -q`: **1,747 passed, 0 skipped, 217.70 seconds**.
- `uv build`: source distribution and wheel built successfully.
- Installed `scripts/check-package.py`: package provenance, resources, receipt
  projection and both permission dialect smokes passed.
- Installed `scripts/check-demo.py`: actual payment bug/repair, regressions and
  both combined A2/A3 rehearsals passed with fixture provenance and cleanup.
- Installed `scripts/check-launcher.py`: all four real tmux/PTY fake-host flows
  passed against the final installed code, including correct receipts and cleanup.
- Skill validation, UTF-8/local Markdown links and diff checks passed. Both
  archives include the launcher code/resources; the source archive includes its
  harness and checkpoint. Private contributor filenames are absent from tracked
  content and both package archives.

The first hosted run exposed a timing assumption in the mailbox timeout test and
double escaping in printed Windows replay commands. The test now synchronizes on
handler admission before expiring its caller deadline; command output preserves
shell quoting while rejecting terminal controls. The verification command also
forces a package refresh after source edits to avoid reusing an older local wheel.
The corrected integration commit starts new macOS/Windows hosted checks. Their
actual completed results must be reviewed before advancing main. Native startup
and human acceptance below remain separate even if both CI jobs pass.

## Remaining human and platform checks

No authenticated coding/model session, global hook installation or sponsor service
was started. CLI help and fixture SessionStart events are not native startup proof.
Upstream native hook execution/trust, real-human prediction and consent, and an
actual host receiving/acknowledging a selected instruction still require human
acceptance. Native Windows manual-pane startup has not been exercised; its shared
Python and PowerShell contracts are checked separately in Windows CI. WSL/tmux
support is not evidence of native Windows pane behavior. No benchmark or time
savings is claimed.
