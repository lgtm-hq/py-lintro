# Mypy Tool Analysis

## Overview

Mypy is a static type checker for Python. This analysis compares Lintro's wrapper with
core mypy behavior.

## Core Tool Capabilities

- Static type checking with modern typing (PEP 604 unions, PEP 673 Self)
- Config discovery: `pyproject.toml [tool.mypy]`, `mypy.ini`, `setup.cfg`
- Flags: `--strict`, `--ignore-missing-imports`, `--python-version`, `--config-file`,
  `--cache-dir`, `--show-error-codes`
- Output formats: text, JSON (with error codes/columns), JUnit via plugins
- Exclude/include patterns, namespace package support, incremental cache

## Lintro Implementation Analysis

### ✅ Preserved Features

- Invokes mypy with JSON output plus error codes and columns for precise issue data
- Respects native configs and forwards `--config-file` when discovered
- Defaults to `--strict` and `--ignore-missing-imports` unless overridden via
  `mypy:strict` / `mypy:ignore_missing_imports`
- Supports `config_file` and `cache_dir` tool options. `python_version` requires a
  string; use native configuration for decimal versions because the CLI option parser
  coerces them to floats before validation.
- File discovery for `*.py` and `*.pyi`; applies CLI excludes and tool-level excludes

### ⚠️ Limited / Missing

- No auto-fix (mypy is check-only)
- No pass-through for advanced flags (plugins, namespace packages, incremental tuning,
  warn-unreachable, warn-unused-\* toggles)
- No per-file/namespace overrides beyond what native config provides
- No parallel execution control; uses single mypy invocation

### 🚀 Enhancements

- Safe timeout handling (default 60s) with structured timeout result
- Default exclude set to avoid tests/samples unless a native config provides its own
  excludes
- Auto config discovery in `__post_init__`, reloaded per run to honor project changes
- Normalized `ToolResult` with parsed issues from `mypy_parser`
- Type `LINTER | TYPE_CHECKER`; execution order follows claims and tool-ordering policy

## Usage Comparison

```bash
# Core mypy
mypy --strict --ignore-missing-imports src/

# Lintro wrapper
lintro check src/ --tools mypy
lintro check src/ --tools mypy --tool-options mypy:strict=False
```

To set a Python target version, put a string in `pyproject.toml`:

```toml
[tool.mypy]
python_version = "3.13"
```

Or use `python_version = 3.13` under `[mypy]` in `mypy.ini`. Shell quoting a decimal
value in `--tool-options` does not preserve its string type: this release's common
parser still converts it to a float, which the mypy wrapper refuses.

## Configuration Strategy

- Prefers native configs; if found, Lintro passes them via `--config-file`
- If no native exclude is defined, applies built-in excludes for tests, samples,
  `node_modules`, `dist`, `build`
- Tool options override defaults:
  - `mypy:strict` (bool)
  - `mypy:ignore_missing_imports` (bool)
  - `mypy:python_version` (string in programmatic/config input; decimal CLI values are
    coerced to floats and refused, so use native mypy configuration)
  - `mypy:config_file` (path) — takes precedence over discovery
  - `mypy:cache_dir` (path)
- Uses unified timeout (`mypy:timeout` via global mechanism) to avoid long runs

## Priority and Conflicts

- **Ordering:** Derived from capability claims and tool-ordering configuration.
- **Tool Type:** LINTER | TYPE_CHECKER
- **Conflicts:** None

## Recommendations

- Use Lintro when you want strict-by-default type checking with normalized JSON output
  and project config autodiscovery.
- Use core mypy directly if you need plugin configuration, fine-grained
  cache/incremental tuning, or advanced warning flags not yet exposed via Lintro
  options.
