# Ruff Tool Analysis

## Overview

Ruff is a Python linter and code formatter written in Rust that covers rules from
flake8, isort, pyupgrade and other tools, with Black-compatible formatting. This
analysis compares Lintro's wrapper implementation with the core Ruff tool.

## Core Tool Capabilities

Ruff provides extensive linting and formatting capabilities including:

- **Linting**: Rules covering PEP 8, flake8, isort, pyupgrade, and more; the count
  depends on the installed release
- **Formatting**: Black-compatible code formatting with customization options
- **Implementation**: Rust-based; no timing is established by this analysis
- **Configuration**: `pyproject.toml`, `ruff.toml`, or command-line options
- **Rule selection**: `--select`, `--ignore`, `--extend-select`, `--extend-ignore`
- **Formatting options**: `--line-length`, `--target-version`,
  `--skip-magic-trailing-comma`
- **Fix capabilities**: `--fix`, `--unsafe-fixes`, `--fix-only`, `--show-fixes`
- **Output formats**: Default, JSON, SARIF, JUnit XML

## Lintro Implementation Analysis

### ✅ Preserved Features

**Core Functionality:**

- ✅ **Linting capability**: Full preservation through `ruff check` command
- ✅ **Formatting capability**: Full preservation through `ruff format` command
- ✅ **Auto-fixing**: Supports `--fix` with safe and unsafe fix options
- ✅ **Rule selection**: Supports `--select`, `--ignore`, `--extend-select`,
  `--extend-ignore`
- ✅ **Configuration files**: Respects `pyproject.toml` and `ruff.toml`
- ✅ **File targeting**: Supports Python file patterns (`*.py`, `*.pyi`)
- ✅ **Error detection**: Captures both linting and formatting violations

**Command Execution:**

```python
# From tool_ruff.py
cmd = self._get_executable_command("ruff") + ["check"]
# For fixing:
cmd = self._get_executable_command("ruff") + ["check", "--fix"]
# For formatting:
cmd = self._get_executable_command("ruff") + ["format"]
```

**Configuration Options:**

- ✅ **Rule selection**: `select`, `ignore`, `extend_select`, `extend_ignore`
- ✅ **Line length**: `line_length` parameter
- ✅ **Target version**: `target_version` parameter
- ✅ **Fix options**: `fix_only`, `unsafe_fixes`, `show_fixes`
- ✅ **Formatting control**: `format` boolean to enable/disable formatting

### Cooperation with Black (Policy)

When Ruff and Black are both selected and Black is runnable, Ruff focuses on linting by
default while Black owns Python formatting. If Black is unavailable or fails its version
gate, Ruff's formatting stages stay enabled by default when Ruff is runnable. Explicit
Ruff `format` and `format_check` overrides still apply. Use
`lintro check --explain-order` to inspect resolved ownership. Legacy
`[tool.lintro.post_checks]` configuration is ignored.

- In `lintro format`, Ruff fixes lint issues while `format=False` unless explicitly
  overridden via `--tool-options ruff:format=True`.
- In `lintro check`, Ruff runs lint checks with `format_check=False` unless explicitly
  overridden via `--tool-options ruff:format_check=True`.

This avoids double-formatting and lets Black handle final formatting. You can override
either side via CLI or `[tool.lintro.ruff]`.

```bash
# Force Ruff to format even when Black is in the run
lintro format --tool-options "ruff:format=True"

# Force Ruff to include format-check during check
lintro check --tool-options "ruff:format_check=True"
```

### ⚠️ Limited/Missing Features

**Advanced Configuration:**

- ⚠️ **Per-file ignores and excludes at runtime**: Prefer config files; proposed CLI
  pass-throughs include `per_file_ignores`, `extend_exclude`, `force_exclude`,
  `respect_gitignore`.
- ❌ **Custom rule definitions**: Not supported by Lintro wrappers (upstream feature set
  only).
- ⚠️ **Config path/isolated**: Proposed `ruff:config=...`, `ruff:isolated=True` for
  ad-hoc runs.
