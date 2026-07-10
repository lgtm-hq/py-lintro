# ADR-0002: Per-execution tool isolation

## Status

Accepted

## Context

Lintro executes tools concurrently and chains commands (for example `format, check`).
Tool plugins are long-lived objects that carry mutable option state. When the
orchestrator mutated a shared plugin instance's options while preparing an execution,
that mutation was not safe under parallel or threaded execution: concurrent runs could
observe or clobber one another's options.

This surfaced as issue #1080 ("tool option mutation is not safe under parallel/thread
execution") and the follow-up work that routes post-check tools through isolated copies
rather than mutating shared state.

## Decision

Each tool execution operates on an isolated, per-execution copy of the tool's
configuration and options rather than mutating a shared plugin instance. Option state
that varies per run is scoped to that run.

## Consequences

- Parallel and chained executions no longer race on shared mutable option state, which
  removes a class of order-dependent, hard-to-reproduce bugs.
- The orchestration layer must construct and pass per-execution copies, adding a small
  amount of setup per run in exchange for correctness under concurrency.
- This decision underpins Lintro's parallel execution model; any future change to how
  options are threaded through execution must preserve the isolation guarantee.

## References

- Issue #1080 — "tool option mutation is not safe under parallel/thread execution".
- Commit routing post-check tools through per-execution isolated copies.
- `lintro/utils/async_tool_executor.py`, `lintro/utils/execution/tool_configuration.py`,
  `lintro/plugins/base.py`.
