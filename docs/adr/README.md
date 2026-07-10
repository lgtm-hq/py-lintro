# Architecture Decision Records

This directory holds Lintro's Architecture Decision Records (ADRs): short, numbered
documents that capture a single significant decision, the context that prompted it, and
the consequences that follow.

## Why ADRs

`docs/architecture/ARCHITECTURE.md` and `docs/architecture/VISION.md` describe the
system as it is and the principles behind it, but they do not isolate individual
decisions as discrete, searchable records. ADRs fill that gap: each one answers "why was
this decided, and when might it be revisited?" for a single choice.

ADRs are intentionally lightweight and complement, rather than replace, the longer-form
architecture docs. Where a decision needed an extended evaluation of alternatives before
it was made, that evaluation lives alongside the codebase (for example, the SARIF
ingestion evaluation) and the resulting decision is distilled here.

## Index

| ADR                                                | Title                                   | Status   |
| -------------------------------------------------- | --------------------------------------- | -------- |
| [0001](0001-native-parser-per-tool.md)             | A dedicated native parser per tool      | Accepted |
| [0002](0002-per-execution-tool-isolation.md)       | Per-execution tool isolation            | Accepted |
| [0003](0003-sarif-partial-adoption.md)             | Partial adoption of SARIF               | Accepted |
| [0004](0004-release-automation-version-pr-flow.md) | Automated release via a version-PR flow | Accepted |
| [0005](0005-uv-and-bun-toolchain.md)               | uv for Python and bun for JavaScript    | Accepted |

## Writing a new ADR

1. Copy [`template.md`](template.md) to `NNNN-short-title.md`, using the next free
   four-digit number.
2. Fill in Status, Context, Decision, Consequences, and References.
3. Only record decisions with evidence in the repository — link the code, config,
   issues, or merged PRs that establish the decision. Do not invent rationale.
4. Add a row to the index table above.
5. When a later ADR overturns an earlier one, set the old ADR's status to `Superseded`
   and link the replacement.

## Status values

- **Proposed** — under discussion, not yet in effect.
- **Accepted** — the decision is in effect and reflected in the codebase.
- **Superseded** — replaced by a later ADR (linked from the status).
- **Deprecated** — no longer relevant, but kept for historical context.
