# djLint Tool Analysis

## Overview

djLint is a linter and formatter for HTML templates written in template languages such
as Jinja, Django, Nunjucks, Handlebars, and Go templates. It detects common template
problems (rule codes such as `H013`) and reformats template markup for consistent
indentation and layout. This analysis compares Lintro's wrapper implementation with the
core djLint tool.

## Core Tool Capabilities

djLint provides template-aware analysis including:

- **Reformatting**: Consistent indentation/layout via `--reformat`, with a
  non-destructive `--check` mode that reports the diff.
- **Linting**: Rule-based checks for common template issues via `--lint` (e.g. `H006`,
  `H013`).
- **Profiles**: Template-language presets via `--profile` (jinja, django, handlebars,
  nunjucks, golang).
- **Configuration options**: `--indent`, `--max-line-length`, `--ignore`,
  `--extend-exclude`, `--profile`, and more.
- **Statistics**: Occurrence counts per rule via `--statistics`.
- **Configuration files**: `.djlintrc` and the `[tool.djlint]` table in
  `pyproject.toml`.

## Lintro Implementation Analysis

### Preserved Features

- **Formatting check and fix**: `check` runs `djlint --check`; `format` runs
  `djlint --reformat`, following a check -> fix -> verify loop so auto-fix metrics
  (initial/fixed/remaining) are accurate.
- **Profiles**: `profile` option maps to `--profile` (default `jinja`).
- **Indentation and width**: `indent` and `max_line_length` options map to `--indent`
  and `--max-line-length`.
- **Rule ignores**: `ignore` option maps to `--ignore`.
- **Exclusions**: `extend_exclude` option maps to `--extend-exclude`.
- **Native configuration**: `.djlintrc` and `pyproject.toml` are respected by djLint
  automatically.
- **Structured output**: Reformat diffs and rule findings are normalized into standard
  `DjlintIssue` rows.

### Limited / Missing Features

- **Standalone lint mode**: Lintro drives djLint in formatting mode (`--check` /
  `--reformat`); rule-only `--lint` runs are not exposed as a separate lintro mode,
  though the parser tolerates `--lint` output.
- **`--statistics`**: Not surfaced as a lintro option.
- **Per-file ignores**: `--per-file-ignores` is not exposed; use a config file.
- **CSS/JS indent overrides**: `--indent-css` / `--indent-js` are config-file only
  within lintro.

### Enhancements

- **Unified interface**: Same `check()` / `fix()` / `set_options()` surface as every
  other lintro tool.
- **Accurate fix metrics**: The verify pass recomputes remaining issues after a
  reformat, since djLint exits non-zero whenever it rewrites a file.
- **Disjoint file patterns**: djLint claims `*.jinja`, `*.jinja2`, `*.j2`, `*.twig`,
  `*.nj` but deliberately not `*.html`, so it does not collide with prettier's ownership
  of plain HTML.

## Usage Comparison

### Core djLint

```bash
# Check formatting
djlint templates/ --profile jinja --check

# Reformat
djlint templates/ --profile jinja --reformat

# Ignore rule codes
djlint templates/ --profile django --ignore H014,H017 --check
```

### Lintro Wrapper

```bash
# Check formatting
lintro check --tools djlint

# Reformat
lintro format --tools djlint

# With options
lintro check --tools djlint --tool-options djlint:profile=django,djlint:ignore=H014,H017
```

## Configuration Strategy

djLint reads `.djlintrc` or `[tool.djlint]` in `pyproject.toml`. Lintro passes runtime
options through `--tool-options`, layering on top of any native config: config files
provide defaults, runtime options override specific settings.

## Priority and Conflicts

- **Priority**: 50 (default), consistent with other formatters.
- **Conflicts**: None. File patterns are scoped to template extensions and avoid
  `*.html` so prettier retains ownership of plain HTML.

## Recommendations

### When to Use Core djLint

- Need `--statistics`, `--per-file-ignores`, or CSS/JS indent overrides.
- Want standalone rule-only linting via `--lint`.

### When to Use the Lintro Wrapper

- Part of a multi-tool linting/formatting pipeline.
- Want consistent issue reporting and aggregated results across tools.
- Need accurate auto-fix metrics in `format` runs.

## Version Note

djLint is pinned to `1.39.2` (see `pyproject.toml`). From `1.39.3` onward djLint
requires `click>=8.2`, which conflicts with semgrep's `click~=8.1.8` pin in the same
environment. `1.39.2` is the newest djLint that runs under click 8.1.x. This ceiling
should be revisited when semgrep relaxes its click requirement.
