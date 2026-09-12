# Understanding helps the developer get out of debug hell

Arjun clarified this purpose on September 12, 2026: developers can lose track of
their codebase, make changes without a reliable model of what is happening, and
get trapped in repeated debugging attempts. Scope’s understanding workflow should
help them recover that model and make progress toward a working system.

The central question is: **“What is happening in this version of the code, and
what should I try next?”** Learning can follow from that work. A lesson, quiz score,
or generic explanation is not the primary outcome.

## The intended debugging loop

1. Recover context: identify the relevant source, recent changes and previous
   observations, including evidence saved before a process or coding session ended.
2. State a concrete hypothesis: for example, “Retries use a new payment key, so
   the fake service records two charges for one order.” Cite the responsible code.
3. Choose a small probe that can distinguish that hypothesis from alternatives.
   Record the prediction and obtain separate consent before running it.
4. Inspect the real result, including failures and uncertainty. An invalid or
   truncated observation cannot establish that a hypothesis was right or wrong.
5. Make a bounded repair and rerun the relevant probe/regression. Earlier evidence
   becomes stale when its supporting source changes.
6. Hand the current coding agent the next concrete debugging action, or stop when
   the observed behavior and relevant regression checks establish the fix.

The person’s prediction exposes the working assumption behind the next action.
Its purpose is to help diagnose a mismatch, not to grade the person. Permission
approval stays independent of whether that assumption turns out to be correct.

## What A1 establishes

A1 supplies source snapshots/citations, task and checkpoint persistence, a bounded
caller-side runner, stale-evidence handling and a pure receipt projection. The
automated debugging journey uses a disposable, network-free retry bug: it observes
two fake charges, retrieves context in a fresh process, changes the retry key,
observes one charge, passes a regression and marks the old evidence stale.

That exercises debugging primitives. A2 still needs to connect hypothesis,
prediction, consent, comparison and next action. A3 still needs the user-facing
CLI/skill. A1 does not autonomously diagnose arbitrary bugs, perform a human
session, or establish how much debugging time a user saves.

## Acceptance evidence to collect later

Use concrete bug-recovery tasks with recorded initial symptoms, hypotheses,
probes, source changes and final regressions. Determine whether a developer can
recover context and select the next useful action without repeatedly trying the
same unsupported fix. If comparing debugging time or number of attempts, record
the actual method and results. Do not infer improvement from automated fixture
success or a correct prediction alone.
