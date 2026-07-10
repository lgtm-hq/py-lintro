# ADR-0003: Partial adoption of SARIF

## Status

Accepted

## Context

SARIF (Static Analysis Results Interchange Format) is the interchange format that GitHub
code scanning and many analysis platforms consume. Two distinct uses of SARIF are
possible for Lintro:

1. **As an output artifact format** — emitting Lintro's aggregated results as SARIF so
   external systems can ingest them.
2. **As an internal ingestion/parser layer** — reading SARIF that individual tools
   produce and using it as the shared internal representation instead of per-tool
   parsers (see [ADR-0001](0001-native-parser-per-tool.md)).

Lintro already supports the first use: `sarif` is one of the artifact formats in
`lintro/config/execution_config.py` (the `ArtifactFormat` literal), alongside `json`,
`csv`, `markdown`, `html`, and `plain`.

The second use — SARIF as a shared ingestion layer — was evaluated separately
([issue #1066](https://github.com/lgtm-hq/py-lintro/issues/1066), with the design
evaluation in [PR #1140](https://github.com/lgtm-hq/py-lintro/pull/1140)) rather than
adopted wholesale. Not all wrapped tools emit SARIF, and those that do vary in coverage,
so replacing the per-tool parsers with a single SARIF ingestion path is not a clean win.

## Decision

Adopt SARIF partially: Lintro emits SARIF as a selectable output artifact format, but
does not use SARIF as its internal ingestion layer. Per-tool native parsers remain the
source of the internal `BaseIssue` model.

## Consequences

- Users get a standards-based output that GitHub code scanning and similar tools can
  consume, without Lintro taking on SARIF's full complexity internally.
- The internal model stays under Lintro's control via per-tool parsers, keeping
  [ADR-0001](0001-native-parser-per-tool.md) intact.
- SARIF-as-ingestion remains an open evaluation. If a future analysis concludes the
  tradeoffs favour it, this ADR should be superseded.

## References

- `lintro/config/execution_config.py` — `ArtifactFormat` literal including `sarif`.
- [Issue #1066](https://github.com/lgtm-hq/py-lintro/issues/1066) and
  [PR #1140](https://github.com/lgtm-hq/py-lintro/pull/1140) — evaluation of SARIF
  ingestion as a shared parser layer.
- [ADR-0001](0001-native-parser-per-tool.md) — per-tool native parsers.
