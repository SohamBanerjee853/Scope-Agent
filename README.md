# Scope

Only the F0 foundation exists: a Python package, local paths and event logs, and
lazy CLI routing. Permission scopes, understanding checks, receipts, demos, and
host launchers are not implemented yet. Reserved commands fail explicitly.

Scope is intended to support two independent workflows:

- A person reviews a bounded permission card. Matching requests can reuse that
  approval until its budget or lifetime expires; dangerous requests still require
  native review.
- A person predicts a concrete program result, separately consents to a probe,
  inspects its observation, and chooses a manageable next task.

A prediction never grants permission or proves general mastery. The planned
watcher transports human decisions; probes execute in the calling agent's
environment. Scope supplements the coding tool's sandbox and approval layers.
The design uses the user's existing signed-in coding agent, without an additional
model SDK, API key, database, or web service.

The planned `scope codex` and `scope claude` commands will put the coding agent on
the left and Scope's review pane on the right in one terminal, followed by a
receipt on exit. They are reserved commands today. Automatic panes are planned
for tmux on macOS/Linux/WSL; native Windows will have an explicit manual fallback.
Neither startup nor Windows behavior has been verified in F0.

## Develop the foundation

With Python 3.11+ and uv available, these commands work in POSIX shells and
PowerShell:

```sh
git clone https://github.com/SohamBanerjee853/Scope-Agent.git
cd Scope-Agent
uv sync --locked
uv run scope --help
uv run pytest -q
uv build
```

Runtime dependencies are Rich and PyYAML; pytest is a development dependency.
Resources beneath `src/scope` are included in wheels. F0 includes clearly marked
resource placeholders; feature owners will add actual rules, skills, and demos.

`SCOPE_HOME` defaults to `~/.scope`; `CODEX_HOME` defaults to `~/.codex`. Looking up
a path creates nothing. Appending an event creates its session directory as needed.
All tests isolate these locations and `CLAUDE_CONFIG_DIR` in temporary directories.

Read [the frozen interfaces](docs/INTERFACES.md), [contributor instructions](AGENTS.md),
and [the baseline hook reference](docs/codex-hook-reference.md) before feature work.

Developed with AI coding assistance. Offline tests use isolated fixtures;
they do not establish live model behavior.

F0 ends at the shared main checkpoint. Soham's next milestone is S1 on
`work/soham-permissions`; Arjun's is A1 in a separate checkout on
`work/arjun-understanding`. Neither feature branch is created by F0.

See [the F0 checkpoint](docs/F0-CHECKPOINT.md) for actual validation and limits.