- ⚠️ **Output controls**: `quiet`, `statistics`, `preview` useful for UX; propose
  pass-throughs.

**Formatting Options:**

- ❌ **Detailed formatting options**: No access to `--skip-magic-trailing-comma`,
  `--preview`
- ❌ **Formatting configuration**: Limited line length and target version control
- **Format-only fixes:** Set `ruff:lint_fix=False,ruff:format=True` to skip the lint-fix
  pass. Check mode still follows its separate lint/check workflow.

**Advanced Features:**

- ❌ **Watch mode**: No native watch-flag pass-through; repeated checks use
  `lintro watch`
- ❌ **Cache control**: No access to `--cache-dir`, `--no-cache`
- ❌ **Statistics**: No access to `--statistics` output
- ❌ **Exit codes**: Limited exit code customization

**Performance Options:**

- ❌ **Parallel processing**: No access to Ruff's built-in parallelization
- ❌ **Memory optimization**: No control over memory usage settings

### 🚀 Enhancements

**Unified Interface:**

- ✅ **Consistent API**: Same interface as other linting tools (`check()`, `fix()`,
  `set_options()`)
- ✅ **Structured output**: Issues formatted as standardized `Issue` objects
- ✅ **Combined operations**: Runs both linting and formatting in single operation

### Unimplemented option ideas {#-proposed-runtime-pass-throughs}

The following spellings are proposals, **not working commands in this release**. Use
native configuration or the native CLI for the corresponding behavior.

- `--tool-options ruff:config=path/to/ruff.toml`
- `--tool-options ruff:per_file_ignores=A.py:E501|pkg/B.py:I001`
- `--tool-options ruff:extend_exclude=build|dist,ruff:force_exclude=True`
- `--tool-options ruff:respect_gitignore=True`
- `--tool-options ruff:quiet=True,ruff:statistics=True,ruff:preview=True`
- ✅ **Integration ready**: Seamless integration with other tools in linting pipeline

**Enhanced Error Processing:**

- ✅ **Issue normalization**: Converts Ruff output to standard Issue format:

  ```python
  Issue(
      file_path=issue.file,
      line_number=issue.line,
      column_number=issue.column,
      error_code=issue.code,
      message=issue.message,
      severity="error"
  )
  ```

**Smart Fix Handling:**

- ✅ **Fix reporting**: Shows number of issues fixed vs remaining
- ✅ **Unsafe fix detection**: Warns about issues that could be fixed with unsafe fixes
- ✅ **Fix-only mode**: Option to only apply fixes without reporting remaining issues
- ✅ **Format integration**: Automatic formatting when enabled

**File Management:**

- ✅ **Extension filtering**: Automatic Python file detection
- ✅ **Batch processing**: Efficient handling of multiple files
- ✅ **Error aggregation**: Collects all issues across files

## Usage Comparison

### Core Ruff

```bash
# Basic linting
ruff check src/

# With specific rules
ruff check --select E501,B006 src/

# Auto-fixing
ruff check --fix src/

# Formatting
ruff format src/

# Combined lint and format
ruff check --fix src/ && ruff format src/
```

### Lintro Wrapper

```bash
# Check Python lint and formatting through Lintro
lintro check src/ --tools ruff

# Apply Ruff fixes and formatting
lintro format src/ --tools ruff

# Override selected rules and line length
lintro check src/ --tools ruff --tool-options "ruff:select=E501|B006,ruff:line_length=88"
```

## Configuration Strategy

### Core Tool Configuration

Ruff uses configuration files:

- `pyproject.toml` `[tool.ruff]` section
- `ruff.toml`
- `.ruff.toml`

### Lintro Approach

The wrapper supports both configuration files and runtime options:

- **Configuration files**: Primary configuration method
- **Runtime options**: Override specific settings via `set_options()`
- **Combined approach**: Configuration files provide defaults, runtime options override

### First-party import detection

