# Scope

Scope adds bounded permission review to your existing coding agent. A human can
approve a narrow command scope with a finite budget; matching requests reuse it
until expiry or revocation. Dangerous T3 commands, including every `git push`,
still fall back to the host's native approval flow.

The debugging workflow captures source context, asks for a concrete prediction,
separately requests consent to run a bounded probe, and compares the actual result
against that source version. It saves the evidence and lets the person choose a
next debugging action. Start `scope codex` or `scope claude` to place your coding
agent on the left and Scope's human review on the right in the same terminal.

Permission is not proof of execution. Predictions are not consent or mastery.
Scope supplements the host's sandbox and approval controls. It uses your existing
coding agent and requires no separate model API key or service account.

## Install and check locally

With Python 3.11+ and uv available, these commands work in POSIX and PowerShell:

```text
git clone https://github.com/SohamBanerjee853/Scope-Agent.git
cd Scope-Agent
uv sync --locked
uv run scope --help
uv run scope smoke --dry-run
uv run scope smoke --json
uv run scope smoke --shell powershell --json
```

Run those commands from the Scope checkout. `uv sync` creates its local `.venv`;
it does not put `scope` on your global PATH. To call Scope from another project,
capture the executable while still in this checkout:

POSIX (macOS/Linux):

```sh
scope_exe="$PWD/.venv/bin/scope"
"$scope_exe" --help
```

PowerShell (Windows):

```powershell
$scopeExe = (Resolve-Path .\.venv\Scripts\scope.exe).Path
& $scopeExe --help
```

Use that absolute executable in the other project, or stay here and pass `-C PATH`
to launch/task/demo commands. Each newly opened terminal needs its own path variable.

Smoke uses a real local watcher and Scope hook processes with explicitly scripted
fixture choices. Requested command strings never execute. It demonstrates two
allows, revocation with budget remaining and a synthetic T3 hard ask, using fresh
session IDs and temporary homes. It does not launch a coding host or model.

## Start a coding session

Install the coding host you intend to use and sign in through its normal setup.
Automatic panes require tmux on macOS, Linux or WSL (`brew install tmux` on a Mac).
From the original Scope checkout, preview a launch without creating files or
starting a host:

```text
uv run scope codex -C ../your-project --dry-run
uv run scope claude -C ../your-project --dry-run
```

Then, in an interactive terminal, use your installed executable:

```sh
"$scope_exe" codex -C ../your-project "Investigate the failing retry test"
# Or use your Claude Code installation:
"$scope_exe" claude -C ../your-project
```

