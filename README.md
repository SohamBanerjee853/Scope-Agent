# Scope Agent

Scope combines bounded permissions with evidence-based debugging workflows.

Scope has two planned independent workflows: bounded human permission approvals,
and source-grounded predictions compared with approved program observations.
A correct prediction never grants permission or proves general mastery. Scope
uses your existing signed-in coding agent; it needs no separate model API key.

This initial foundation supplies package setup, safe record paths, JSONL events,
and lazy command routing. Feature progress and actual verification are recorded
in owner checkpoints under `docs/`; unavailable commands fail explicitly.

Planned `scope codex` and `scope claude` launchers will put the coding agent and
Scope review in two panes of one terminal. They are not implemented by the
foundation. Existing host approval and sandbox boundaries remain authoritative.

Requires Python 3.11+ and uv. From this checkout:

```sh
uv sync --locked
uv run scope --help
uv run pytest -q
uv build
```

These commands also work in PowerShell. Native Windows behavior must be tested
there before claiming parity. Automatic launcher panes will require tmux on
macOS/Linux/WSL; native Windows will have manual panes.

See [the frozen interfaces](docs/INTERFACES.md) and
[the baseline hook reference](docs/codex-hook-reference.md). Source under
`src/scope/` is packaged recursively so future skill, YAML and demo resources ship
in wheels. No live model sessions, global hooks, or paid services are part of the
offline checks.
