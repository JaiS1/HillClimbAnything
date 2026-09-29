# Policy-development agent instructions

Use these instructions in your trusted coding-agent wrapper for the dream phase.
The wrapper receives a JSON request on stdin and returns the response specified
by the CLI's developer protocol. It must not mix conversational text with JSON.

You are improving an executable exploration policy. Improve how the discovery
budget is allocated; do not solve the underlying project, modify its evaluator,
or change the harness. Read the incumbent policy and the supplied replay feedback.
Use both successful and unsuccessful revisions to propose a new complete policy.

The executing policy receives only revealed observations. It may open a new root
branch, continue an observed leaf, or stop. It must derive decisions from those
observations and the supplied budget. Reconstruct branch trajectories: improvement,
stagnation, regressions, invalid attempts and possible recovery. A failed attempt
does not by itself establish that the whole branch is worthless.

Your policy must use only its JSON input. Do not read archives or other files,
inspect the environment, access the network, identify particular historical runs,
hard-code node IDs or stored scores, or alter any files. Return deterministic
actions for identical observations. Do not leave child processes running.

Seek a general decision rule rather than memorizing the replay pool. Missing
recorded continuations are unsupported, not observed failures. Replay feedback
is conditional on the recorded history. A higher replay utility is not evidence
of better performance on a fresh project.

Return a complete Python policy source and a short explanation of the mechanism
changed, the replay evidence motivating it, and the remaining uncertainty. Keep
the source small and self-contained with Python standard-library dependencies.
The harness evaluates it, preserves the result, and decides whether to promote
it. Do not assert that a proposed revision has passed before receiving its result.

These are behavioural instructions for a trusted agent, not an OS security
sandbox. Provider authentication, spending limits and cancellation belong to the
wrapper. The repository promises no particular provider integration.
