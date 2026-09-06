# Creating Lintro Plugins

This guide explains how to create **third-party tool plugins** for Lintro. A plugin is a
normal Python package published to PyPI (or installed from anywhere) that adds one or
more tools to Lintro without any change to the Lintro core repository.

## Overview

Lintro uses a plugin architecture that lets you add support for new linting and
formatting tools. Built-in tools live in `lintro/tools/definitions/`; external tools
ship in their own distributions and are discovered automatically at startup via Python
entry points in the **`lintro.tools`** group.

An external plugin gets the exact same lifecycle as a built-in tool: config injection,
file discovery, subprocess execution, output normalization, and per-invocation execution
isolation.

> **Security note:** Installing a Lintro plugin means running its code. A plugin is
> ordinary Python and executes with your privileges the moment it is discovered. This is
> the same trust model as installing any `pip` package — only install plugins from
> sources you trust.

## Entry Point Registration

Register your plugin in the installing package's `pyproject.toml`. The entry-point
**name** is only a label; the actual tool name comes from the plugin's
`ToolDefinition.name`.

```toml
[project.entry-points."lintro.tools"]
my-tool = "my_package.plugin:MyToolPlugin"
```

The value points to the plugin **class** (`module:ClassName`).

Plugins packaged against the previously documented `lintro.plugins` group are still
discovered for backward compatibility, with a deprecation warning logged at startup.
Update existing packages to the `lintro.tools` group.

## Plugin API Version

The plugin-facing contract is versioned so that core refactors never silently break — or
crash — an installed plugin. The current version is exposed as
`lintro.plugins.LINTRO_PLUGIN_API_VERSION`.

Declare the version your plugin targets as a class attribute:

```python
from lintro.plugins import LINTRO_PLUGIN_API_VERSION


class MyToolPlugin(BaseToolPlugin):
    LINTRO_PLUGIN_API_VERSION = LINTRO_PLUGIN_API_VERSION
    ...
```

At load time Lintro compares this against its own version. A plugin built for an
incompatible major version is **logged and skipped**, never loaded. Declaring the
attribute is optional (an undeclared plugin is assumed compatible) but strongly
recommended for forward safety.

## Failure Isolation

Discovery is fully fault-tolerant. A plugin that fails to import, is malformed (missing
the required methods), declares an incompatible API version, collides with a built-in
tool name, or raises on construction is **logged as a warning and skipped**. One broken
plugin never crashes Lintro and never blocks discovery of the other plugins. Built-in
tools always win a name collision, so an external plugin can never silently shadow a
curated core tool.

## Seeing Where a Tool Came From

Run `lintro list-tools` to see every registered tool with an **Origin** column:
`builtin` for core tools, or the distribution/package name for a third-party plugin. The
same field is present in `lintro list-tools --json` as `"origin"`.

## Plugin Implementation

Create a plugin class that inherits from `BaseToolPlugin`:

> **Do not use `@register_tool` in a third-party plugin.** That decorator is for
> built-in tools, which are imported eagerly. External plugins are registered by the
> entry-point loader — decorating would attempt a second (duplicate) registration.

```python
from dataclasses import dataclass

from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.plugins import LINTRO_PLUGIN_API_VERSION
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition


@dataclass
class MyToolPlugin(BaseToolPlugin):
    """My custom linting tool plugin."""

    # Declare the plugin API version this plugin was built against.
    LINTRO_PLUGIN_API_VERSION = LINTRO_PLUGIN_API_VERSION

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition."""
        return ToolDefinition(
            name="my-tool",
            description="My custom linting tool",
            can_fix=False,  # Set to True if tool can auto-fix issues
            tool_type=ToolType.LINTER,  # LINTER, FORMATTER, or SECURITY
            file_patterns=["*.py"],  # Glob patterns for files to check
            priority=50,  # Execution priority (higher = runs earlier)
            conflicts_with=[],  # Names of conflicting tools
            native_configs=["pyproject.toml", ".mytool.yaml"],  # Config files
            version_command=["my-tool", "--version"],  # Command to get version
            min_version="1.0.0",  # Minimum supported version
            default_options={
                "timeout": 30,
                # Add tool-specific options here
            },
            default_timeout=30,
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with the tool.

        Args:
            paths: List of file or directory paths to check.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with check results.
        """
        # Use prepare() for common setup (version check, file discovery). It
        # returns the finished ToolResult when execution must stop early.
        ctx = self.prepare(paths, options)
        if isinstance(ctx, ToolResult):
            return ctx

        # Build and run the tool command
        cmd = ["my-tool", "check"] + ctx.rel_files
        success, output = self._run_subprocess(cmd, timeout=ctx.timeout, cwd=ctx.cwd)

        # Parse output into issues (create a parser in lintro/parsers/)
        issues = parse_my_tool_output(output)

        return ToolResult(
            name=self.definition.name,
            # success=True means the check passed (tool ran AND no issues found)
            # If you want success to only reflect tool execution, use just `success`
            success=success and len(issues) == 0,
            output=output if not success else None,
            issues_count=len(issues),
            issues=issues,
        )

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Fix issues in files (optional - only if can_fix=True).

        Args:
            paths: List of file or directory paths to fix.
            options: Runtime options that override defaults.

        Returns:
            ToolResult with fix results.
        """
        # Similar to check() but runs fix command
        raise NotImplementedError("This tool does not support auto-fixing.")
```

