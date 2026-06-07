---
title: 'prettier'
description: ''
category: tools
order: 130
navGroup: js-ts
---

# Prettier Tool Analysis

## Overview

Prettier is a code formatter that supports CSS, HTML, JSON, YAML, Markdown, GraphQL, and
many other languages. In Lintro, Prettier handles file types that oxfmt doesn't support,
while **oxfmt handles JavaScript/TypeScript/Vue formatting** for better performance.

## File Type Responsibilities

| File Types            | Formatter    | Why                      |
| --------------------- | ------------ | ------------------------ |
| JS, TS, JSX, TSX, Vue | **oxfmt**    | 30x faster than Prettier |
| CSS, SCSS, Less       | **Prettier** | Not supported by oxfmt   |
| HTML                  | **Prettier** | Not supported by oxfmt   |
| JSON                  | **Prettier** | Not supported by oxfmt   |
| YAML                  | **Prettier** | Not supported by oxfmt   |
| Markdown              | **Prettier** | Not supported by oxfmt   |
| GraphQL               | **Prettier** | Not supported by oxfmt   |

## Core Tool Capabilities

Prettier provides extensive CLI options including:

- **Formatting options**: `--tab-width`, `--use-tabs`, `--semi`, `--single-quote`,
  `--quote-props`, `--trailing-comma`
- **File handling**: `--write`, `--check`, `--config`, `--ignore-path`,
  `--stdin-filepath`
- **Parser options**: `--parser` (auto-detect or specify: css, html, json, yaml, etc.)
- **Output control**: `--list-different`, `--require-pragma`, `--insert-pragma`
- **Debug options**: `--debug-check`, `--debug-print-doc`

## Lintro Implementation Analysis

### ✅ Preserved Features

**Core Functionality:**

- ✅ **Formatting capability**: Full preservation through `--write` flag
- ✅ **Check mode**: Preserved through `--check` flag
- ✅ **File targeting**: Supports file patterns and paths
- ✅ **YAML formatting**: Formats `*.yml` / `*.yaml` files (yamllint handles linting)
- ✅ **Configuration files**: Respects `.prettierrc` and `prettier.config.js`
- ✅ **Error detection**: Captures formatting violations as issues
- ✅ **Auto-fixing**: Can automatically format files when `fix()` is called

**Supported File Patterns:**

```python
PRETTIER_FILE_PATTERNS = [
    "*.css", "*.scss", "*.less",  # Stylesheets
    "*.html",                       # HTML
    "*.json",                       # JSON
    "*.yaml", "*.yml",              # YAML
    "*.md",                         # Markdown
    "*.graphql",                    # GraphQL
]
```

### ⚠️ Limited/Missing Features

**Intentionally Excluded:**

- ❌ **JavaScript/TypeScript**: Use oxfmt instead (30x faster)
- ❌ **Vue files**: Use oxfmt instead

**Not Implemented:**

- ⚠️ **Stdin processing**: No `--stdin-filepath` support
- ⚠️ **List different**: Cannot use `--list-different` mode
- ⚠️ **Custom ignore paths**: No runtime `--ignore-path` specification
- ⚠️ **Runtime formatting options**: Prefer config files

### 🚀 Enhancements

**Unified Interface:**

- ✅ **Consistent API**: Same interface as other linting tools
- ✅ **Structured output**: Issues formatted as standardized `Issue` objects
- ✅ **File filtering**: Built-in file extension filtering
- ✅ **Integration ready**: Seamless integration with other tools

## Usage Comparison

### Core Prettier

```bash
# Check formatting (CSS, HTML, JSON, YAML, MD)
prettier --check "src/**/*.{css,html,json,yml,md}"

# Format files
prettier --write "src/**/*.{css,html,json,yml,md}"
```

### Lintro (Combined oxfmt + Prettier)

```bash
# Format all supported files
lintro format --tools oxfmt,prettier

# Check formatting
lintro check --tools oxfmt,prettier
```

## Recommendations

### When to Use Core Prettier

- Need specific formatting options at runtime
- Require debug output or syntax validation
- Working with non-standard file patterns
- Need to format JS/TS (though oxfmt is faster)

### When to Use Lintro Wrapper

- Part of multi-tool linting pipeline
- Need consistent issue reporting across tools
- Want simplified configuration management
- Prefer oxfmt handling JS/TS for performance

## Configuration Strategy

The Lintro wrapper relies entirely on Prettier's configuration files:

- `.prettierrc`
- `.prettierrc.json`
- `prettier.config.js`
- `package.json` "prettier" field

For runtime customization, users should modify these config files rather than passing
CLI options.

## Migration Notes

If you were previously using Prettier for JavaScript/TypeScript formatting through
Lintro, those files are now handled by oxfmt. To maintain Prettier for JS/TS:

1. Use `prettier` directly (not through Lintro)
2. Or configure Lintro to skip oxfmt: `lintro format --tools prettier`
