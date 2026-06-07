---
title: 'clippy'
description: ''
category: tools
order: 70
navGroup: rust
---

# Clippy Tool Analysis

## Overview

Clippy is Rust's linter delivered as a `cargo` subcommand. It surfaces correctness,
style, and performance lints across all targets and features.

## Core Tool Capabilities

- Runs via `cargo clippy` against workspace or package
- Supports all targets/features, JSON diagnostics (`--message-format=json`)
- Autofix for some lints via `--fix --allow-dirty --allow-staged`

## Lintro Implementation Analysis

### ✅ Preserved Features

- Executes `cargo clippy --all-targets --all-features --message-format=json`
- Autofix path uses `--fix --allow-dirty --allow-staged`
- Parses Cargo diagnostic JSON into structured issues
- Discovers Cargo root from provided paths; respects exclude patterns

### ⚠️ Defaults and Notes

- Requires `Cargo.toml` to run; otherwise returns success with message
- Times out after configurable default (120s)
- Uses first span for location; multi-span support limited to first entry

### 🚀 Enhancements

- Normalized `ToolResult` with issue counts and fix metrics
- Detects fixable hints via presence of suggestions
- Integrates with unified runner and timeout handling

## Usage Comparison

### Core Clippy

```bash
cargo clippy --all-targets --all-features --message-format=json
cargo clippy --fix --allow-dirty --allow-staged
```

### Lintro Wrapper

```python
tool = ClippyTool()
result = tool.check([\"path/to/project\"])
result = tool.fix([\"path/to/project\"])
```

## Configuration Strategy

- Minimum version from `[tool.lintro.versions].clippy`
- Uses system `cargo clippy`; install via `rustup component add clippy`
- File patterns: `*.rs`, `Cargo.toml`, `Cargo.lock`
- Timeout configurable via tool options

## ⚠️ Limited/Missing Features

- No pass-through for custom clippy args (e.g., target selection tweaks)
- Does not surface secondary spans; only first span is shown

## Recommendations

- Consider pass-through for additional clippy flags when needed
