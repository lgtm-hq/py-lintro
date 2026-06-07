---
title: 'markdownlint'
description: ''
category: tools
order: 90
navGroup: config
---

# Markdownlint-cli2 Tool Analysis

## Overview

Markdownlint-cli2 checks Markdown files for style and formatting issues. This analysis
compares Lintro's wrapper with the upstream `markdownlint-cli2` behavior.

## Core Tool Capabilities

- Rule enforcement for common Markdown style issues (e.g., headings, trailing spaces,
  MD013 line length)
- Config discovery: `.markdownlint.*` (`json`, `jsonc`, `yaml`, `yml`) and
  `.markdownlint-cli2.jsonc`
- Ignore support via `.markdownlintignore`
- Standard CLI options: `--config`, `--ignore`, `--fix` (not supported by cli2), glob
  patterns for files

## Lintro Implementation Analysis

### ✅ Preserved Features

- Standard linting via `markdownlint-cli2`
- Native config discovery respected; `.markdownlintignore` honored by the underlying
  tool
- File targeting for Markdown patterns (`*.md`, `*.markdown`)
- Timeout control (default 30s) via `markdownlint:timeout`
- Line-length handling through central `enforce.line_length` or tool option
  `markdownlint:line_length`

### ⚠️ Limited / Missing

- No auto-fix support (cli2 is lint-only)
- No pass-through of advanced CLI flags (e.g., `--ignore-path`, custom rule toggles)
  beyond config files
- No custom formatter selection; relies on default cli2 output
- No parallelization control; runs single process

### 🚀 Enhancements

- Centralized priority (`DEFAULT_TOOL_PRIORITIES["markdownlint"]`, default 30) keeps
  formatters ahead of linters
- Automatic temp config injection for MD013 line length when no native config is present
- Unified `ToolResult` with normalized issues from `markdownlint_parser`
- Safe version check with a skip result when cli2 is missing or below the required
  version

## Usage Comparison

```bash
# Core markdownlint-cli2
npx markdownlint-cli2 \"**/*.md\"

# Lintro wrapper
lintro check docs/ --tools markdownlint
lintro check docs/ --tools markdownlint --tool-options markdownlint:timeout=60
```

## Configuration Strategy

- Prefers native configs: `.markdownlint.(json|jsonc|yaml|yml)` or
  `.markdownlint-cli2.jsonc`
- Honors `.markdownlintignore` from upstream
- Fallback: injects MD013 line length from `.lintro-config.yaml` `enforce.line_length`
  or `markdownlint:line_length`
- Excludes inherited from `--exclude` and tool options; respects `include_venv=False` by
  default

## Priority and Conflicts

- **Priority:** 30 (runs after formatters like Prettier and before heavier linters)
- **Tool Type:** LINTER
- **Conflicts:** None

## Recommendations

- Use Lintro when you want Markdown linting aligned with your central line-length policy
  and unified reporting.
- Use core `markdownlint-cli2` directly if you need custom rule toggles or formatter
  output beyond what configs provide.
