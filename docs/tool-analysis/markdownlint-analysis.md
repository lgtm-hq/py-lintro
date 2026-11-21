# Markdownlint-cli2 Tool Analysis

## Overview

Markdownlint-cli2 checks Markdown files for style and formatting issues. This analysis
compares Lintro's wrapper with the upstream `markdownlint-cli2` behavior.

## Core Tool Capabilities

- Rule enforcement for common Markdown style issues (e.g., headings, trailing spaces,
  MD013 line length)
- Config discovery: `.markdownlint.*` (`json`, `jsonc`, `yaml`, `yml`) and
  `.markdownlint-cli2.jsonc`
- Ignore globs through native markdownlint-cli2 configuration
- Native CLI options include `--config`, `--fix` and glob patterns. Native `--fix` is
  not invoked by Lintro.

## Lintro Implementation Analysis

### ✅ Preserved Features

- Standard linting via `markdownlint-cli2`
- Native config discovery is respected; use the native cli2 configuration for ignore
  globs.
- File targeting for Markdown patterns (`*.md`, `*.markdown`)
- Timeout control (default 30s) via `markdownlint:timeout`
- Line-length handling through central `enforce.line_length` or tool option
  `markdownlint:line_length`

### ⚠️ Limited / Missing

- No auto-fix support in this Lintro wrapper; fix() reports that Markdownlint cannot fix
  issues.
- No pass-through of advanced CLI flags (e.g., `--ignore-path`, custom rule toggles)
  beyond config files
- No custom formatter selection; relies on default cli2 output
- No parallelization control; runs single process

### 🚀 Enhancements

- Execution order derived from claims (`CHECK` on `*.md`, after prettier) keeps
  formatters ahead of linters
- Automatic temp config injection for MD013 line length when no native config is present
- Unified `ToolResult` with normalized issues from `markdownlint_parser`
- Safe version check with a skip result when cli2 is missing or below the required
  version

## Usage Comparison

```bash
# Core markdownlint-cli2
npx markdownlint-cli2 "**/*.md"

# Lintro wrapper
lintro check docs/ --tools markdownlint
lintro check docs/ --tools markdownlint --tool-options markdownlint:timeout=60
```

## Configuration Strategy

- Prefers native configs: `.markdownlint.(json|jsonc|yaml|yml)` or
  `.markdownlint-cli2.jsonc`
- Configure ignore globs in the native cli2 configuration
- Fallback: injects MD013 line length from `.lintro-config.yaml` `enforce.line_length`
  or `markdownlint:line_length`
- Excludes inherited from `--exclude` and tool options; respects `include_venv=False` by
  default

## Priority and Conflicts

- **Ordering:** Derived from capability claims and tool-ordering configuration.
- **Tool Type:** LINTER
- **Conflicts:** None

## Recommendations

- Use Lintro when you want Markdown linting aligned with your central line-length policy
  and unified reporting.
- Use core `markdownlint-cli2` directly if you need custom rule toggles or formatter
  output beyond what configs provide.
