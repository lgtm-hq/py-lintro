---
title: 'astro-check'
description: ''
category: tools
order: 30
navGroup: frameworks
---

# Astro Check Tool Analysis

## Overview

Astro Check is Astro's built-in type checking command that provides TypeScript
diagnostics for `.astro` files including frontmatter scripts, component props, and
template expressions. This analysis compares Lintro's wrapper with core astro check
behavior.

## Core Tool Capabilities

- TypeScript type checking for `.astro` files
- Frontmatter script validation
- Component prop type checking
- Template expression type checking
- Config discovery: `astro.config.mjs`, `astro.config.ts`, `astro.config.js`
- Integration with project's `tsconfig.json`
- Watch mode for development (`--watch`)

## Lintro Implementation Analysis

### Preserved Features

- Invokes `astro check` for type checking
- Respects native `astro.config` and `tsconfig.json`
- Intelligent command fallback: direct `astro` -> `bunx astro` -> `npx astro`
- Parses astro check output into structured `ToolResult` with file/line/column/code
- Auto-install support for Node.js dependencies

### Limited / Missing

**Watch Mode:**

- No `--watch` mode (continuous type checking)

**Build Features:**

- No build mode or output generation
- Type checking only (no production builds)

**Advanced Features:**

- No telemetry control (--telemetry flags)
- No sync mode (--sync flag)
- No experimental features

### Enhancements

- Safe timeout handling (default 120s) with structured timeout result
- Auto config discovery for astro.config files
- Normalized `ToolResult` with parsed issues from `astro_check_parser`
- Priority 83, tool type `LINTER | TYPE_CHECKER`
- Graceful handling when astro is not installed with helpful install hints
- Dependency error detection with helpful suggestions
- Auto-install support for Node.js dependencies

## Usage Comparison

```bash
# Core astro check
astro check

# Core astro check with specific root
astro check --root ./packages/web

# Lintro wrapper - check Astro files
lintro check src/ --tools astro-check

# Lintro wrapper - with specific root
lintro check . --tools astro-check --tool-options "astro-check:root=./packages/web"

# Lintro wrapper - auto-install dependencies
lintro check src/ --tools astro-check --auto-install
```

## Configuration Strategy

- **Config inherited:** All settings from `astro.config` and `tsconfig.json` are
  preserved
- **No config injection:** Lintro cannot modify astro.config settings; tool is "Native
  only"
- **Tool options available:**
  - `astro-check:root` (string) - root directory for the Astro project
  - `astro-check:timeout` (int) - execution timeout in seconds (default: 120)
- **Config display:** `lintro config -v` shows detected astro.config file

## Priority and Conflicts

- **Priority:** 83 (runs after tsc, same priority as svelte-check and vue-tsc)
- **Tool Type:** LINTER | TYPE_CHECKER
- **Conflicts:** None
- **Complements:** oxlint, oxfmt, prettier (formatting/linting)

## Recommendations

- **Use Lintro** when you want quick type checking integrated into a multi-tool workflow
  with normalized output and timeout safety.
- **Use core astro check directly** when you need:
  - Watch mode for development (`astro check --watch`)
  - Build integration
  - Sync mode for content collections
  - Fine-grained control over astro-specific features
