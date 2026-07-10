# ADR-0001: A dedicated native parser per tool

## Status

Accepted

## Context

Every tool Lintro wraps emits diagnostics in its own format: JSON Lines, JSON arrays, or
free-form regex-parseable text. Lintro needs to convert these varied outputs into a
single `BaseIssue` model so the formatter layer can render any tool's results
consistently.

Two broad approaches exist:

- A generic, configuration-driven parser factory that maps output formats to a small
  number of parser types.
- A dedicated parser module per tool, each owning that tool's quirks.

The generic factory is documented in `docs/architecture/ARCHITECTURE.md` as a _future_
direction ("Generic parser factory to reduce duplication"), not the current state. The
code that ships today follows the per-tool approach: the `lintro/parsers/` tree contains
a directory per tool (`ruff/`, `black/`, and so on), each pairing a `*_parser.py` with a
`*_issue.py` dataclass.

## Decision

Each tool has its own parser under `lintro/parsers/<tool>/`, exposing a tool-specific
`*_issue.py` dataclass that subclasses `BaseIssue` and a `*_parser.py` that converts raw
tool output into those issue objects.

## Consequences

- Each tool's output idiosyncrasies stay isolated in one place, so a change in one
  tool's format cannot break another tool's parsing.
- Parsers are individually testable; `tests/unit/parsers/` mirrors this layout with one
  test module per parser.
- The cost is duplication: the repository carries many similar parser implementations.
  `ARCHITECTURE.md` records a generic parser factory as the intended future
  consolidation, at which point this ADR should be revisited.

## References

- `docs/architecture/ARCHITECTURE.md` — "Parser System" and "Generic Parser Factory"
  sections.
- `lintro/parsers/` — per-tool parser and issue modules.
- `tests/unit/parsers/` — matching per-parser test modules.
