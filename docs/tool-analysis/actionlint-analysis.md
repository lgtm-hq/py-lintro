# Actionlint Tool Analysis

## Overview

Actionlint is a linter for GitHub Actions workflow files. This analysis compares
Lintro's wrapper with the core Actionlint tool.

## Core Tool Capabilities

- **Workflow linting**: Checks YAML under `.github/workflows/`
- **ShellCheck integration**: Lints shell snippets in `run:` steps
- **Rule configuration**: Enable/disable specific checks via config or flags
- **Formatting**: Default text output, `-format`/`-oneline`, color toggles

## Lintro Implementation Analysis

### ✅ Preserved Features

- ✅ Uses default Actionlint output; robust parser maps to structured issues
- ✅ Filters input files to `/.github/workflows/` paths
- ✅ Respects workflow discovery through file walking

### ⚠️ Defaults and Notes

- ⚠️ No flags by default (portable across versions and platforms)
- ⚠️ ShellCheck behavior left to upstream defaults

### 🚀 Enhancements

- ✅ Normalized output (tables/JSON) via Lintro formatters
- ✅ Unified CLI and reporting experience

## Usage Comparison

### Core Actionlint

```bash
actionlint .github/workflows/ci.yml
actionlint -no-color -format oneline .github/workflows/
```

### Lintro Wrapper

```bash
lintro check .github/workflows/ --tools actionlint
```

## ⚠️ Limited/Missing Features

- ⚠️ ShellCheck toggles (`-shellcheck`, `-no-shellcheck`) not exposed
- ⚠️ Config path (`-config-file`) not exposed
- ⚠️ Rule enable/disable flags not exposed
- ⚠️ Output formatting flags (`-format`, `-oneline`, color) not exposed

### Unimplemented option ideas {#-proposed-runtime-pass-throughs}

The following spellings are proposals, **not working commands in this release**. Use
native configuration or the native CLI for the corresponding behavior.

- `--tool-options actionlint:shellcheck=False`
- `--tool-options actionlint:config=.actionlint.yaml`
- `--tool-options actionlint:disable=rule1|rule2`
- `--tool-options actionlint:format=oneline,actionlint:no_color=True`

## Recommendations

- Use the supported wrapper for workflow checks; run native Actionlint directly when you
  need the unavailable flags above.