## Key Components

### ToolDefinition

The `ToolDefinition` dataclass defines your tool's metadata:

| Field             | Type        | Description                        |
| ----------------- | ----------- | ---------------------------------- |
| `name`            | `str`       | Unique tool identifier             |
| `description`     | `str`       | Brief description                  |
| `can_fix`         | `bool`      | Whether tool supports auto-fixing  |
| `tool_type`       | `ToolType`  | LINTER, FORMATTER, or SECURITY     |
| `file_patterns`   | `list[str]` | Glob patterns for target files     |
| `priority`        | `int`       | Execution order (higher = earlier) |
| `conflicts_with`  | `list[str]` | Names of conflicting tools         |
| `native_configs`  | `list[str]` | Config file names                  |
| `version_command` | `list[str]` | Command to check version           |
| `min_version`     | `str`       | Minimum supported version          |
| `default_options` | `dict`      | Default tool options               |
| `default_timeout` | `int`       | Default timeout in seconds         |

### ToolResult

The `ToolResult` dataclass represents execution results:

| Field          | Type                      | Description                  |
| -------------- | ------------------------- | ---------------------------- |
| `name`         | `str`                     | Tool name                    |
| `success`      | `bool`                    | Whether execution succeeded  |
| `output`       | `str \| None`             | Raw output (errors/warnings) |
| `issues_count` | `int`                     | Number of issues found       |
| `issues`       | `list[BaseIssue] \| None` | Parsed issues                |

### BaseToolPlugin Helpers

The `BaseToolPlugin` base class provides useful methods:

- `prepare(paths, options)` - Common setup (version check, file discovery); returns an
  `ExecutionContext`, or a `ToolResult` to return as-is when execution stops early
- `_run_subprocess(cmd, timeout, cwd)` - Run tool command safely
- `_get_executable_command(tool_name)` - Get command with proper path
- `_discover_files(paths, patterns)` - Find files matching patterns

### Per-file check runs

Tools that lint one file at a time should not write their own loop either. Call
`lintro.tools.core.check_runner.run_per_file_check()` from `check()` with the command
builder and the parser; it runs the command per file, turns a timeout or an OS error
into a per-file failure, and aggregates every issue into a single `ToolResult`.

```python
from lintro.tools.core.check_runner import PerFileCheckPolicy, run_per_file_check


def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
    ctx = self.prepare(paths, options)
    if isinstance(ctx, ToolResult):
        return ctx
    return run_per_file_check(
        ctx,
        plugin=self,
        command=lambda f: [*self._build_command(), str(f)],
        parse=lambda output: parse_mytool_output(output=output),
    )
```