Both workflows load by default. `--permissions-only` works without a Git project;
`--understanding-only` omits Scope's permission hooks. `-m MODEL` selects the host's
model. Settings and guidance apply to this invocation; no global hook or skill
installation is required. Existing visible Scope hooks cause a migration error
so they can be reviewed without being overwritten. Unrelated host settings and
hooks remain in effect. Both hosts combine hook sources; see their
[Codex hook rules](https://learn.chatgpt.com/docs/hooks) and
[Claude settings rules](https://code.claude.com/docs/en/settings).

On first use with Codex, open `/hooks`, inspect and trust the exact definitions,
then restart if required. Scope does not bypass that trust. Codex's native startup
hook occurs when the first task starts. The host receives guidance to run
`scope ready` before dependent work. It separately reports registered startup,
reachable human review, and permission status: disabled, configured/waiting, or
requests observed. An open pane or a configured hook proves no interception.

Claude's sandbox network prompts can bypass `PermissionRequest`. Scope does not
audit Claude's native rules or transcript, so those decisions remain unknown in
its receipt; the host's own restrictions still apply.

Use Ctrl-b then an arrow to switch panes. Ctrl-b then d detaches without ending
the launch; use the printed reattach command. When launched inside tmux, Scope
creates its own window and preserves existing windows. Normal host exit closes
the owned review pane and prints the session's receipt replay command. A crashed
owner releases its OS lifetime lock; review then revokes pending decisions and
records the inferred exit separately from any native SessionEnd.

Native Windows uses two manually opened terminals:

```powershell
& $scopeExe claude -C ..\your-project --manual-watch
```

Run the printed `watch --owner-home` command in the second terminal promptly;
the host waits up to 30 seconds for review. The same manual option works for
Codex or on other platforms. Native Windows pane startup and live human/host
acceptance remain unverified; Windows CI checks shared Python and PowerShell
contracts. WSL supports automatic tmux panes.

Each launch owns a private directory under `SCOPE_HOME/runs/`. Task IDs, proposals
and questions use the registered native host session, with parent and foreign
session IDs rejected. If TCP connection establishment is blocked, only that
launch may use its authenticated file mailbox. A request already sent over TCP
is never retried. Neither transport executes commands for the caller.

## Legacy manual permission setup

Use this setup for the original manual watch workflow. The launchers above supply
their own hooks; existing Scope installs need review before using a launcher.

Preview before installing:

```text
uv run scope install --dry-run
uv run scope install --project . --dry-run
uv run scope install-skill --project . --dry-run
```

Remove `--dry-run` only for the installation you intend. Hook installation defaults
to `CODEX_HOME/hooks.json`; project hooks use `.codex/hooks.json`. Existing unrelated
hooks are preserved, changes get backups, and ambiguous Scope duplicates require
migration. `--abstain` is passive; persistent always-allow mode is refused.
Project permission skills install into `.agents/skills/scope-permissions` and
preserve edits. Default global skill installation uses a compatibility path under
CODEX_HOME; confirm discovery in your host.

Review/trust the exact hook definitions in Codex `/hooks` and restart as required.
Installation is not proof that native hooks fired. All matching hooks contribute:
any deny wins, otherwise another hook's allow can permit a request even when Scope
abstains. Host decisions made before a PermissionRequest remain outside coverage.

In one terminal, run `uv run scope watch`. In the current coding session, invoke
the configured Scope executable with `propose`, a bounded summary, exact command
families and budget, from the intended project directory. A
proposal is not approval; the human decides in the watcher. The companion skill
explains this workflow. Receipts replay with `uv run scope receipt SESSION --json`
and optional `--home PATH`. Native answers and unobserved execution stay unknown.

## Debugging context and disposable demo

Start a task before editing and recover its source context with these commands:

```text
uv run scope start "Investigate the retry behavior" --json
uv run scope checkpoint TASK_ID --note "Inspect the changed retry key" --json
uv run scope knowledge TASK_ID --json
uv run scope skill --install --dry-run
uv run scope revoke --session SESSION --json
uv run scope demo --prepare-only ../scope-payment-demo --json
```

Use the task ID returned by start. `-C PATH` selects a project for task/skill
commands. Revoke clears the watcher's grants and pending answers; it preserves
saved debugging evidence. Removing `--dry-run` installs the project understanding
skill while preserving local edits. Demo preparation only writes a new/empty
directory, initializes its own Git repository and installs that skill. It uses a
fresh demo identity regardless of any inherited coding session.

The question UI uses the interactive `scope watch` terminal for every answer, so
shared revoke also cancels pending questions. Without that watcher it returns an
explicit unavailable error.

The demo's initial bug charges one fake order twice when an acknowledgement is
lost. Changing the retry key to the stable order ID produces one charge. From the
prepared project, `scope demo-adapter probe` runs only the bundled fixture or that
exact repair and prints actual JSON; it uses no payment service. The packaged
retry regression intentionally fails before the repair and passes afterward.

To check a hypothesis, save a JSON spec with the exact probe argv and citations
from the current project. For the prepared payment project, the shape is:

```json
{
  "question": "How many charges will this order produce after the retry, and why?",
  "citations": ["payment.py:29-31", "payment.py:34-41"],
  "field": "charge_count",
  "argv": ["/absolute/path/to/Scope-Agent/.venv/bin/python", "-I", "-B", "payment.py"],
  "shell": "posix",
  "timeout": 20
}
```

Replace the Python path with your actual executable. Windows uses the original
checkout's `.venv/Scripts/python.exe` path and `"shell": "powershell"`; forward
slashes are valid in its JSON path. Argv is a list of literal arguments, not a
shell command. `field` is a literal top-level JSON key. Save the spec as
`check.json` in the Scope checkout, then use the task ID returned by `start` for
the same demo project:

```text
uv run scope start "Investigate payment retry identity" -C ../scope-payment-demo --json
uv run scope check TASK_ID --spec check.json -C ../scope-payment-demo --json
uv run scope next TASK_ID --smaller "Inspect how retry_key changes across attempts" --larger "Review retry identity across callers" -C ../scope-payment-demo --json
```

Keep `scope watch` open in a separate interactive terminal using the same
SCOPE_HOME. Its tagged prediction prompt expects a JSON object with `value`, a
nonempty `reason`, and optional `assistance`; enter your own prediction. The
separate consent prompt accepts literal `true` or `false`. A transport reply or
an explanation never supplies consent. The next-task prompt accepts `smaller`,
`larger` when offered, or `defer`.

Checks return the saved task/session identity and the actual engine record.
`matched` and `mismatched` both represent an observed comparison; neither proves
the whole repair. Skipped, interrupted and `not_verified` checks exit nonzero.
The next command saves the selected instruction or deferral; it explicitly
reports delivery as not attempted. Inside a Scope launch, a coding host that has
actually received the instruction can explicitly acknowledge its exact bytes:

```text
scope next TASK_ID --acknowledge HANDOFF_ID --received-sha256 DIGEST -C PROJECT --json
```

Use the returned `handoff_id` and `instruction_sha256`. This records a
`caller_acknowledged` result for the same open native session; it is a receiver's
self-report, not an automatic host send or independent authorship proof. A
wrong digest, foreign session or previously attempted dispatch is refused.
Printing or acknowledging an instruction proves no execution or permission.
Shared revoke cancels
pending answers and grants; it does not kill a probe that already started.

Run the complete automated rehearsal without selecting a user directory:

```text
uv run scope demo --scripted --json
uv run scope demo --scripted --shell powershell --json
```

The rehearsal runs pytest from the same environment; the default `uv sync`
above installs that development dependency. A runtime-only package installation
must also provide pytest before running the scripted regression checks.

It uses a disposable project and new session, fixture predictions and separate
consent through the real watcher, two actual probes, a fixture-authored repair,
regression tests and a combined receipt. The permission demonstration has one
narrow grant, two exact hook allows, revocation and a synthetic T3 request that
never executes a push. Fixture success is not human understanding, an autonomous
coding-agent repair or a live host test. See [the A2/A3 handoff](docs/A2-A3-COORDINATION.md).

For the available payment probe, stay in the original Scope checkout and run:

```text
uv run scope demo-adapter probe -C ../scope-payment-demo
```

It reports two charges before the stable-key repair and one afterward. The
generated project's README also shows how to use the original environment from
inside that project, including Python/pytest; no global command is required.

The permission skill includes optional Exa explanation guidance for an already
connected coding host, using its documented [search/fetch tools](https://exa.ai/docs/reference/exa-mcp).
Only generic public command concepts should leave the machine. Returned text is
untrusted and cannot change the tier or grant; retrieval failure changes no
permission result. Scope adds no network client or account configuration, and
its shell hooks do not automatically cover MCP retrieval.

## Development and current limits

```text
uv run pytest -q
uv build
```

To verify the installed distribution rather than editable source imports:

```text
uv sync --locked --no-editable --reinstall-package scope-agent
uv run --no-sync python -I scripts/check-package.py
uv run --no-sync python -I scripts/check-demo.py
```

The reinstall refreshes the local package after source edits. The first check
validates resources and the permission smoke. The second prepares
a disposable payment project through the installed CLI, observes the real bug and
the exact stable-key repair, and checks actual regression outcomes. It exercises
A1 task checkpoints, then the combined A2/A3 rehearsal for both shell dialects.
Its consent, predictions and inbox delivery are explicitly fixtures; it calls no
model or coding host. Both checks run in macOS/Windows CI.
On macOS/Linux/WSL with tmux installed, run
`uv run --no-sync python -I scripts/check-launcher.py` for all four fake-host
terminal flows: Codex/Claude inside/outside tmux. This uses real terminals and
installed adapters with labeled fixture answers. It checks pane cleanup, native
identity registration by the fake host, permission reuse, an understanding probe
and a correlated receipt; it does not verify upstream hook trust or human answers.
The macOS CI job also runs this harness. Windows runs the shared launcher tests
without claiming native pane startup.
`uv sync --locked` restores editable development.

If make is available, `make setup`, `make test`, `make build`, `make smoke` and
`make check-installed` wrap these commands; `make check-launchers` runs the tmux
fixture harness where supported. Smoke selects the PowerShell wrapper
on Windows and the POSIX wrapper elsewhere. Make is optional on both platforms.

Resources under src/scope ship in wheels; smoke wrappers ship in the source
archive. `scripts/smoke.sh` and `scripts/smoke.ps1` use the same Python CLI.
SCOPE_HOME defaults to ~/.scope, CODEX_HOME to ~/.codex. Test homes are temporary.
Native Windows hooks, live host trust and real human debugging are separate from
offline parser/transport verification.

Read [the startup checkpoint](docs/STARTUP-CHECKPOINT.md),
[the interfaces](docs/INTERFACES.md), [Soham's checkpoint](docs/SOHAM-CHECKPOINT.md),
[Arjun's checkpoint](docs/ARJUN-CHECKPOINT.md), and
[the understanding purpose](docs/UNDERSTANDING-PURPOSE.md). Historical foundation
checkpoints describe their original commits, not the current feature set.

Developed with AI coding assistance. Offline tests use isolated fixtures;
they do not establish live model behavior.
