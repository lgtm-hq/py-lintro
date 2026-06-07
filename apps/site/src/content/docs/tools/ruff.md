---
title: 'ruff'
description: ''
category: tools
order: 160
navGroup: python
---

# Ruff Tool Analysis

## Overview

Ruff is an extremely fast Python linter and code formatter written in Rust that can
replace multiple Python tools like flake8, black, isort, and more. This analysis
compares Lintro's wrapper implementation with the core Ruff tool.

## Core Tool Capabilities

Ruff provides extensive linting and formatting capabilities including:

- **Linting**: 700+ rules covering PEP 8, flake8, isort, pyupgrade, and more
- **Formatting**: Black-compatible code formatting with customization options
- **Performance**: Extremely fast execution due to Rust implementation
- **Configuration**: `pyproject.toml`, `ruff.toml`, or command-line options
- **Rule selection**: `--select`, `--ignore`, `--extend-select`, `--extend-ignore`
- **Formatting options**: `--line-length`, `--target-version`,
  `--skip-magic-trailing-comma`
- **Fix capabilities**: `--fix`, `--unsafe-fixes`, `--fix-only`, `--show-fixes`
- **Output formats**: Default, JSON, SARIF, JUnit XML

## Lintro Implementation Analysis

### ✅ Preserved Features

**Core Functionality:**

- ✅ **Linting capability**: Full preservation through `ruff check` command
- ✅ **Formatting capability**: Full preservation through `ruff format` command
- ✅ **Auto-fixing**: Supports `--fix` with safe and unsafe fix options
- ✅ **Rule selection**: Supports `--select`, `--ignore`, `--extend-select`,
  `--extend-ignore`
- ✅ **Configuration files**: Respects `pyproject.toml` and `ruff.toml`
- ✅ **File targeting**: Supports Python file patterns (`*.py`, `*.pyi`)
- ✅ **Error detection**: Captures both linting and formatting violations

**Command Execution:**

```python
# From tool_ruff.py
cmd = self._get_executable_command("ruff") + ["check"]
# For fixing:
cmd = self._get_executable_command("ruff") + ["check", "--fix"]
# For formatting:
cmd = self._get_executable_command("ruff") + ["format"]
```

**Configuration Options:**

- ✅ **Rule selection**: `select`, `ignore`, `extend_select`, `extend_ignore`
- ✅ **Line length**: `line_length` parameter
- ✅ **Target version**: `target_version` parameter
- ✅ **Fix options**: `fix_only`, `unsafe_fixes`, `show_fixes`
- ✅ **Formatting control**: `format` boolean to enable/disable formatting

### Cooperation with Black (Policy)

When Black is configured as a post-check in Lintro, Ruff focuses on linting by default:

- In `lintro format`, Ruff fixes lint issues while `format=False` unless explicitly
  overridden via `--tool-options ruff:format=True`.
- In `lintro check`, Ruff runs lint checks with `format_check=False` unless explicitly
  overridden via `--tool-options ruff:format_check=True`.

This avoids double-formatting and lets Black handle final formatting. You can override
either side via CLI or `[tool.lintro.ruff]` and `[tool.lintro.post_checks]`.

```bash
# Force Ruff to format even with Black post-checks enabled
lintro format --tool-options "ruff:format=True"

# Force Ruff to include format-check during check
lintro check --tool-options "ruff:format_check=True"
```

### ⚠️ Limited/Missing Features

**Advanced Configuration:**

- ⚠️ **Per-file ignores and excludes at runtime**: Prefer config files; proposed CLI
  pass-throughs include `per_file_ignores`, `extend_exclude`, `force_exclude`,
  `respect_gitignore`.
- ❌ **Custom rule definitions**: Not supported by Lintro wrappers (upstream feature set
  only).
- ⚠️ **Config path/isolated**: Proposed `ruff:config=...`, `ruff:isolated=True` for
  ad-hoc runs.
- ⚠️ **Output controls**: `quiet`, `statistics`, `preview` useful for UX; propose
  pass-throughs.

**Formatting Options:**

- ❌ **Detailed formatting options**: No access to `--skip-magic-trailing-comma`,
  `--preview`
- ❌ **Formatting configuration**: Limited line length and target version control
- ❌ **Format-only mode**: Cannot run formatter without linter

**Advanced Features:**

- ❌ **Watch mode**: No `--watch` functionality for continuous monitoring
- ❌ **Cache control**: No access to `--cache-dir`, `--no-cache`
- ❌ **Statistics**: No access to `--statistics` output
- ❌ **Exit codes**: Limited exit code customization

**Performance Options:**

- ❌ **Parallel processing**: No access to Ruff's built-in parallelization
- ❌ **Memory optimization**: No control over memory usage settings

### 🚀 Enhancements

**Unified Interface:**

- ✅ **Consistent API**: Same interface as other linting tools (`check()`, `fix()`,
  `set_options()`)