`PerFileCheckPolicy` is optional and carries the only two classification choices the
runner cannot infer: `issues_imply_failure` marks a file unsuccessful when the parser
found issues even though the command exited zero, and `failure_message` records an
execution error when the command exits non-zero _without_ producing a parseable issue
(leave it `None` when the tool's exit status is a reliable verdict on its own). `label`
renames the progress bar.

### Per-file fix runs

Tools that fix one file at a time should not write their own loop. Call
`lintro.tools.core.fix_runner.run_per_file_fix()` from `fix()` with the two command
builders, the parser and a `PerFileFixPolicy`; it runs check -> fix -> optional
verification per file and aggregates the initial/fixed/remaining counts into a single
`ToolResult`.

```python
from lintro.tools.core.fix_runner import (
    PerFileFixPolicy,
    VerifyMode,
    run_per_file_fix,
)


def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
    ctx = self.prepare(paths, options, no_files_message="No files to format.")
    if isinstance(ctx, ToolResult):
        return ctx
    return run_per_file_fix(
        ctx,
        plugin=self,
        check_command=self._diff_command,
        fix_command=self._write_command,
        parse=lambda output: parse_mytool_output(output=output),
        policy=PerFileFixPolicy(
            check_failure_message="mytool check failed before fix",
            verify=VerifyMode.AFTER_SUCCESS,
            verify_failure_message="mytool recheck failed",
        ),
    )
```

`VerifyMode` picks how surviving issues are counted: `NEVER` skips re-checking entirely
and trusts the fix command's exit status, `AFTER_SUCCESS` re-checks only after a clean
fix, and `ALWAYS` re-checks even when the fix exits non-zero (for tools that apply fixes
partially).

Both runners classify a single check-style invocation through the same
`check_runner.check_one_file()` step, so a timeout, an execution error and a parser
failure are reported identically whether the tool is checking or fixing. A non-zero exit
that produced no parseable issue is the one outcome the two sides can differ on: it
becomes an execution error only when a message is configured, and `failure_message` is
optional on the check side while `check_failure_message` is mandatory on the fix side.

### Batch check and fix runs

Tools that hand their whole file list to one invocation use
`lintro.tools.core.batch_runner` instead. `run_batch_check()` runs the command once,
parses it and classifies the outcome; `run_batch_fix()` runs check -> fix -> re-check
and scores the difference.

```python
from lintro.tools.core.batch_runner import (
    BatchCheckPolicy,
    BatchCommands,
    BatchFixPolicy,
    BatchSuccess,
    run_batch_check,
    run_batch_fix,
)


def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
    ctx = self.prepare(paths, options)
    if isinstance(ctx, ToolResult):
        return ctx
    return run_batch_check(
        ctx,
        plugin=self,
        cmd=[*self._build_command(), *ctx.rel_files],
        parse=lambda output: parse_mytool_output(output=output),
        policy=BatchCheckPolicy(
            success=BatchSuccess.ISSUES_ONLY,
            report_cwd=True,
        ),
        cwd=ctx.cwd,
    )
```

`BatchCheckPolicy` carries the two classification choices. `BatchSuccess` says what the
verdict is derived from: `ISSUES_ONLY` for a tool that exits non-zero purely to report
findings, `EXIT_STATUS` for one whose exit code is the whole verdict, and
`EXIT_AND_ISSUES` (the default) when both must be clean. `BatchOutput` says when the raw
output is surfaced — `NEVER`, `ON_FAILURE` (the default),
`ON_EXIT_FAILURE_WITHOUT_ISSUES` for tools where unparseable output is the only sign of
a compilation or config error, and `ON_ISSUES_OR_EXIT_FAILURE`. Both policies also carry
`tool_name` (the name timeout messages use when it differs from the registered one) and
`report_cwd` (whether the working directory is recorded on the `ToolResult`, which tools
emitting issue paths relative to it need).

`run_batch_fix()` takes both fully built command lines as a
`BatchCommands(check=..., fix=...)` bundle — the check command runs twice, once before
the fix and once to score it — plus a `BatchFixPolicy` holding the wording of the
summary (`fixed_label`, `all_fixed_message`, `verbose_output_label`) and two reporting
switches: `report_initial_issues` prefixes `ToolResult.issues` with the pre-fix set for
tools that render a two-table view, and `always_report_initial_issues` passes an empty
list rather than `None` when nothing was detected.

Both entry points take `on_timeout` and `on_error` hooks. Leave `on_timeout` out to get
the standard `batch_timeout_result()` / `batch_fix_timeout_result()` shape, and pass it
only when the tool has its own timeout message. Leave `on_error` out to let a launch
failure propagate. Those two result builders are exported on their own, so a tool whose
middle section is bespoke — a missing-config skip, a per-module loop, a dependency-error
hint — can still share the timeout and result-construction shapes without adopting the
whole runner.

### Ecosystem preconditions

Two families of tools cannot run from the directory lintro discovered their files in.
`lintro.tools.core` carries the shared preconditions so each definition states the
requirement rather than reimplementing it.

**Cargo workspaces.** `cargo clippy`, `cargo fmt` and `cargo deny` must be launched from
a directory that owns a `Cargo.toml`, but lintro hands the plugin `*.rs` paths.
`find_cargo_root()` walks every path upward to the nearest manifest and reconciles the
results: one package wins outright, several fall back to their common ancestor and only
if that ancestor is itself a workspace root.

```python
from lintro.tools.core.cargo import find_cargo_root

cargo_root = find_cargo_root(ctx.files, tool_label="rustfmt")
if cargo_root is None:
    return ToolResult(name=self.definition.name, success=True, output="...", issues_count=0)
```

`tool_label` is optional and affects logging only: pass it to explain an unresolvable
multi-package layout to the user, leave it out to fail silently and let the caller emit
its own skip message.

**Node dependencies.** `astro-check` and `svelte-check` ship inside the project they
lint, so neither exists until `node_modules` is populated. `ensure_node_modules()` makes
the three-way decision — skip on a read-only directory, install when the user passed
`--auto-install`, otherwise skip with the instruction to pass it — and returns the skip
`ToolResult` for the caller to return, or `None` when the tool may proceed.

```python
from lintro.tools.core.node_modules import ensure_node_modules

skip_result = ensure_node_modules(
    plugin=self,
    cwd=cwd_path,
    auto_install=bool(merged_options.get("auto_install", False)),
    tool_label="astro-check",
)
if skip_result is not None:
    return skip_result
```

`tool_label` is the human-facing tool name; it names the tool in both the log lines and
the `Skipping <tool>: ...` message, so it is the spelling users see rather than the
registered snake_case name.

### Execution Isolation (important for correctness)

Registered plugin instances are process-wide singletons with mutable option state.
Lintro's parallel executor never mutates that singleton directly — it takes a private
per-invocation copy via `copy_for_execution()` so concurrent runs cannot clobber each
other's options. Subclassing `BaseToolPlugin` gives you this for free.

If your plugin adds its **own** mutable option state (for example a config dataclass
that `set_options()` mutates), you must isolate it too by overriding
`_isolate_execution_state()` (deep-copy it onto the execution copy) and
`_reset_execution_state()` (restore defaults). Otherwise concurrent invocations will
race on that shared state. Read-mostly caches may stay shared.

## Creating a Parser

Create a parser module to convert tool output into structured issues:

```python
# lintro/parsers/my_tool/my_tool_parser.py
import re

from lintro.parsers.base_issue import BaseIssue


class MyToolIssue(BaseIssue):
    """Issue class for my-tool output."""

    pass  # Inherits all fields from BaseIssue


def parse_my_tool_output(output: str) -> list[MyToolIssue]:
    """Parse my-tool output into issues.

    Assumes output format: filename:line:column: level: message [CODE]

    Args:
        output: Raw tool output.

    Returns:
        List of parsed issues.
    """
    issues: list[MyToolIssue] = []

    if not output.strip():
        return issues

    # Pattern for: file:line:col: level: message [CODE]
    pattern = re.compile(
        r"^(.+?):(\d+):(\d+):\s*(error|warning|info):\s*(.+?)\s*\[(\w+)\]$"
    )

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        match = pattern.match(line)
        if match:
            file, line_num, col, level, message, code = match.groups()
            issues.append(
                MyToolIssue(
                    file=file,
                    line=int(line_num),
                    column=int(col),
                    message=message,
                    code=code,
                    level=level,
                )
            )

    return issues
```

## Packaging Checklist

A minimal third-party plugin distribution contains:

- `my_package/plugin.py` — a `BaseToolPlugin` subclass (see above).
- `my_package/parser.py` — an output parser (see below).
- `pyproject.toml` — with a `[project.entry-points."lintro.tools"]` entry pointing at
  your plugin class, and `lintro` as a dependency.

## Testing Your Plugin

1. Install your plugin package (`pip install .` / `uv pip install .`).
2. Run `lintro list-tools` and confirm your tool appears with your package name in the
   **Origin** column.
3. Run `lintro check --tools my-tool path/to/files` to test.

## Example Plugins

See the built-in plugins in `lintro/tools/definitions/` for complete examples:

- `ruff.py` - Python linter with fix support
- `bandit.py` - Security scanner (no fix)
- `prettier.py` - JavaScript/TypeScript formatter
- `hadolint.py` - Dockerfile linter
