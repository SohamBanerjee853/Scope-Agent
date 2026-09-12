# What Scope is for

Scope is intended to let you approve a small, bounded family of agent commands
and independently check your understanding of a code change. For example, predict
how many fake charges a retry will create, separately approve a probe, then compare
your prediction with the observed result and choose a manageable next task.

Permission and understanding remain separate. A prediction is not execution
consent; a selected next task is not proof that the task was completed.

Only the shared foundation exists at its initial commit. Owner checkpoints record
subsequent implementation and test evidence. The planned `scope codex` and
`scope claude` experience puts the coding agent on the left and Scope questions on
the right in one terminal; those launchers require later integration.

Scope will use your existing coding agent account. It does not replace the host's
sandbox or approvals and will not execute probes in its human-review watcher.
