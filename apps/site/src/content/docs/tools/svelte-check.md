---
title: 'svelte-check'
description: ''
category: tools
order: 170
navGroup: frameworks
---

# Svelte Check Tool Analysis

## Overview

Svelte Check is the official type checker and linter for Svelte components. It provides
TypeScript diagnostics, unused CSS detection, and accessibility hints for `.svelte`
files. This analysis compares Lintro's wrapper with core svelte-check behavior.

## Core Tool Capabilities

- TypeScript type checking for `.svelte` files
- Unused CSS selector detection
- Accessibility (a11y) hints
- Config discovery: `svelte.config.js`, `svelte.config.ts`, `svelte.config.mjs`
- Integration with project's `tsconfig.json`
- Watch mode for development (`--watch`)
- Machine-readable output formats (`--output machine`, `--output machine-verbose`)
- Threshold filtering (`--threshold error|warning|hint`)

## Lintro Implementation Analysis

### Preserved Features

- Invokes `svelte-check` with `--output machine-verbose` for structured parsing
- Respects native `svelte.config` and `tsconfig.json`
- Intelligent command fallback: direct `svelte-check` -> `bunx svelte-check` ->
  `npx svelte-check`
- Parses svelte-check output into structured `ToolResult` with file/line/column/severity
- Threshold filtering (`--threshold`) exposed via `--tool-options`
- Custom tsconfig path support
- Auto-install support for Node.js dependencies

### Limited / Missing

**Watch Mode:**

- No `--watch` mode (continuous type checking)

**Output Formats:**

- Only machine-verbose format is used (human-readable not exposed)

**Advanced Features:**

- No `--compiler-warnings` flag for controlling compiler warning behavior
- No `--diagnostic-sources` filtering (js, svelte, css)
- No workspace support (`--workspace`)

### Enhancements

- Safe timeout handling (default 120s) with structured timeout result
- Auto config discovery for svelte.config files
- Normalized `ToolResult` with parsed issues from `svelte_check_parser`
- Priority 83, tool type `LINTER | TYPE_CHECKER`
- Graceful handling when svelte-check is not installed with helpful install hints
- Dependency error detection with helpful suggestions
- Auto-install support for Node.js dependencies

## Usage Comparison

```bash
# Core svelte-check
svelte-check

# Core svelte-check with threshold
svelte-check --threshold warning

# Core svelte-check with specific tsconfig
svelte-check --tsconfig ./tsconfig.app.json

# Lintro wrapper - check Svelte files
lintro check src/ --tools svelte-check

# Lintro wrapper - with threshold
lintro check . --tools svelte-check --tool-options "svelte-check:threshold=warning"

# Lintro wrapper - with tsconfig
lintro check . --tools svelte-check --tool-options "svelte-check:tsconfig=./tsconfig.app.json"

# Lintro wrapper - auto-install dependencies
lintro check src/ --tools svelte-check --auto-install
```

## Configuration Strategy

- **Config inherited:** All settings from `svelte.config` and `tsconfig.json` are
  preserved
- **No config injection:** Lintro cannot modify svelte.config settings; tool is "Native
  only"
- **Tool options available:**
  - `svelte-check:threshold` (string) - minimum severity to report (error, warning,
    hint)
  - `svelte-check:tsconfig` (string) - path to tsconfig.json
  - `svelte-check:timeout` (int) - execution timeout in seconds (default: 120)
- **Config display:** `lintro config -v` shows detected svelte.config file

## Priority and Conflicts

- **Priority:** 83 (runs after tsc, same priority as astro-check)
- **Tool Type:** LINTER | TYPE_CHECKER
- **Conflicts:** None
- **Complements:** oxlint, oxfmt, prettier (formatting/linting)

## Recommendations

- **Use Lintro** when you want quick type checking integrated into a multi-tool workflow
  with normalized output and timeout safety.
- **Use core svelte-check directly** when you need:
  - Watch mode for development (`svelte-check --watch`)
  - Diagnostic source filtering (`--diagnostic-sources`)
  - Compiler warning control (`--compiler-warnings`)
  - Workspace support
