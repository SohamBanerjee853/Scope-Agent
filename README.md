# Scope

Scope adds bounded permission review to your existing coding agent. A human can
approve a narrow command scope with a finite budget; matching requests reuse it
until expiry or revocation. Dangerous T3 commands, including every `git push`,
still fall back to the host's native approval flow.

The repository also contains Arjun's source snapshots, persistent task context,
bounded probe runner and historical understanding projection. Soham is adding
A3's CLI, skill and disposable demo while Arjun develops A2's prediction and
separate execution-consent workflow. See the owner and integration checkpoints
under docs for the exact implemented state. The `scope codex` and `scope claude`
two-pane launchers are planned and remain unavailable.

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

Smoke uses a real local watcher and Scope hook processes with explicitly scripted
fixture choices. Requested command strings never execute. It demonstrates two
allows, revocation with budget remaining and a synthetic T3 hard ask, using fresh
session IDs and temporary homes. It does not launch a coding host or model.

## Optional manual permission setup

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

In one terminal, run `uv run scope watch`. In the current coding session, use
`scope propose` with a bounded summary, exact command families and budget. A
proposal is not approval; the human decides in the watcher. The companion skill
explains this workflow. Receipts replay with `uv run scope receipt SESSION --json`
and optional `--home PATH`. Native answers and unobserved execution stay unknown.

## Debugging context and disposable demo

These A3 commands use Arjun's implemented A1 APIs:

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

`scope check`, `scope next`, and `scope demo --scripted` currently report an
explicit pending A2 dependency. They do not invent predictions or execute a
substitute consent workflow. Arjun's published branch has not supplied those
callable signatures yet. [A2/A3 coordination](docs/A2-A3-COORDINATION.md) records
what can be wired when he publishes them.

## Development and current limits

```text
uv run pytest -q
uv build
```

Resources under src/scope ship in wheels; smoke wrappers ship in the source
archive. `scripts/smoke.sh` and `scripts/smoke.ps1` use the same Python CLI.
SCOPE_HOME defaults to ~/.scope, CODEX_HOME to ~/.codex. Test homes are temporary.
Native Windows hooks, live host trust and real human debugging are separate from
offline parser/transport verification.

Read [the interfaces](docs/INTERFACES.md), [Soham's checkpoint](docs/SOHAM-CHECKPOINT.md),
[Arjun's checkpoint](docs/ARJUN-CHECKPOINT.md), and
[the understanding purpose](docs/UNDERSTANDING-PURPOSE.md). Historical foundation
checkpoints describe their original commits, not the current feature set.

Developed with AI coding assistance. Offline tests use isolated fixtures;
they do not establish live model behavior.
