# Prettier Tool Analysis

## Overview

Prettier is a code formatter that supports CSS, HTML, JSON, YAML, Markdown, GraphQL, and
many other languages. Lintro assigns CSS, HTML, JSON, YAML, Markdown and GraphQL
patterns to Prettier, and **JavaScript/TypeScript/Vue formatting to Oxfmt**. These are
wrapper responsibilities, not a claim about every native formatter's capabilities.

## File Type Responsibilities

| File Types            | Formatter    | Why                          |
| --------------------- | ------------ | ---------------------------- |
| JS, TS, JSX, TSX, Vue | **oxfmt**    | Assigned by wrapper patterns |
| CSS, SCSS, Less       | **Prettier** | Assigned by wrapper patterns |
| HTML                  | **Prettier** | Assigned by wrapper patterns |
| JSON                  | **Prettier** | Assigned by wrapper patterns |
| YAML                  | **Prettier** | Assigned by wrapper patterns |
| Markdown              | **Prettier** | Assigned by wrapper patterns |
| GraphQL               | **Prettier** | Assigned by wrapper patterns |

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

Lintro checks the batch before running Prettier's `--write`. If that check exits 2 (for
example, a syntax error in one file), formatting stops for the entire batch, including
files that could otherwise be formatted. The run fails and preserves the error output.
Fix the syntax or configuration error and rerun formatting.

**Supported File Patterns:**

```python
PRETTIER_FILE_PATTERNS = [
    "*.css", "*.scss", "*.less",  # Stylesheets
    "*.html",                       # HTML
    "*.json",                       # JSON
    "*.yaml", "*.yml",              # YAML
    "*.md",                         # Markdown
    "*.graphql",                    # GraphQL
    "*.astro",                      # Astro (native plugin may be needed)
]
```

### ⚠️ Limited/Missing Features

**Intentionally Excluded:**

- ❌ **JavaScript/TypeScript**: Use oxfmt instead (assigned to oxfmt by the wrapper)
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
- Need to format JS/TS directly with native Prettier outside Lintro's pattern assignment

### When to Use Lintro Wrapper

- Part of multi-tool linting pipeline
- Need consistent issue reporting across tools
- Want simplified configuration management
- Prefer oxfmt handling JS/TS for performance

## Configuration Strategy

The Lintro wrapper reads native formatting policy and supports a central line-length
override and verbose fix output. Native configuration includes:

- `.prettierrc`
- `.prettierrc.json`
- `prettier.config.js`
- `package.json` "prettier" field

Use native files for detailed style options; the generated option table above lists the
wrapper’s exposed options.

## Migration Notes

If you were previously using Prettier for JavaScript/TypeScript formatting through
Lintro, those files are now handled by oxfmt. To maintain Prettier for JS/TS:

1. Use `prettier` directly (not through Lintro)
2. Selecting only `prettier` skips oxfmt, but does not expand Prettier’s wrapper
   patterns to JS/TS. Run native Prettier for those files.
