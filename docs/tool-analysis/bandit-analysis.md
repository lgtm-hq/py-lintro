# Bandit Tool Analysis

## Overview

Bandit is a security linter for Python code that inspects AST nodes and reports
potential vulnerabilities. This analysis compares Lintro's wrapper with the core Bandit
tool.

## Core Tool Capabilities

- **Recursive scanning**: `-r` to traverse directories
- **Severity/confidence gates**: `-l/-ll/-lll` and `-i/-ii/-iii`
- **Rule selection**: `-t/--tests`, `-s/--skip`
- **Profiles/config**: `-p/--profile`, `-c/--configfile`
- **Baselines**: `-b/--baseline`
- **Aggregation**: `-a vuln|file`
- **Output**: `-f <format>` including `json`, `txt`, `xml`, etc.

## Lintro Implementation Analysis

### ✅ Preserved Features

- ✅ Recursive scanning (`-r`)
- ✅ JSON output (`-f json`) with robust parsing (stdout/stderr mixed)
- ✅ Severity/confidence/test selection, profile, config file, baseline
- ✅ Aggregate mode (`-a vuln|file`), `--ignore-nosec`, `-v/-q`

### ⚠️ Defaults and Notes

- ⚠️ Forces `-f json -q` to ensure parseable output (suppresses logs only)
- ⚠️ Combines stdout+stderr and extracts the JSON object defensively

### 🚀 Enhancements

- ✅ Normalized `ToolResult` with structured issues
- ✅ Stable parsing across Bandit versions/outputs

## Usage Comparison

### Core Bandit

```bash
bandit -r src -f json -q
bandit -r src -lll -iii -t B101,B102 -s B301
```

### Lintro Wrapper

```bash
lintro check src/ --tools bandit
```

## Configuration Strategy

- Respects `[tool.bandit]` in `pyproject.toml` where present
- Supports runtime options via `set_options()` and `--tool-options`

## ⚠️ Limited/Missing Features

- ⚠️ Bandit-specific excludes (`-x/--exclude`) not wired through (Lintro has its own
  exclude mechanism)
- ⚠️ `--exit-zero` not exposed (can be useful in CI)
- ⚠️ Disable recursion (always `-r`) not exposed

### Unimplemented option ideas {#-proposed-runtime-pass-throughs}

The following spellings are proposals, **not working commands in this release**. Use
native configuration or the native CLI for the corresponding behavior.

- `--tool-options bandit:exclude=tests|migrations`
- `--tool-options bandit:exit_zero=True`
- `--tool-options bandit:recursive=False`

## Recommendations

- Use Lintro for normalized findings. For excludes or exit handling outside the
  supported option table, use native configuration or invoke Bandit directly.
