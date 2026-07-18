# j2lint Tool Analysis

## Overview

[j2lint](https://github.com/aristanetworks/j2lint) is a command-line linter for Jinja2
templates maintained by Arista Networks. It targets non-HTML Jinja2 templates (for
example `*.j2` files used to render YAML, TOML, or other configuration output) and
enforces a set of best-practice rules covering indentation, delimiter spacing, statement
layout, and variable naming. This analysis compares Lintro's wrapper implementation with
the core j2lint tool.

## Core Tool Capabilities

j2lint provides Jinja2 template analysis including:

- **Best-practice rules**: Indentation (`S3`), single-statement-per-line, delimiter
  spacing (`S6`), variable spacing (`S1`), variable naming (`V1`/`V2`), and syntax
  errors (`S0`).
- **Rule control**: `--ignore` to drop rules entirely and `--warn` to demote rules from
  errors to warnings.
- **Output formats**: default text output and structured `--json` output.
- **File discovery**: recursive directory scanning with a configurable extension list.
- **STDIN linting**: templates can be piped in via `--stdin`.

## Lintro Implementation Analysis

### Preserved Features

- **JSON-based parsing**: Lintro runs `j2lint --json` and parses the structured
  `ERRORS`/`WARNINGS` report, preserving rule IDs, messages, filenames, and line
  numbers.
- **Rule ignoring**: `ignore` option maps to `-i` (rules dropped entirely).
- **Rule warning demotion**: `warn` option maps to `-w` (errors demoted to warnings);
  demoted rules do not fail the run.
- **File targeting**: patterns `*.j2`, `*.jinja`, `*.jinja2`.
- **Error categorization**: entries reported under `ERRORS` map to severity `error` and
  entries under `WARNINGS` map to severity `warning`.

### Limited / Missing Features

- **Custom rules directory**: j2lint's `-r/--rules_dir` is not exposed via Lintro.
- **Extension override**: j2lint's `-e/--extensions` is not exposed; Lintro controls
  discovery through its own file patterns instead.
- **STDIN mode**: not exposed; Lintro operates on discovered files.
- **Auto-fixing**: j2lint cannot fix issues, so Lintro's `fix()` raises
  `NotImplementedError`.

### Enhancements

- **Unified interface**: same `check()` / `set_options()` API as every other Lintro
  tool, with issues normalized into the shared `BaseIssue` display format.
- **Timeout handling**: a configurable per-run timeout (default 30s) with a clear
  message when exceeded.
- **Robust JSON extraction**: the parser locates the JSON object even if surrounding log
  noise is present, and degrades gracefully (empty result) on unparseable output.
- **Complementary execution**: priority `60` lets j2lint run after HTML-oriented Jinja
  linters so both rulesets apply to shared `*.jinja` files.

## Usage Comparison

### Core j2lint

```bash
# Basic checking
j2lint template.j2

# JSON output
j2lint --json template.j2

# Ignore and warn on specific rules (files after --)
j2lint --json -i S3 -w S6 -- template.j2
```

### Lintro Wrapper

```bash
# Basic checking
lintro check --tools j2lint

# Ignore and demote specific rules
lintro check --tools j2lint --tool-options "j2lint:ignore=S3 j2lint:warn=S6"
```

## Configuration Strategy

- **Native config**: `.j2lint.yaml` is declared as a native config file.
- **Runtime options**: `ignore`, `warn`, and `timeout` are available via
  `--tool-options`.

## Priority and Conflicts

- **Priority**: `60` (runs after formatters and most linters).
- **Conflicts**: none. j2lint focuses on non-HTML Jinja2 templates and is designed to
  run alongside HTML-oriented Jinja linters on shared `*.jinja` files.

## Error Code Mapping

Lintro preserves j2lint's rule identifiers:

| Rule                   | ID    | Description                                    |
| ---------------------- | ----- | ---------------------------------------------- |
| Syntax error           | S0    | Jinja2 syntax error                            |
| Single space delimiter | S1    | Space required inside variable delimiters      |
| Operator spacing       | S2    | Operators enclosed by spaces                   |
| Indentation            | S3    | Bad indentation                                |
| Statement delimiters   | S6    | Statements must not use `{%-`/`-%}` delimiters |
| Variable naming        | V1/V2 | Variable lower-case / format                   |

## Recommendations

### When to Use Core j2lint

- Need a custom rules directory or a non-standard extension list.
- Want to lint templates piped through STDIN.

### When to Use the Lintro Wrapper

- Part of a multi-tool linting pipeline.
- Want consistent, normalized issue reporting across tools.
- Need aggregated results and standardized error handling.
