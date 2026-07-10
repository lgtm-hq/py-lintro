# Stylelint Tool Analysis

## Overview

[Stylelint](https://stylelint.io/) is a mighty, configurable linter for CSS, SCSS, Sass,
and Less stylesheets. It ships 100+ built-in rules, understands modern CSS syntax, and
can auto-fix many violations via `--fix`. This analysis compares Lintro's wrapper with
the upstream `stylelint` CLI behavior.

## Core Tool Capabilities

- Rule enforcement for CSS/SCSS/Sass/Less (invalid values, duplicate properties, empty
  blocks, hex-color length, and 100+ more)
- Auto-fix for many rules via `--fix`
- Config discovery: `.stylelintrc`, `.stylelintrc.{json,yaml,yml,js,cjs,mjs}`,
  `stylelint.config.{js,cjs,mjs}`, or a `stylelint` key in `package.json`
- Ignore support via `.stylelintignore`
- Machine-readable output via `--formatter json`
- Standard CLI options: `--config`, `--fix`, `--formatter`, glob file patterns

## Lintro Implementation Analysis

### ✅ Preserved Features

- Standard linting via `stylelint --formatter json`
- Auto-fix path via `stylelint --fix` (both `check` and `fix` supported)
- Native config discovery respected (per-file, walking upward) — delegated to stylelint
  itself; `.stylelintignore` honored by the underlying tool
- File targeting for `*.css`, `*.scss`, `*.sass`, `*.less`
- Explicit config override via `stylelint:config=<path>` (`--config`)
- Timeout control (default 30s) via `stylelint:timeout`
- Documentation links for each rule (`https://stylelint.io/user-guide/rules/<rule>`)

### ⚠️ Limited / Missing

- **Config is required.** Stylelint cannot run without a resolvable configuration. When
  none is found lintro skips the tool as a non-error (with a helpful message) rather
  than surfacing stylelint's hard `ConfigurationError` (exit 78).
- **No per-warning fixability metadata.** Stylelint's JSON formatter does not report
  which individual warnings are auto-fixable, so `fixable` defaults to `False`. The
  fixed/remaining split is instead derived empirically (re-check after `--fix`).
- No pass-through of advanced CLI flags (e.g. custom syntax, cache, `--report-*`) beyond
  `--config`.
- No custom formatter selection; the JSON formatter is used internally for parsing.

### 🚀 Enhancements

- Graceful skip when no config is present, keeping mixed-language runs clean
- Unified `ToolResult` with normalized issues from `stylelint_parser`
- Fix path preserves the lintro count invariant (`initial = fixed + remaining`) by
  re-checking after `--fix`
- Syntax failures (`CssSyntaxError`) are surfaced as issues rather than swallowed
- Safe version check with a skip result when stylelint is missing or below the required
  version

## Usage Comparison

```bash
# Core stylelint
npx stylelint "**/*.css" --formatter json
npx stylelint "**/*.scss" --fix

# Lintro wrapper
lintro check styles/ --tools stylelint
lintro format styles/ --tools stylelint
lintro check styles/ --tools stylelint --tool-options stylelint:config=.stylelintrc.json
lintro check styles/ --tools stylelint --tool-options stylelint:timeout=60
```

## Output Format

Stylelint writes the JSON payload to stderr (which lintro combines with stdout before
parsing). The array carries one object per source file:

```json
[
  {
    "source": "/path/to/file.css",
    "deprecations": [],
    "invalidOptionWarnings": [],
    "parseErrors": [],
    "errored": true,
    "warnings": [
      {
        "line": 2,
        "column": 10,
        "endLine": 2,
        "endColumn": 17,
        "rule": "color-hex-length",
        "severity": "error",
        "text": "Expected \"#FFFFFF\" to be \"#FFF\" (color-hex-length)"
      }
    ]
  }
]
```

The parser extracts `warnings` (rule, severity, line/column, message) plus any
`parseErrors`, and maps each into a `StylelintIssue`.

## Parser Choice (SARIF vs. native)

Per the SARIF ingestion fidelity checklist
(`docs/design/sarif-ingestion-evaluation.md`), the shared SARIF parser is only used when
it is lossless for a tool. **Stylelint does not emit SARIF natively** — it ships a
`json` formatter, not a `sarif` formatter. A native JSON parser
(`lintro/parsers/stylelint/stylelint_parser.py`) is therefore used. It preserves the
rule name, severity, precise location, and message directly from stylelint's own JSON
schema, which is the highest-fidelity source available for this tool.

## Configuration Strategy

- Prefers native configs (`.stylelintrc*`, `stylelint.config.*`, or a `stylelint` key in
  `package.json`), discovered by stylelint per file
- Honors `.stylelintignore` from upstream
- Explicit override via `stylelint:config`
- Skips gracefully when no configuration is resolvable

## Rule Categories & Plugin Ecosystem

- Built-in rules span: possible errors, limit language features, stylistic issues (many
  delegated to formatters), and metadata (e.g. `color-hex-length`, `block-no-empty`,
  `declaration-block-no-duplicate-properties`)
- Rich plugin ecosystem: shareable configs such as `stylelint-config-standard` and
  `stylelint-config-standard-scss`, and plugins like `stylelint-order` and
  `stylelint-scss`. These are activated through the user's stylelint config; lintro does
  not manage them.

## Installation

```bash
# Recommended (repo convention)
bun add -g stylelint

# Or per-project
bun add -d stylelint stylelint-config-standard
```

## Priority and Conflicts

- **Priority:** 50 (default linter priority)
- **Conflicts:** none declared. Prettier also touches `*.css`/`*.scss`/`*.less` for
  formatting; stylelint focuses on lint rules, so they are complementary.
