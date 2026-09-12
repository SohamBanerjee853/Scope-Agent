# Soham checkpoint: S1

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
