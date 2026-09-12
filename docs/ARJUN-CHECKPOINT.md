# Arjun checkpoint: A1 repository evidence and bounded runner

September 12, 2026. Branch: `arjun_branch`. **A1 is implemented; A2–A4 and I1L
are not implemented.** Arjun requested local commits only, so nothing was pushed.
The remote is `https://github.com/SohamBanerjee853/Scope-Agent.git`.

The remote was empty at setup. Arjun implemented the minimal shared
foundation on this branch; its local commit is `c822dc0`. That commit passed
39 tests and built its wheel and source distribution.

## Implemented interfaces

| Module | Entry points and responsibility |
| --- | --- |
| `repository.py` | `find_root(path)`, `snapshot(path)`, `validate_citation(source, citation)`, `evidence_status(references, source)`, `changes(before, after)` |
| `storage.py` | `project_id(root)`, `project_dir(root)`, `project_lock(root, timeout=5)`, `load(root)`, `update(root, change, timeout=5)` |
| `runner.py` | `run(argv, *, cwd, timeout=20) -> RunResult`; `to_dict()` preserves the captured result |
| `learning.py` | `resolve_session`, `start(path, description, session_id=...)`, `checkpoint(path, task_id, note=...)`, `knowledge(path, task_id)`, `version_observation` |
| `understanding_summary.py` | Pure `summarize(events) -> dict` for the receipt integration |

Read `learning.py` first to follow task start and checkpoint, then `repository.py`
for source evidence, `storage.py` for persistence, and `runner.py` for execution.
The understanding CLI belongs to A3, so `scope start` and other feature commands
still fail explicitly. `scope --help` lists the reserved routes.

## Evidence and storage behavior

Snapshots include tracked and nonignored untracked relevant UTF-8 source, with
full-content SHA-256 hashes, validated line citations and bounded Python symbols.
They skip private/secret paths, obvious secret content, binaries, all symlinks,
environments, dependency trees, lock files and generated noise. Tracked files that
have subsequently become ignored are excluded too. Privacy filtering cannot
detect every possible embedded secret and is not a filesystem sandbox.

Limits: 32 KiB per included file, 512 KiB included source in total, 160 files,
2,048 explicit skip records, 64 KiB diff text. Git listings are bounded by the
runner; truncation marks the listing incomplete, and incomplete filenames are
discarded. Skipped-record overflow is counted. Missing evidence means unavailable,
not deleted. Diffs compare only captured source, never an unrestricted Git diff.

State resides under SCOPE_HOME/projects/<SHA-256-of-canonical-repository-path>.
Moving a repository gives it a different local identity; separate checkouts are
intentionally separate. Atomic JSON replacement uses a persistent OS-lock file,
POSIX `flock` or Windows one-byte `msvcrt` locking. Lock files are not deleted to
“fix” contention. Crashed-owner release and concurrent increments are tested.
Corrupt, mismatched, incomplete or oversized state is rejected and preserved.

Each task preserves its initial source manifest and its latest full snapshot;
only its five most recent checkpoint records are retained. Distinct task UUIDs
coexist without overwriting each other. The 8 MiB project-state cap causes an
explicit error rather than silently erasing old evidence. Source/hash mismatches
inside stored state are rejected. State and event-log writes are separate, so a
crash between them can leave a missing receipt event; see the event contract.

Old observations retain their historical result. Fresh knowledge checks report
changed source as stale and missing/invalid references as unverified, with
`current_status: not_verified`. They do not assign mastery, infer forgetting,
alter permission grants or convert fixture provenance into a human answer.

## Runner and platform limits

The runner accepts a list of arguments and explicit cwd, inherits the caller’s
environment, uses `shell=False`, supplies no interactive stdin, and enforces a
20-second default / 300-second maximum timeout. Each stream is drained separately
with a 64 KiB returned UTF-8 cap. Nonzero exits, spawn errors, timeouts, truncation
and incomplete cleanup are represented explicitly. Invalid argv/timeouts and
batch executables are rejected before spawning; Windows can otherwise shell-launch
`.bat`/`.cmd` files even when shell=False.

POSIX cleanup kills the dedicated process group. Windows uses a bounded
`taskkill /PID ... /T /F` attempt and direct-child kill. Descendants can escape
either mechanism; a lost parent can limit Windows tree cleanup. Output draining
has a bounded wait and marks pipes that remain open as incomplete. An escaped
descendant may retain a pipe/thread until it exits. This is best-effort cleanup,
not complete process containment or a replacement for host restrictions.

