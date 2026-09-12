# Reconciliation and parallel A3 checkpoint

September 12, 2026. Soham owns S4, branch reconciliation and A3; Arjun owns A2.
Independent integration work proceeds while engine APIs are pending. This
checkpoint records the implementation and validation available at each stage.

## Histories and conflict decisions

Soham's S4 permission handoff is `20d6ce03ecfbf792b9515c0088e9ae5326de3928`;
S1 was `54f39c769df4603e7bae8fb7b63d823657c15662`. Arjun's available tip is
`c808a3ba1aebe4deb0180de2bf5e8f85f85cde7f`, containing A1 and debugging regression
fixes. Their independently created foundations have no merge base. Reconciliation
uses normal merge commits with unrelated histories explicitly allowed. Neither
feature branch is rewritten or deleted; both tested tips must be ancestors of
the final main merge.

Before the main merge, Arjun published preparation tip
`6e5a6795f389b14eb09180fa960f6eff07e2a8f7`. It adds an A2 implementation brief
and three independent fake-payment fixture tests, without changing production
modules. That tip is also merged with history preserved. His brief explicitly
waits for main reconciliation before implementing the proposed A2 APIs.

A final branch fetch found `84a2e7f1a4365da853e636fb02a59c98640cbb1a`, with
Arjun's integration audit, a manual direct-question revocation diagnostic and
LF/CRLF coverage for his real crashed-owner lock test. The overlapping readiness
assertion is resolved to his complete parametrized test. This newer tip is also
preserved in the merge; it contains no A2 engine implementation.

The published main foundation is the canonical nested `{ts,event,fields}` event
schema, safe session-/sha256- filenames, lazy CLI dispatch and missing-feature
exit code 1. Arjun's five production modules work against those APIs unchanged;
his pure projection already accepts nested records. Duplicate foundation tests
were reconciled to these documented semantics, retaining containment, Unicode,
malformed-record, lazy-import and explicit-error coverage. No permission wire
assertion was weakened.

Package dependency bounds and lock resolution remain the published main values.
Arjun's strict pytest marker setting and module entry point are retained. Shared
metadata/help/docs now describe actual available behavior. `/scripts` was added
to the source archive so both smoke wrappers ship; the wheel contains both skills
and all fake-payment assets. Root contributor instructions record the current
A2/A3 division. Historical owner checkpoints remain their original evidence.

Old Arjun A1 event files used bare SHA-256 filenames and flat bodies. Existing
on-disk logs are not silently moved or rewritten by this merge. The canonical
receipt command reads the published main filename/envelope; legacy Arjun logs
need an explicit migration before replay. No real user homes were inspected or
migrated. Project state APIs and Arjun's source/runner behavior remain unchanged.

## Working seams and partial A3

Real A1 task events appear in schema-1 receipts through Arjun's actual summary.
Permission counts stay separate from prediction/execution evidence. Watcher
revocations now include factual session/revoked fields consumed by that summary.
The offline permission fixture loads the summary without fabricating predictions.

A3 provides start/checkpoint/knowledge, shared revoke, authenticated watcher
questions, the project understanding skill, disposable demo preparation and the
narrow payment probe adapter. The real fake-payment regression observes two
charges before the stable-key repair and one after it. User edits and nonempty
directories are preserved. Neither preparation nor a fixture answer claims human
consent, understanding or completion.

Check/next and the combined scripted rehearsal remain explicit dependency errors
until Arjun publishes A2's actual APIs. See [A2/A3 coordination](A2-A3-COORDINATION.md).
No substitute consent engine, invented function signature, model SDK, live host,
global installation, service account or sponsor integration was added. The
Codex/Claude pane launchers and full I1/I1L rehearsal are still future work.

