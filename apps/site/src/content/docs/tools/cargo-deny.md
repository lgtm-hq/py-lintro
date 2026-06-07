---
title: 'cargo-deny'
description: ''
category: tools
order: 60
navGroup: rust
---

# Cargo-deny Tool Analysis

## Overview

Cargo-deny is a Rust tool that checks Cargo dependencies for license compliance,
security advisories, banned crates, and duplicate dependencies. It runs as a `cargo`
subcommand and produces JSON output for structured analysis.

## Core Tool Capabilities

- Runs via `cargo deny check` against workspace or package
- Four check categories: advisories, licenses, bans, sources
- JSON output format (`--format json`) with per-issue diagnostics
- Configured via `deny.toml` in project root

## Lintro Implementation Analysis

### Preserved Features

- Executes `cargo deny check --format json`
- Parses JSON diagnostic output into structured issues
- Discovers Cargo root from provided paths
- Respects project-level `deny.toml` configuration

### Defaults and Notes

- Requires `Cargo.toml` to run; otherwise returns success with message
- Times out after configurable default (60s)
- Cannot auto-fix issues (read-only security/compliance tool)
- Runs all check categories by default (advisories, licenses, bans, sources)

### Enhancements

- Normalized `ToolResult` with issue counts
- Integrates with unified runner and timeout handling
- Structured issue parsing with severity, category, and advisory IDs

## Usage Comparison

### Core cargo-deny

```bash
cargo deny check --format json
cargo deny check advisories
cargo deny check licenses
```

### Lintro Wrapper

```python
tool = CargoDenyPlugin()
result = tool.check(["path/to/project"], {})
```

## Configuration Strategy

- Minimum version: `0.14.0`
- Uses system `cargo deny`; install via `cargo install cargo-deny`
- File patterns: `Cargo.toml`, `deny.toml`
- Timeout configurable via tool options (default: 60s)

## Limited/Missing Features

- No pass-through for selecting specific check categories
- No support for `--exclude` or `--features` flags
- Cannot auto-fix; issues must be resolved manually

## Recommendations

- Consider pass-through for category selection (e.g., advisories-only mode)
- Consider supporting `--exclude` for specific advisory IDs