Both POSIX and PowerShell-shaped path/argv tables run on this Mac (five POSIX,
six PowerShell cases), plus Windows lock/cleanup API fixtures. **Native Windows
execution has not been run.** No shell scripts were added, so no missing
PowerShell twins are implied.

Implementation references: [Python subprocess documentation](https://docs.python.org/3/library/subprocess.html),
[Windows locking documentation](https://docs.python.org/3/library/msvcrt.html), and
[Git ls-files documentation](https://git-scm.com/docs/git-ls-files).

## Verification performed

| Environment | Result |
| --- | --- |
| macOS arm64, Python 3.14.7, uv 0.12.13, noneditable package | **124 passed, 0 skipped**, 10.24 seconds |
| macOS arm64, Python 3.11.16, separate noneditable environment | **124 passed, 0 skipped**, 10.33 seconds |
| Isolated archive of Soham’s exact S1 SHA, overlaid only with Arjun’s five modules and 85 A1 tests; Python 3.11.16, noneditable install | **514 passed, 0 skipped**, 10.58 seconds |
| `uv build` | Wheel and source distribution built successfully |
| Wheel/install inspection | All five A1 modules exactly match current source and installed bytes |
| CLI help, Git whitespace/ignore checks | Passed |

These runtimes are measured test-suite runtimes, not an agent-performance benchmark.
The isolated S1 compatibility check preserved Soham’s production foundation and
permission files and all 429 of his tests. It did not merge Git histories or claim
completion of I1, the watcher, live hooks or the prediction/consent workflow.
Tests use temporary Git repositories and isolated SCOPE_HOME, CODEX_HOME and
CLAUDE_CONFIG_DIR. Every synthetic observation is labeled `test_fixture`.

The macOS environment applied the hidden-file flag to the editable `.pth` file;
Python 3.14 then ignored that file and could not import Scope. A noneditable
install resolved this without changing Python’s protections. During verification,
a cached older wheel also caused a new regression to fail; explicitly rebuilding
and reinstalling the package fixed the environment, and the unchanged assertion
then passed. The final source/install/wheel byte comparison guards against that.

Portable reproduction (assuming uv is on PATH):

```sh
uv sync --locked --no-editable --reinstall-package scope-agent
uv run --no-editable pytest -q
uv build
uv run --no-editable scope --help
```

Reinstall after source edits when using a noneditable environment. In this session
uv was available through a temporary tooling environment at
`/tmp/scope-arjun-tools/bin/uv`. The pre-commit test invocation uses
`UV_NO_EDITABLE=1 uv run pytest -q`; the second interpreter uses a separate
UV_PROJECT_ENVIRONMENT. No package compatibility claim beyond tested versions.

## Receipt handoff and next dependency

[UNDERSTANDING-EVENTS.md](UNDERSTANDING-EVENTS.md) freezes the detailed fields for
Soham to review. The summary has exactly `tasks`, `predictions`, `observations`,
`executions`, `next_tasks`, `dispatches`, `skipped_checks`, `deferred_tasks`,
`revocations` and `meaning`. It copies historical records, does no I/O, and neither
imports nor mutates grants. Only task-start/checkpoint events are emitted in A1.

During A1, Soham published foundation
`3d6144cedf9d4c9107c9012d688a19158fc3c225` and S1
`54f39c769df4603e7bae8fb7b63d823657c15662`. His committed S1 checkpoint identifies
that commit as the tested handoff and reports 429 passing tests on his Mac.
Those are his reported results, distinct from the checks above.

**Next: reconcile the independently created foundations, then merge that exact S1
SHA before A2.** Preserve both histories; do not use a destructive reset,
cherry-pick duplicate permission history, or wholesale conflict choices. Shared
differences include the logger envelope (`fields` nested on published main versus
flat locally), session filenames, CLI missing-feature exit codes and foundation
tests. Arjun’s summary already supports both logger envelopes without changing
the shared logger. No merge or push was performed at this A1 checkpoint.

A2 must still establish prediction-before-consent ordering, refusal/skip behavior,
T3 rejection with the real classifier, valid typed observations and next-task
handoff. A3 must add CLI/UI, current-host skill and the fake-payment rehearsal.
Real watcher transport, receipts, launchers, native host hooks, human sessions and
Windows execution remain pending. No paid model calls, global hooks or model SDKs
were introduced. Sponsor recommendations are a separate requested document, not
an implemented change to any of these interfaces.
