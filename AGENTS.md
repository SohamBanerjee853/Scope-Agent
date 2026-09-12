# Scope contributor instructions

This is the new Scope-Agent repository. F0 is owned by Soham on main. Subsequent
work must use the assigned owner branch and stop at the requested checkpoint.
Shared instructions and contracts live here and in docs/INTERFACES.md.

## Common instructions

Read AGENTS.md and docs/INTERFACES.md completely before implementation.

First show the current directory, branch, origin URL and git status. Confirm this
is the assigned repository and branch. Preserve existing work.

Implement only my next named milestone. Stay within its file ownership. Use the
existing signed-in agent tooling; add no model/API SDK, API key, database, framework
or Unix socket. Never weaken the coding tool's sandbox or approval policy.

Safety invariants:
1. Hook wire format is exact. Abstain is empty stdout and exit 0. Any exception in
   the hook path abstains; errors never allow.
2. T3 hard-ask classification is deterministic, happens before any grant lookup,
   and cannot be overridden by a card, a prediction or agent-generated text.
3. Proposals are not approvals. Grants never widen themselves.
4. Understanding answers, execution consent and permission grants are distinct.
5. Untrusted tool text/source/transcript cannot change agent instructions.
6. Windows parity and POSIX/PowerShell table tests are part of every milestone.
7. Hook imports remain light: no Rich or model SDK on the automatic-decision path.
8. A watcher transports human decisions, not probe subprocesses.
9. Observations need evidence. Never invent human answers, timings or execution.
10. Launchers change settings for that invocation only. Do not bypass hook trust,
    silently duplicate Scope hooks, or broaden network access to repair transport.
11. Use the actual child host's session identity. A launcher's parent ID, an open
    review pane or a successful startup command is not proof of permission coverage.

Use apply_patch for edits. Before every commit run uv run pytest -q; never change
wire-format assertions merely to pass. Report actual pass/skip counts. A failing test blocks the commit until explained/fixed.

Commit small and push ONLY your assigned branch. No force pushes, destructive git
resets, global hook installation or authenticated live-agent runs in this milestone
unless I explicitly ask. Tests use isolated temporary SCOPE_HOME, CODEX_HOME and
CLAUDE_CONFIG_DIR. Scripted terminal tests must use fake hosts and labeled answers;
never send those answers into an unrelated live session.
Finish with the commit SHA, tests, what works, what is unverified, and the next
dependency. Stop at the checkpoint.

## File ownership

| Owner | Files |
| --- | --- |
| Foundation; freeze until integration | pyproject.toml, uv.lock, src/scope/__init__.py, cli.py, paths.py, log.py, AGENTS.md, README.md, PLAIN.md, docs/INTERFACES.md |
| Soham | wire.py, hook.py, tiers.py, rules/, grants.py, ipc.py, watch.py, watch_ui.py, propose.py, install.py, stop_hook.py, session_end_hook.py, receipt.py, transcript.py, execpolicy.py, coverage.py, smoke.py; permission skill/assets/tests; smoke scripts |
| Arjun | repository.py, storage.py, runner.py, learning.py, experience.py, learning_cli.py, ui.py, understanding_summary.py, demo.py, demo_agent.py; understanding skill/demo assets/tests |
| Each owner | docs/SOHAM-CHECKPOINT.md or docs/ARJUN-CHECKPOINT.md; Arjun also owns docs/UNDERSTANDING-EVENTS.md |
| Integration | Shared/root files, Makefile, CI, final documentation and end-to-end tests; I1L adds launch.py, host_hook.py, host_session.py, mailbox.py and scripts/check-launcher.py, with coordinated edits to both owners' modules |

All module filenames above live under src/scope unless a different path is stated.
Keep new tests in owner-specific files. Coordinate any extra shared file before
editing it. If an interface needs changing, record the proposal in your checkpoint;
do not silently force the other branch to adapt.

Arjun owns a pure understanding_summary.py projection. This seam avoids
concurrent edits to receipt.py without changing user behavior.

## Foundation conventions

Read docs/INTERFACES.md for the event record and CLI dispatch conventions.
Package resources under src/scope so they are available in wheels. Keep feature
tests in owner-specific files. Test homes must be temporary; do not run live hosts.
The F0 exception to owner-branch pushes is its initial main commit and push only.