Ruff's import sorting (`I001`) classifies each import as standard library, third-party
or first-party. It decides first-party by resolving the module against its
[`src`](https://docs.astral.sh/ruff/settings/#src) search path. Without a `[tool.ruff]`
table that path is relative to the directory Ruff runs in, so plain `ruff check` can
give a different verdict depending on where you start it.

Lintro does not run Ruff in the directory you start it from. It runs every Ruff
subprocess (the check pass, the format pass and the `lintro format` fix path) from a
**project root** worked out from the files being linted. Lintro takes the common
ancestor of those files and walks up to the nearest directory with `.git`,
`pyproject.toml` or another project marker. So `lintro check scripts/ci` and
`lintro format scripts/ci` give the same result whether you start them at the repository
root or inside `scripts/ci/`, and `lintro check` and `lintro format` agree with each
other.

The root still depends on **which paths you pass**, because they decide the files and so
their common ancestor. For example, take a repository with `.git` at the top and a
nested `pkg_c/pyproject.toml` with no `[tool.ruff]` table:

- `lintro check .` also covers files outside `pkg_c`, so it anchors at the outer
  repository. A module at `pkg_c/helper.py` is not a top-level module from there.
- `lintro check pkg_c` anchors at `pkg_c`, where `helper` is first-party. The same
  import in `pkg_c/sub/use.py` can therefore get a different `I001` verdict.

A run that covers several projects uses the root they have in common, which is the outer
one. Lintro does not group files by project.

To fix the classification regardless of which paths you pass, set `src` explicitly. Ruff
resolves it against the config file that declares it, and Lintro does not override it.
For example, to treat a helper in `scripts/ci/` that a sibling script imports as
first-party:

```toml
[tool.ruff]
src = ["scripts/ci"]
```

## Rule Categories

Lintro preserves all Ruff rule categories:

| Category                  | Prefix | Description                        |
| ------------------------- | ------ | ---------------------------------- |
| **Pyflakes**              | F      | Logical errors and undefined names |
| **pycodestyle**           | E, W   | PEP 8 style violations             |
| **isort**                 | I      | Import sorting issues              |
| **pep8-naming**           | N      | Naming conventions (PEP 8)         |
| **pydocstyle**            | D      | Docstring style violations         |
| **pyupgrade**             | UP     | Python upgrade suggestions         |
| **flake8-annotations**    | ANN    | Type annotation requirements       |
| **flake8-bugbear**        | B      | Bug detection and complexity       |
| **flake8-comprehensions** | C4     | Comprehension improvements         |
| **flake8-simplify**       | SIM    | Code simplification suggestions    |

## Recommendations

### When to Use Core Ruff

- Need maximum configuration flexibility
- Need Ruff’s native JUnit XML output; Lintro’s own output already includes SARIF
- Need native Ruff watch behavior instead of Lintro’s repeated-check orchestration
- Need native-only diagnostic and introspection flags
- Require detailed statistics and caching control

### When to Use Lintro Wrapper

- Part of multi-tool linting pipeline
- Need consistent issue reporting across tools
- Want Python object integration
- Require combined linting and formatting
- Need standardized error handling

## Limitations and Workarounds

### Limited Runtime Configuration

**Problem**: Cannot customize all Ruff options at runtime **Workaround**: Use
configuration files for complex setups, runtime options for overrides

### No Custom Rules

**Problem**: Cannot define custom linting rules **Workaround**: Use Ruff's built-in rule
set

### Native parsing and public output {#limited-output-formats}

The Ruff integration reads native JSON into structured findings. This is the internal
parser contract. Lintro's public output includes grid, JSON, Markdown, CSV and SARIF
formats; select them with `--output-format`.

## Current orchestration and future work {#future-enhancement-opportunities}

Watch is already available through `lintro watch --tools ruff`. It is Lintro's watch
command, rather than a missing Ruff integration feature. The remaining items below are
proposals, not promised or shipped capabilities.

1. **Configuration Pass-through**: Support for all Ruff CLI options
2. **Custom Rules**: Integration with Ruff's rule system
3. **Performance**: Leverage Ruff's parallel processing
4. **Statistics**: Detailed performance and issue statistics
5. **Cache Integration**: Intelligent caching for faster subsequent runs
