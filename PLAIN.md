# What Scope does

Scope helps a person stay in control while working with a coding agent.

The permission workflow is implemented: propose a small command scope, review it
in the terminal, and allow matching requests for a limited budget and lifetime.
Revocation stops reuse. Destructive or publishing commands remain native hard asks.
The watcher asks questions and carries decisions; it never runs probe commands.

The understanding workflow is being built around a debugging loop: recover the
relevant source, explain a hypothesis, ask the person to predict a concrete result,
request separate consent for a probe, inspect the real output and choose the next
small repair. Source snapshots, task storage, a bounded runner and receipt
projection exist. Arjun is implementing the prediction/consent engine while Soham
adds the CLI, companion skill and disposable payment example.

A saved prediction is not execution permission. An allow is not proof that a
command ran. A selected next task is not a completed repair, and a correct answer
is not a general mastery score. Missing evidence stays unknown.

The offline permission smoke is available through `uv run scope smoke --json`.
Its fixture decisions and synthetic git-push request are labeled; it executes no
requested commands and demonstrates no human understanding or time savings.

Scope uses the person's current coding agent, without another model API key. It
preserves the host's sandbox, approval settings and normal hook trust. The planned
Codex/Claude launchers with agent and review panes are not implemented yet.
See [README.md](README.md) and owner checkpoints for current commands and limits.
