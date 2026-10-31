# ADR-0012: Packaged Semgrep rules and deterministic AST checks

## Status

Accepted for opt-in Semgrep packs. Proposed for the future native AST tool and
precise-rule gating, which are not implemented here.

## Context

The None-handling audit behind [#3023](https://github.com/lgtm-hq/py-lintro/issues/3023)
found useful syntactic candidates, but its broad scanner had about 3.5% precision. A
candidate count therefore cannot represent a bug count. Lintro already wraps Semgrep,
while logic-heavy checks such as mock-only tests and duplicate test bodies currently
live in `scripts/ci/testing/`.

The per-tool baseline mechanism in #438 and the result collections work in #3018 are not
merged. Patterns cannot yet justify gating on declared `ToolResult` or `BaseIssue`
fields or establish a precise residual-issue invariant.

## Decision

Ship Semgrep YAML under `lintro/rules/<language>/<pack>.yaml`, versioned with the Lintro
release and selected explicitly through `tools.semgrep.rule_packs`.
`lintro list-tools --rules` lists the installed packs. Unknown names are configuration
errors. Packs augment native or explicitly selected Semgrep rules; selecting a pack
replaces the default `auto` registry lookup with local rules.

The first pack, `python/none-coercion`, reports INFO candidates with review questions
about empty versus unknown values. It does not prove a bug. It includes fixtures for all
eight rule families and for both sides of each suppression. No pack is enabled in
Lintro's own default configuration. No candidate-count baseline or ratchet is added.

Choose a future **built-in deterministic AST tool** for logic-shaped checks that need
Python ASTs or cross-file state. It will own the test-hygiene scanners; #1246's external
plugin support can consume the same tool protocol. Migration waits for that tool's
implementation: the existing scanners and their gates stay in place in this change.

Only precise rules should gate Lintro's own code once #438 and #3018 supply the needed
contracts. Separate advisory reporting from gating then; do not turn this pack's
candidate count into a baseline in advance of those prerequisites.

## Consequences

Users can reuse pattern rules without installing another analysis engine. Matcher
fixtures run against Semgrep itself, offline, rather than fabricated findings. Wheel and
sdist data declarations include the rule YAML.

These are syntactic heuristics. JSON provenance covers direct `json.loads(...)` calls
and same-block assignments, including import aliases; it does not establish types, track
arbitrary aliases, or prove that a value has not been reassigned. The capability rule
uses names such as `can_*`, `supports_*`, and `capability`. ALL_CAPS table reads,
environment reads, known subprocess stdout fallbacks, false-returning `is_*`/`has_*`
predicates, and true-returning None guards are suppressed only in their relevant rules.
These choices can miss real bugs and need fixture evidence when revised.

The failed-`ToolResult` rule is an advisory constructor-shape check, not type-aware
gating. Its field-presence exclusions do not validate residual values; `**kwargs` also
prevents proving a field absent. No result model or success semantics change. When a
user explicitly enables the pack under `lintro check`, its findings use the existing
Semgrep reporting and CLI exit behavior, so INFO candidates can produce a nonzero check
exit. Automatic non-gating advisory lanes remain future work.

## References

- [Semgrep plugin](../../lintro/tools/semgrep/definition.py)
- [Pack and resolver](../../lintro/rules/)
- [Matcher fixtures](../../tests/fixtures/rules/none_coercion/)
- [Configuration and usage](../configuration.md#packaged-semgrep-rules)
- [#3023](https://github.com/lgtm-hq/py-lintro/issues/3023),
  [#2996](https://github.com/lgtm-hq/py-lintro/issues/2996),
  [#438](https://github.com/lgtm-hq/py-lintro/issues/438),
  [#3018](https://github.com/lgtm-hq/py-lintro/issues/3018),
  [#1246](https://github.com/lgtm-hq/py-lintro/issues/1246),
  [#1610](https://github.com/lgtm-hq/py-lintro/issues/1610)
