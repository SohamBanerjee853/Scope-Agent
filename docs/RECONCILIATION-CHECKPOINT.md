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

A3 provides start/checkpoint/knowledge, shared revoke, direct tagged/headless IPC
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

Hosted CI is pending at this commit. Its completed run evidence will be reported
with the final merge; this local result does not claim native Windows execution.
