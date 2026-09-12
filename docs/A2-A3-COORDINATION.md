# A2/A3 integrated handoff

Arjun published A2 at `23d96d1`, based on reconciled main `37f4b75`. The user
asked Soham to pull it and continue A3. It merges normally with Soham's independent
improvements through `c990f80`; both histories remain intact.
[A2-API.md](A2-API.md) is the actual contract and supersedes A2-PREPARATION.md.

## CLI and human answers

- `scope check TASK_ID --spec check.json [-C PROJECT] [--json]` calls the real
  `experience.check`. The spec path belongs to the calling directory; `-C`
  selects the source and execution project. Fields are question, citations,
  literal top-level JSON field and argv, with optional shell and timeout.
- `scope next TASK_ID --smaller INSTRUCTION [--larger INSTRUCTION] [-C PROJECT]
  [--json]` calls `experience.choose_next`. The caller supplies concrete actions
  based on evidence; the engine does not invent a diagnosis.
- Both return saved task/session identity. Start/checkpoint/knowledge use the same
  source and storage. Revoke uses the shared watcher and retains saved knowledge.
- Structured adapters use authenticated `ui.ask` for every answer. Only an
  interactive `scope watch` reads terminal input. Prediction is strict typed JSON
  with value/reason/optional assistance; consent is separately parsed from literal
  true/false, and next-task choice is smaller/larger/defer.
- Exact argv, cwd and offered instructions must fit the bounded prompt or the
  adapter refuses to ask. Evidence excerpts are escaped, bounded and labeled
  untrusted. Omitted context cannot become an answer or authorization.

Checks distinguish completed comparisons from skipped/interrupted/not-verified
outcomes. Both matched and mismatched comparisons are real evidence and exit 0;
neither establishes the whole repair. Incomplete outcomes exit nonzero. Current
knowledge uses current_status/source_status; historical results stay historical.

Next-task selection/defer is a recorded outcome. The CLI explicitly reports
delivery_status=not_attempted and creates no dispatch event. An instruction on
stdout is not acknowledged host delivery. I1L adds an explicit receiving-caller
operation: `scope next TASK --acknowledge HANDOFF --received-sha256 DIGEST`.
It validates the same open native session, project and exact selected bytes before
using `experience.dispatch`, and records a separate `host_handoff_acknowledged`
event with `caller_reported` provenance. This is self-reported receipt, not an
automatic upstream host send or independent caller-authorship proof. Selection,
acknowledgment, execution, completion and permission remain distinct.

## Rehearsals and packaging

`scope demo --prepare-only DIRECTORY` still prepares only a new/empty directory.
`scope demo --scripted [--shell posix|powershell] [--json]` owns temporary homes and
a project and accepts no existing directory. It uses a fresh demo UUID, never a
parent host session, and labels automated answers and the fixture-authored repair.

The combined rehearsal uses the real A2 engine and TCP watcher, saves prediction
before separate consent, executes actual bounded probes and packaged regressions,
and produces a combined receipt. A selected instruction is delivered to a
deterministic fixture inbox and consumed by fixture repair code; that is not
production coding-host delivery. The permission side verifies a narrow grant,
two exact hook allows, revocation and a synthetic T3 git-push request that never
runs. Actual observations and regressions establish only fixture behavior, not
human understanding, an autonomous coding-agent repair or time saved.

The installed checker separately verifies packaged assets, the known bug/repair
and A1 checkpoints, then runs the combined rehearsal for both shell dialects.
Installed-resource and permission smoke checks remain in both native CI jobs.
Validation is recorded in [the reconciliation checkpoint](RECONCILIATION-CHECKPOINT.md).

## I1L continuation

The user subsequently authorized I1L on the integration branch. It adds
Codex/Claude two-pane launchers, native SessionStart registration, readiness,
launch-owned watcher lifecycle, mailbox fallback and explicit receiver
acknowledgment. Validation and platform limits belong in the startup checkpoint.
No global hooks, model session or sponsor account was installed for A3/I1L.
Real human prediction/consent, upstream hook trust and live-host acceptance remain
manual checks. Native Windows manual-pane startup is not established by its
shared Python/PowerShell contract tests.
