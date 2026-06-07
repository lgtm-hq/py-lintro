---
title: 'black'
description: ''
category: tools
order: 50
navGroup: python
---

# Black Tool Analysis

## Overview

Black is the Python code formatter that provides opinionated, deterministic formatting.
This analysis compares Lintro's wrapper implementation with the core Black tool.

## Core Tool Capabilities

Black provides a stable set of formatting capabilities:

- Code formatting with stable, minimal-diff output
- Check mode (`--check`) and write mode (default)
- Configuration via `pyproject.toml`
- Options such as `--line-length`, `--target-version`, `--fast`, `--preview`, and
  `--diff`

## Lintro Implementation Analysis

### ✅ Preserved Features

- Formatting via Black with check and write flows
- Respect for Black's configuration in `pyproject.toml`
- Standard CLI behavior surfaced through the wrapper

### ⚙️ Runtime Options (Pass-through)

Lintro exposes a subset of Black's CLI options for controlled pass-through:

- `line_length` → `--line-length`
- `target_version` → `--target-version`
- `fast` → `--fast`
- `preview` → `--preview`
- `diff` → `--diff` (when formatting during fix)

These can be provided via `--tool-options` or through `[tool.lintro.black]` in
`pyproject.toml`.

```bash
# CLI overrides
lintro check . --tool-options "black:line_length=100,black:target_version=py313"
lintro format . --tool-options "black:fast=True,black:preview=True"
lintro format . --tool-options "black:diff=True"
```

```toml
[tool.lintro.black]
line_length = 100
target_version = "py313"
fast = false
preview = false
diff = false
```

### 🧩 Cooperation with Ruff

When Black is configured as a post-check in Lintro, Ruff focuses on linting by default
to avoid double-formatting. See the Ruff analysis for details on the Ruff↔Black policy
and how to override with `--tool-options`.

## Usage Comparison

### Core Black

```bash
# Check
black --check src/

# Format
black src/

# With options
black --line-length 100 --target-version py313 src/
```

### Lintro Wrapper

```python
from lintro.tools.implementations.tool_black import BlackTool

tool = BlackTool()
tool.set_options(line_length=100, target_version="py313")
result = tool.check(["src/"])
# or
result = tool.fix(["src/"])
```

With CLI overrides:

```bash
lintro check src/ --tool-options "black:line_length=100,black:target_version=py313"
```

## Configuration Strategy

- Primary configuration via Black's own `pyproject.toml`
- Optional overrides via `[tool.lintro.black]` and `--tool-options`
- Black can be used as a post-check via `[tool.lintro.post_checks]`

## ⚠️ Limited/Missing Features

- No JSON output; Black output is parsed from text
- Only a curated subset of Black options are passed through at runtime
- No stdin-based formatting via wrapper (run core Black for advanced usage)

## Recommendations

- Use Black post-checks to ensure final formatting consistency when combining tools
  (Ruff for lint; Black for formatting)
- Prefer `pyproject.toml` for defaults; use `--tool-options` for ad-hoc runs
