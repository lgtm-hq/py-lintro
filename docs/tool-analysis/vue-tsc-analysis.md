# Vue-tsc Tool Analysis

## Overview

Vue-tsc is the TypeScript type checker for Vue Single File Components (SFCs). It extends
`tsc` with Vue-specific type checking capabilities, enabling proper type checking for
`.vue` files including `<script setup>`, component props, template expressions, and slot
types. This analysis compares Lintro's wrapper with core vue-tsc behavior.

## Core Tool Capabilities

- TypeScript type checking for `.vue` files
- `<script setup>` and Composition API type checking
- Component prop type validation
- Template expression type checking
- Slot type inference
- Config discovery: `tsconfig.json`, `tsconfig.app.json`
- Integration with project's `tsconfig.json`
- Watch mode for development (`--watch`)

## Lintro Implementation Analysis

### Preserved Features

- Invokes `vue-tsc` for type checking with `--noEmit --pretty false`
- Respects native `tsconfig.json` and `tsconfig.app.json`
- Intelligent command fallback: direct `vue-tsc` -> `bunx vue-tsc` -> `npx vue-tsc`
- Parses vue-tsc output into structured `ToolResult` with file/line/column/code
- Auto-install support for Node.js dependencies
- Temporary tsconfig generation for targeted file checking
- Respects tsconfig.json `include`/`exclude`/`files` scoping (#851)
- Multi-project monorepo discovery and per-project checking (#803, #805)
- Dependency error categorization with helpful suggestions

### Limited / Missing

**Watch Mode:**

- No `--watch` mode (continuous type checking)

**Build Features:**

- No build mode or output generation
- Type checking only (no production builds)

**Advanced Features:**

- No declaration file generation (`--declaration`)
- No composite project building (`--build`)

### Enhancements

- Safe timeout handling (default 120s) with structured timeout result
- Targeted file checking via temporary tsconfig extending base config
- Normalized `ToolResult` with parsed issues from `vue_tsc_parser`
- Priority 83, tool type `LINTER | TYPE_CHECKER`
- Graceful handling when vue-tsc is not installed with helpful install hints
- Dependency error detection with missing module extraction
- Auto-install support for Node.js dependencies
- `--skipLibCheck` enabled by default for faster checking

## Usage Comparison

```bash
# Core vue-tsc
vue-tsc --noEmit

# Core vue-tsc with specific project
vue-tsc --noEmit --project tsconfig.app.json

# Lintro wrapper - check Vue files
lintro check src/ --tools vue-tsc

# Lintro wrapper - with specific project
lintro check . --tools vue-tsc --tool-options "vue-tsc:project=tsconfig.app.json"

# Lintro wrapper - auto-install dependencies
lintro check src/ --tools vue-tsc --auto-install
```

## Configuration Strategy

- **Config inherited:** All settings from `tsconfig.json` are preserved
- **No config injection:** Lintro cannot modify tsconfig settings; tool is "Native only"
- **Tool options available:**
  - `vue-tsc:project` (string) - path to tsconfig.json file
  - `vue-tsc:strict` (bool) - enable strict type checking mode
  - `vue-tsc:skip_lib_check` (bool) - skip checking declaration files (default: true)
  - `vue-tsc:use_project_files` (bool) - use tsconfig's include patterns (default:
    false)
  - `vue-tsc:timeout` (int) - execution timeout in seconds (default: 120)
- **Config display:** `lintro config -v` shows detected tsconfig.json file

## Priority and Conflicts

- **Priority:** 83 (runs after tsc, same priority as astro-check)
- **Tool Type:** LINTER | TYPE_CHECKER
- **Conflicts:** None
- **Complements:** oxlint, oxfmt, prettier (formatting/linting)

## Recommendations

- **Use Lintro** when you want quick type checking integrated into a multi-tool workflow
  with normalized output and timeout safety.
- **Use core vue-tsc directly** when you need:
  - Watch mode for development (`vue-tsc --watch`)
  - Declaration file generation
  - Composite project builds
  - Fine-grained control over TypeScript-specific features
