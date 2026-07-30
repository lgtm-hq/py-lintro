# golangci-lint Tool Analysis

## Overview

[golangci-lint](https://golangci-lint.run/) is the de-facto Go meta-linter. It runs 100+
sub-linters (errcheck, staticcheck, ineffassign, govet, gosec, misspell, ...) in
parallel with aggressive caching. lintro integrates it as a module-context linter: it
requires a Go module (`go.mod`) and the Go toolchain, and skips cleanly for non-Go
projects.

This plugin targets **golangci-lint v2**. The v2 CLI replaced the v1
`--out-format <fmt>` flag with per-format `--output.<fmt>.path` options and changed the
config schema (`version: "2"`); v1 is not supported.

## Core Tool Capabilities

- Runs via `golangci-lint run` over a Go module (`./...`)
- Aggregates dozens of sub-linters; each finding is attributed to its linter
- Machine-readable JSON via `--output.json.path stdout`
- Autofix for supported linters via `--fix`
- YAML/TOML/JSON configuration (`.golangci.yml` and variants)

## Parser Choice: native JSON (not SARIF)

golangci-lint can emit SARIF (`--output.sarif.path`), but its SARIF export is **lossy**
for lintro's model, per the fidelity checklist in
`docs/design/sarif-ingestion-evaluation.md`. Comparing both formats on the same run:

| Signal                 | Native JSON                           | golangci-lint SARIF                     |
| ---------------------- | ------------------------------------- | --------------------------------------- |
| Sub-linter attribution | `Issues[].FromLinter`                 | `results[].ruleId` (preserved)          |
| Severity               | per-issue `Severity` (configurable)   | **hard-coded `level: "error"`** for all |
| Fix / autofix metadata | `SuggestedFixes` / `Replacement`      | **no `fixes[]` at all**                 |
| Doc URLs               | synthesized from linter name (lintro) | **no `rules[]` / `helpUri`**            |

SARIF would collapse every finding to `error` (dropping the configurable per-issue
severity), drop all autofix metadata (so lintro could not surface which issues are
fixable), and omit rule metadata (no doc URLs). A native JSON parser is therefore used.
This matches the evaluation's guidance to use the shared SARIF parser only when it is
lossless for the tool, and golangci-lint is not among its SARIF-native candidates.

## Lintro Implementation Analysis

### ✅ Preserved Features

- Executes `golangci-lint run --output.json.path stdout --show-stats=false ./...`
- Autofix path runs `golangci-lint run --fix ./...`, then re-checks
- Parses JSON `Issues[]` into structured issues (linter, position, message, severity,
  fixable)
- Discovers the Go module root (`go.mod`) from provided paths
- Per-linter documentation links via `https://golangci-lint.run/usage/linters/#<linter>`

### ⚠️ Defaults and Notes

- Requires `go.mod` and the Go toolchain; otherwise returns success with a skip message
  ("No go.mod found; skipping golangci-lint.")
- Whole-module scan (`./...`), mirroring the compiled-language pattern used by the
  Clippy plugin. Because execution changes to the module root and runs `./...`,
  golangci-lint analyzes every file in each selected module — file-level lintro exclude
  patterns do not scope the scan (golangci-lint v2's own `.golangci.yml`
  `linters.exclusions.paths`/`formatters.exclusions.paths` settings are the way to
  exclude paths, and `linters.exclusions.rules` suppresses specific findings)
- Times out after a configurable default (120s)
- A blank `Severity` normalizes to `WARNING`; an explicit `Severity` is kept
- The parser tolerates a trailing human-readable stats footer and ANSI codes

### 🚀 Enhancements

- Normalized `ToolResult` with issue counts and fix metrics
- `fixable` derived from `SuggestedFixes`/`Replacement` presence
- Integrates with the unified runner, timeout handling, and doctor health check

## Usage Comparison

### Core golangci-lint

```bash
golangci-lint run --output.json.path stdout --show-stats=false ./...
golangci-lint run --fix ./...
```

### Lintro Wrapper

```python
tool = GolangciLintPlugin()
result = tool.check(["path/to/go/module"], {})
result = tool.fix(["path/to/go/module"], {})
```

## Configuration Strategy

- Minimum/recommended version tracked in `lintro/_tool_versions.py` and
  `lintro/tools/manifest.json` (kept in sync by the manifest generator; bumped by
  Renovate via `golangci/golangci-lint` GitHub releases)
- Uses the system `golangci-lint`; install via `brew install golangci-lint` or the
  official installer at <https://golangci-lint.run/welcome/install/>
- Native configs: `.golangci.yml`, `.golangci.yaml`, `.golangci.toml`, `.golangci.json`
- File patterns: `*.go`, `go.mod`
- Timeout configurable via tool options

## Included Linters (selection)

golangci-lint bundles 100+ linters. A representative subset:

- **errcheck** — unchecked error return values
- **govet** — `go vet` correctness checks
- **staticcheck** — comprehensive static analysis
- **ineffassign** — ineffectual assignments
- **unused** — unused code
- **gosec** — security issues
- **misspell** — common English misspellings (autofixable)

See <https://golangci-lint.run/usage/linters/> for the full, versioned list.

## ⚠️ Limited/Missing Features

- Sub-linter selection/config is delegated to the project's `.golangci.*` file rather
  than exposed as lintro options
- Only the primary position of each finding is captured (start line/column)
- SARIF output is intentionally not consumed (see
  [Parser Choice](#parser-choice-native-json-not-sarif))

## References

- <https://golangci-lint.run/>
- <https://github.com/golangci/golangci-lint>