Arjun's final audit reproduced a direct-terminal answer surviving shared revoke.
The A3 question helper now sends every question through the existing watcher
protocol, as required by the frozen interface. There is no separate terminal
reader or new revocation state. Callers need an interactive `scope watch`; absence
returns an explicit error. Existing watcher generation checks reject late answers
after revoke or shutdown. This supersedes the reviewed direct route described in
[Arjun's historical audit](INTEGRATION-REVIEW.md). Route provenance does not turn
injected fixture answers into human evidence or a durable execution authorization.

The updated standalone diagnostic returned exit 0 / `late_answer_rejected`:
the real watcher generation advanced from 0 to 1, its tagged question context
became inactive, and the late fixture answer was absent. The diagnostic also
forbade caller-side terminal reads and observed zero probes or child processes.
Its input was explicitly simulated; no human answer was claimed.

## Validation stages

S4 passed **969 tests, 0 skipped**, build and installed offline smoke checks on
macOS; its detailed limits are in [Soham's checkpoint](SOHAM-CHECKPOINT.md).
The first combined A1/A3 reconciliation passed **1,210 tests, 0 skipped** in
53.09 seconds, followed by **11 demo tests** after final marker/reparse hardening.
Both package archives built. Final S4 ancestry and hosted macOS/Windows results
are recorded below when complete, not inferred from pending jobs.

CI uses pinned verified action revisions, Python 3.11 and uv 0.12.13, a locked
noneditable install, full pytest, build, installed resource checks, both offline
permission smoke dialects and native wrapper dry-runs. It has read-only contents
permissions and no model credentials. Live hooks, native coding-host behavior,
real human debugging and pane startup remain outside these offline checks.

The final local merge of S4 into the reconciled history passed **1,229 tests,
0 skipped**, in 53.25 seconds on macOS 14.8.4 / CPython 3.11.16 / uv 0.12.13.
The source distribution and wheel built successfully. A separate noneditable
wheel environment verified both skills, all demo assets, the real understanding
projection and both permission smoke dialects. Its actual payment probe observed
two charges before the repair and one after. The source archive now contains the
POSIX/PowerShell wrapper twins with only the intended package files.

Hosted run [34706044344](https://github.com/SohamBanerjee853/Scope-Agent/actions/runs/34706044344)
passed all 1,229 tests, package checks and smoke wrappers on macOS. Windows found
a host-dependent absolute-path check in the installer, Unix-only newline
assumptions in tests, oversized pytest IDs exceeding Windows environment limits,
and two isolated smoke failures. The installer now parses both path grammars
independently of the host. Tests preserve literal skill bytes and normalize only
the fake child's readiness-line terminator; long IDs use bounded hashed labels.
The smoke fixture resolves its newly owned temporary directory to avoid Windows
8.3 aliases, and reports only static allowlisted failure stages. Exact permission
wire assertions remain unchanged. Native CI must verify the smoke fix.

After those fixes and Arjun's preparation merge, the full local suite passed
**1,241 tests, 0 skipped**, in 51.94 seconds, and both package archives built.
Hosted run [34706718164](https://github.com/SohamBanerjee853/Scope-Agent/actions/runs/34706718164)
on `1cb66425a7c29f71b09be858850b0298eac397d2` then passed **1,241 tests,
0 skipped** on both Windows 2025 (60.35 seconds) and macOS 15 (35.44 seconds).
Both jobs also passed build, installed-package provenance/resources, both offline
smoke dialects and their native wrapper dry-run. The Windows smoke fix is thus
verified on its native host. This run predates Arjun's final audit and the direct
question cancellation correction; those changes require fresh validation.

The final Arjun audit merge plus watcher-only question fix passed **1,246 tests,
0 skipped**, locally in 53.85 seconds. Both package archives built. The focused
UI suite passed 70 tests, including real tagged watcher answers, shared revoke
and shutdown rejecting late answers, and explicit missing/noninteractive watcher
errors. The final commit's hosted results will be linked in the merge handoff.

## Independent work while A2 is pending

The inspected Arjun tip was still `84a2e7f`; no A2 production module was changed.

- Permission hook/skill installation now refuses existing symlink or Windows
  reparse-point directories from the selected project/CODEX_HOME through the
  destination, in dry-run and write paths. A disposable reproduction previously
  wrote through `.agents` into an outside directory; it now refuses without an
  outside write. Paths above the selected root remain outside this check.
- The installed payment checker exercises the actual packaged CLI, fresh demo
  preparation, real two-charge/one-charge probes, the exact stable-key repair,
  and packaged regressions (one expected failure plus two passes, then three
  passes). A1 captures the actual source change with only task_start and
  task_checkpoint events. The checker records no prediction, consent or saved
  understanding observation, and runs in both offline CI jobs.
- Fresh-clone, packaged demo and skill instructions use the original virtual
  environment explicitly when working from another project. Both skills remain
  valid. The optional Makefile selects native smoke wrappers; it and the Python
  checkers ship in the source archive.
- The permission skill now contains optional Exa guidance:
  generic public queries through already available host tools, official source
  links, untrusted retrieved text, unchanged permission outcomes and explicit
  shell-hook coverage limits. No Scope search command, SDK, account, paid
  research call or live retrieval integration was added. Tool names were checked
  against [Exa's official reference](https://exa.ai/docs/reference/exa-mcp).

Validation: **1,256 tests passed, 0 skipped**, locally in 51.94 seconds; both
archives built; the final noneditable wheel passed installed resource/permission
smoke and payment checks. Skill frontmatter validation and POSIX `make smoke`
passed; Windows Makefile routing was inspected without claiming a native run.
The installed payment check also passed with injected parent host IDs and Git/
pytest selectors, and refused an editable install. Hosted results are pending
at this checkpoint and will be linked in the handoff. A2/A3-dependent commands,
the full scripted rehearsal, launchers and live human/model checks remain pending.

Hosted run [34707907439](https://github.com/SohamBanerjee853/Scope-Agent/actions/runs/34707907439)
passed macOS. Windows exposed two existing nonregular-file simulations whose
constructed stat records have no native file-attribute value. The installer now
rejects nonregular files before inspecting Windows reparse metadata; regular
reparse files are still refused. No fixture or rejection assertion was weakened.
The full corrected local suite passed **1,256 tests, 0 skipped**, in 54.98 seconds.
