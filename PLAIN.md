# Scope in plain language

Today this repository contains only the shared foundation. It can show command
help and store local event records. It cannot yet approve requests, ask prediction
questions, run probes, launch agents, or produce receipts.

The planned product helps you decide what an existing coding agent may do, and
helps you check your understanding of its changes. These are separate decisions:
answering a prediction question does not approve a command. You can decline a
probe even after answering its question.

The intended entry points are `scope codex` and `scope claude`. They will open the
agent on the left and Scope's questions on the right in one terminal. At the end,
a receipt will distinguish approvals, predictions, observations, and unknowns.
These launch commands do not work yet, and are not upstream CLI flags.

Scope will use your existing coding agent, with no separate model account or API
key. It will preserve the agent's own approval and sandbox protections. A command
being allowed does not prove it ran; a correct prediction does not prove mastery.

See [README.md](README.md) for foundation setup and
[docs/INTERFACES.md](docs/INTERFACES.md) for the planned behavior.
