# A2/A3 parallel work

On September 12, Soham was explicitly assigned A3 while Arjun continues A2.
The published Arjun tip inspected for this integration is
`c808a3ba1aebe4deb0180de2bf5e8f85f85cde7f`; it contains A1, without experience.py
or A2's callable signatures. Integration waits on missing signatures while completing independent portions. This document is
coordination status, not a newly imposed A2 API.

Arjun subsequently published `6e5a6795f389b14eb09180fa960f6eff07e2a8f7` with
[A2-PREPARATION.md](A2-PREPARATION.md), proposed check/compare/choose_next/dispatch
signatures and three fixture tests. It explicitly waits for this main merge
before A2 implementation. These proposed boundaries inform the next adapter work;
they do not yet supply a callable engine or final callback/result schemas.

His follow-up `84a2e7f1a4365da853e636fb02a59c98640cbb1a` adds an integration
review and direct-question revocation diagnostic, plus LF/CRLF lock-test coverage.
It still contains no A2 engine. The audit also confirms that successful check/next
CLI result handling must be implemented alongside the eventual real adapters.

## Available A3 work

- learning_cli.py calls the existing learning.start/checkpoint/knowledge APIs.
- `scope revoke` sends the shared revoke IPC operation and records whether the
  watcher actually confirmed it; historical debugging knowledge is retained.
- ui.ask(prompt, *, repo, context="", home=None, timeout=105, ...) returns an
  actual answer plus human_ipc route provenance, or an explicit error. All callers
  use the authenticated watcher question protocol; only an interactive watcher
  reads tagged terminal input. Start `scope watch` before asking questions. Shared
  revoke and shutdown invalidate pending answers through the existing generation
  guards. ui.show displays bounded escaped text. Injected fixture responses still
  need explicit test_fixture evidence; a transport route alone proves no human.
- `scope skill` shows the packaged understanding guidance; `--install` writes
  `.agents/skills/scope-understand/SKILL.md`, preserving edits, with `--dry-run`.
- `scope demo --prepare-only DIRECTORY` creates the packaged disposable payment
  project in a new/empty directory, initializes Git and installs the skill. Each
  preparation gets its own demo UUID, never an inherited parent session.
- `scope demo-adapter probe` captures real JSON from the validated bundled bug or
  exact stable-key repair. It is a narrow demo command, not a general interpreter
  grant. Source/module injection and arbitrary script changes are refused.

## Waiting on Arjun's branch

The run_check/run_next adapters deliberately raise DependencyUnavailable. The
CLI forwards no predicted input or execution to an invented engine. The combined
scripted rehearsal is also gated before file creation, answers or probe execution.
This is partial A3, not an A2/A3 completion claim.

When Arjun publishes his work, inspect the real check/next callables, accepted
probe/reference/request shapes, result schemas and human ask/show callback
contracts. Adapt the CLI and demo to those APIs without changing his engine to
fit a guessed interface. Verify prediction is persisted before separately asking
for probe consent, refusal starts no process, T3 stays rejected, observations
use real bounded output and current source hashes, and typed JSON stays distinct.

Finish the scripted demo using explicitly labeled fixture callbacks through the
real A2 engine and TCP watcher: two probes, actual repair, actual tests, one
narrow scope, two hook allows and a synthetic T3 git-push request. All logs,
SessionEnd and receipts must share a fresh synthetic demo session. Fixture
answers prove neither human understanding nor agent-authored repair/time savings.
Next-task selection/dispatch cannot be reported as completion or a grant.

No A2 learning/experience/runner/storage implementation was edited for A3. The
existing event contract and pure understanding projection remain Arjun-owned.
The two-pane launchers, host registration and live/model checks remain separate
integration work; this parallel handoff does not implement them.
