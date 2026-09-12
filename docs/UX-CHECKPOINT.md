# Terminal review UX checkpoint

The September 12, 2026 follow-up replaces the interactive watcher's escaped
transcript and tagged JSON entry with a persistent keyboard interface. It builds
on the integrated launcher/A2/A3 revision 530f0ba and preserves the engine's
prediction, consent, execution and receipt contracts.

## Behavior

- Requests arrive in an inbox. F2 opens the next request; returning to the inbox
  never activates a later approval. Request identifiers stay inside the app.
- Permission review shows the complete command, directory, shell and finite
  proposed scope. Pass to host is the initial choice. Scope editing uses labeled
  fields and the existing card validator before an explicit approval.
- Prediction has a value type, value, reason and optional assistance. F4 changes
  between Number, Text, Boolean, Null and JSON. Inline errors retain the draft.
- Probe consent is a separate form showing exact argument boundaries, directory,
  timeout and classification. Decline is the initial choice. Next-task selection
  shows complete offered instructions and initially focuses Defer.
- F3 expands supplied evidence without losing edited fields. Page Up/Down scrolls
  long commands and evidence; Tab restores visible focus. An action after manual
  scrolling first restores its focus and requires a fresh activation.
- Esc skips; Ctrl-r revokes scopes and pending answers; Ctrl-c exits. Deadlines,
  disconnects, revocation and owner exit cancel tickets and clear drafts and
  queued transition keys. Every callback checks its ticket and view generation.

The single terminal event loop uses prompt_toolkit 3.0.53. It is imported only
by the interactive watcher; automatic hook imports remain free of UI libraries.
`scope watch --plain` retains tagged line input. Redirected input is unavailable
for human decisions; `TERM=dumb` retains the plain interface.

Optional, strictly validated version-1 question presentation supplies display
fields, never answers, provenance or permission defaults. Commands and next-step
instructions must fit intact; oversized requests fail instead of silently hiding
essential content. Source/history details may be explicitly excerpted. The
existing canonical answer strings and A2 validation remain unchanged. Restart
both caller and watcher together when updating: older watchers reject the new
optional request field safely.

## Design guidance

The requested [UI/UX Pro Max skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)
was installed locally and consulted for visible labels, progressive disclosure,
keyboard focus, inline errors and keeping focused controls visible. These are
terminal adaptations; no mobile touch-target or screen-reader certification is
claimed. The app uses the terminal's font, labeled keyboard controls, a restrained
dark palette and a persistent shortcut footer.

## Validation

The source was installed noneditable with a forced package refresh and compared
byte-for-byte against all 47 package source/resource files. The wheel and source
distribution built successfully. Installed package/resource checks, both shell
dialect permission smokes and the bundled payment/A2/A3 rehearsal passed. The
POSIX smoke wrapper's dry run also passed with the local uv directory on PATH.

The actual tmux/PTY harness passed five cases: Codex and Claude inside and outside
tmux at a 69-by-39 review viewport, plus Codex outside at 45-by-24. Each operated
the visible permission, cancelled prediction, fresh prediction and separate
consent forms. Late `99` plus Enter in the inbox could not answer the new request.
Both inside cases used pane indexes 11/12 and preserved an unrelated window.

Across those five cases the receipts contained 15 permission requests, 10 allows,
five T3 abstentions, five finite grants, five predictions, five real local probe
executions, five observations and five honestly skipped checks. Every fake host
exited zero and owned panes, endpoints and environment records were cleaned up.
ANSI/plain captures and receipts were saved locally outside the public tree and
the actual normal/narrow screens were visually inspected.

`UV_NO_EDITABLE=1 uv run pytest -q` passed **1,817 tests, zero skipped**, in
233.34 seconds on macOS 14.8.4 / Python 3.11.16. This includes 25 focused terminal
tests with actual pipe key events, typed-form validation and watcher integration.
The commit is gated on the repository's macOS and Windows CI jobs before main;
their completed result is reported with the merge rather than predicted here.

## Limits

All rehearsal answers are labeled `test_fixture`. The real bundled local probes
ran, but no live Codex/Claude model session, native hook trust or real human
acceptance was exercised. Shared Windows tests do not establish native Windows
manual-pane UX. Screen-reader behavior and terminal themes beyond the captured
terminal remain unverified. Literal future keystrokes have no inherent request
identity; the inbox transition and ticket checks prevent old queued keys,
callbacks and cancelled drafts from submitting a later form.
