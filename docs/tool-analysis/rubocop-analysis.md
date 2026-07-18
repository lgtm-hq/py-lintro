# RuboCop Tool Analysis

## Overview

RuboCop is a Ruby static code analyzer (linter) and formatter based on the community
Ruby style guide. It ships an extensive rule set organized into departments and can
autocorrect many offenses in place. This analysis compares Lintro's wrapper
implementation with the core RuboCop tool, and documents why Lintro parses RuboCop's
native JSON rather than SARIF.

## Core Tool Capabilities

RuboCop provides comprehensive Ruby analysis including:

- **Extensive rule set**: Hundreds of cops grouped into departments (Layout, Lint,
  Metrics, Naming, Security, Style).
- **Autocorrection**: Safe (`--autocorrect`/`-a`) and unsafe (`--autocorrect-all`/`-A`)
  fixes.
- **Configurable**: Driven by `.rubocop.yml`/`.rubocop.yaml`; runs with sensible
  defaults when no config is present.
- **Extensions**: `rubocop-rails`, `rubocop-rspec`, `rubocop-performance`,
  `rubocop-minitest`, and custom cops.
- **Output formats**: progress, simple, clang, emacs, files, github, html, json, junit,
  markdown, offenses, tap, worst (and custom formatter classes).
- **LSP support**: A built-in language server.

## Parser Choice: Native JSON, not SARIF

RuboCop 1.88 bundles **no SARIF formatter** (its built-in formatters are autogenconf,
clang, emacs, files, github, html, json, junit, markdown, offenses, pacman, progress,
quiet, simple, tap, and worst). SARIF ingestion (see
`docs/design/sarif-ingestion-evaluation.md`) is therefore not an option without an
external converter, so Lintro uses a native parser over `rubocop --format json`.

Even if a SARIF path existed, it would be **lossy** for RuboCop. The JSON payload
distinguishes two per-offense booleans:

- `correctable` — whether a cop _can_ autocorrect the offense.
- `corrected` — whether the offense _was_ autocorrected in this run.

SARIF only models fix **presence** as a `result.fixes[]` array (a single boolean signal
in practice). It cannot represent "correctable but not yet corrected", which is exactly
the signal that drives Lintro's `fixable` flag. The native JSON also gives precise
ranges (`start_line`/`start_column`/`last_line`/`last_column`), the fully-qualified cop
name (department + cop), and RuboCop's six-level severity taxonomy — all preserved
losslessly by the native parser.

## Lintro Implementation Analysis

### Preserved Features

- **Linting**: Full offense detection via `rubocop --format json`.
- **Autocorrection**: `fix()` runs safe `--autocorrect` by default; `--autocorrect-all`
  is available behind the `unsafe_fixes` option.
- **Native config**: `.rubocop.yml`/`.rubocop.yaml` are respected automatically.
- **File targeting**: `*.rb`, `*.rake`, `*.gemspec`, `Gemfile`, `Rakefile`.
- **Rich metadata**: cop name, department, native severity, precise ranges, and the
  `correctable`/`corrected` flags are all captured on each issue.
- **Doc URLs**: Each cop links to its documentation page
  (`https://docs.rubocop.org/rubocop/cops_<department>.html#<anchor>`).

### Severity Mapping

RuboCop's six severities normalize onto Lintro's three levels:

| RuboCop severity | Lintro level |
| ---------------- | ------------ |
| `info`           | INFO         |
| `refactor`       | INFO         |
| `convention`     | WARNING      |
| `warning`        | WARNING      |
| `error`          | ERROR        |
| `fatal`          | ERROR        |

### Limited / Missing Features

- **Per-cop runtime toggles**: Enabling/disabling individual cops or tuning their styles
  is done via `.rubocop.yml`, not Lintro `--tool-options`.
- **Extension auto-install**: Lintro does not install `rubocop-rails`/`rubocop-rspec`;
  add them to the project's Gemfile and `.rubocop.yml` `require` list.
- **Custom formatters**: Lintro always uses the JSON formatter internally.

### Enhancements

- **Unified interface**: Same `check()`/`fix()` contract as every other Lintro tool.
- **Structured output**: Offenses become standardized issue objects rendered by the
  unified formatter (grid, JSON, etc.).
- **stdout/stderr separation**: Only stdout is parsed, so RuboCop's "new cops not
  configured" notice (emitted on stderr) never corrupts the JSON (see issue #1043).
- **Fix invariant**: `fix()` guarantees `initial == fixed + remaining` by check →
  autocorrect → re-check.
- **Configurable timeout**: Defaults to 60s; override via `rubocop:timeout=N`.

## Usage Comparison

### Core RuboCop

```bash
# Lint
rubocop app.rb

# Machine-readable output
rubocop --format json app.rb

# Safe autocorrect
rubocop --autocorrect app.rb

# Unsafe autocorrect (may change semantics)
rubocop --autocorrect-all app.rb
```

### Lintro Wrapper

```bash
# Lint Ruby files
lintro check --tools rubocop

# Safe autocorrect (default)
lintro fmt --tools rubocop

# Unsafe autocorrect
lintro fmt --tools rubocop --tool-options "rubocop:unsafe_fixes=True"
```

## Configuration Strategy

RuboCop is configured primarily through `.rubocop.yml`. Lintro exposes only the
behavioral knobs that are genuinely runtime concerns:

| Option         | Type | Description                                                          |
| -------------- | ---- | -------------------------------------------------------------------- |
| `unsafe_fixes` | bool | Use `--autocorrect-all` instead of safe `--autocorrect`. Default off |
| `timeout`      | int  | Per-invocation timeout in seconds. Default 60                        |

## Cop Departments

| Department | Purpose                     |
| ---------- | --------------------------- |
| Layout     | Whitespace and formatting   |
| Lint       | Likely bugs and dead code   |
| Metrics    | Size and complexity limits  |
| Naming     | Naming conventions          |
| Security   | Insecure patterns           |
| Style      | Idiomatic style conventions |

## Priority and Conflicts

RuboCop runs at priority 55 as a combined linter/formatter. It has no declared conflicts
with other tools because it targets Ruby files that no other bundled tool inspects.

## Recommendations

### When to Use Core RuboCop

- You need per-cop runtime overrides or a specific non-JSON output format.
- You rely on RuboCop extensions (Rails, RSpec, Performance) with bespoke configuration.
- You want RuboCop's interactive/LSP workflow.

### When to Use the Lintro Wrapper

- RuboCop is one step in a multi-language, multi-tool pipeline.
- You want consistent issue reporting, doc URLs, and the safe-by-default autocorrect.
- You want the `initial == fixed + remaining` fix accounting and unified output formats.

## Future Enhancement Opportunities

1. **Cop selection pass-through**: `rubocop:only=Layout,Style` / `rubocop:except=...`.
2. **Extension awareness**: Detect and surface required extensions from `.rubocop.yml`.
3. **Config pass-through**: `rubocop:config=path/to/.rubocop.yml`.