- ✅ **Structured output**: Issues formatted as standardized `Issue` objects
- ✅ **Combined operations**: Runs both linting and formatting in single operation

### 🔧 Proposed runtime pass-throughs

- `--tool-options ruff:config=path/to/ruff.toml`
- `--tool-options ruff:per_file_ignores=A.py:E501|pkg/B.py:I001`
- `--tool-options ruff:extend_exclude=build|dist,ruff:force_exclude=True`
- `--tool-options ruff:respect_gitignore=True`
- `--tool-options ruff:quiet=True,ruff:statistics=True,ruff:preview=True`
- ✅ **Integration ready**: Seamless integration with other tools in linting pipeline

**Enhanced Error Processing:**

- ✅ **Issue normalization**: Converts Ruff output to standard Issue format:

  ```python
  Issue(
      file_path=issue.file,
      line_number=issue.line,
      column_number=issue.column,
      error_code=issue.code,
      message=issue.message,
      severity="error"
  )
  ```

**Smart Fix Handling:**

- ✅ **Fix reporting**: Shows number of issues fixed vs remaining
- ✅ **Unsafe fix detection**: Warns about issues that could be fixed with unsafe fixes
- ✅ **Fix-only mode**: Option to only apply fixes without reporting remaining issues
- ✅ **Format integration**: Automatic formatting when enabled

**File Management:**

- ✅ **Extension filtering**: Automatic Python file detection
- ✅ **Batch processing**: Efficient handling of multiple files
- ✅ **Error aggregation**: Collects all issues across files

## Usage Comparison

### Core Ruff

```bash
# Basic linting
ruff check src/

# With specific rules
ruff check --select E501,W503,B006 src/

# Auto-fixing
ruff check --fix src/

# Formatting
ruff format src/

# Combined lint and format
ruff check --fix src/ && ruff format src/
```

### Lintro Wrapper

```python
# Basic checking (lint + format)
ruff_tool = RuffTool()
ruff_tool.set_files(["src/main.py"])
issues = ruff_tool.check()

# Auto-fixing
ruff_tool.fix()

# With specific options
ruff_tool.set_options(
    select=["E501", "W503", "B006"],
    line_length=88,
    unsafe_fixes=True
)
```

## Configuration Strategy

### Core Tool Configuration

Ruff uses configuration files:

- `pyproject.toml` `[tool.ruff]` section
- `ruff.toml`
- `.ruff.toml`

### Lintro Approach

The wrapper supports both configuration files and runtime options:

- **Configuration files**: Primary configuration method
- **Runtime options**: Override specific settings via `set_options()`
- **Combined approach**: Configuration files provide defaults, runtime options override

## Rule Categories

Lintro preserves all Ruff rule categories:

| Category                  | Prefix | Description                        |
| ------------------------- | ------ | ---------------------------------- |
| **Pyflakes**              | F      | Logical errors and undefined names |
| **pycodestyle**           | E, W   | PEP 8 style violations             |
| **isort**                 | I      | Import sorting issues              |
| **pep8-naming**           | N      | Naming conventions (PEP 8)         |
| **pydocstyle**            | D      | Docstring style violations         |
| **pyupgrade**             | UP     | Python upgrade suggestions         |
| **flake8-annotations**    | ANN    | Type annotation requirements       |
| **flake8-bugbear**        | B      | Bug detection and complexity       |
| **flake8-comprehensions** | C4     | Comprehension improvements         |
| **flake8-simplify**       | SIM    | Code simplification suggestions    |

## Recommendations

### When to Use Core Ruff

- Need maximum configuration flexibility
- Require specific output formats (SARIF, JUnit XML)
- Want watch mode for continuous monitoring
- Need custom rule definitions
- Require detailed statistics and caching control

### When to Use Lintro Wrapper

- Part of multi-tool linting pipeline
- Need consistent issue reporting across tools
- Want Python object integration
- Require combined linting and formatting
- Need standardized error handling

## Limitations and Workarounds

### Limited Runtime Configuration

**Problem**: Cannot customize all Ruff options at runtime **Workaround**: Use
configuration files for complex setups, runtime options for overrides

### No Custom Rules

**Problem**: Cannot define custom linting rules **Workaround**: Use Ruff's extensive
built-in rule set (700+ rules)

### Limited Output Formats

**Problem**: Limited to JSON output for parsing **Workaround**: Lintro provides
structured `Issue` objects and multiple output formats

## Future Enhancement Opportunities

1. **Configuration Pass-through**: Support for all Ruff CLI options
2. **Custom Rules**: Integration with Ruff's rule system
3. **Watch Mode**: Continuous monitoring capabilities
4. **Performance**: Leverage Ruff's parallel processing
5. **Statistics**: Detailed performance and issue statistics
6. **Cache Integration**: Intelligent caching for faster subsequent runs
