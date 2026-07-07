# Creating Lintro Plugins

This guide explains how to create **third-party tool plugins** for Lintro. A plugin
is a normal Python package published to PyPI (or installed from anywhere) that adds
one or more tools to Lintro without any change to the Lintro core repository.

## Overview

Lintro uses a plugin architecture that lets you add support for new linting and
formatting tools. Built-in tools live in `lintro/tools/definitions/`; external tools
ship in their own distributions and are discovered automatically at startup via
Python entry points in the **`lintro.tools`** group.

An external plugin gets the exact same lifecycle as a built-in tool: config
injection, file discovery, subprocess execution, output normalization, and
per-invocation execution isolation.

> **Security note:** Installing a Lintro plugin means running its code. A plugin is
> ordinary Python and executes with your privileges the moment it is discovered.
> This is the same trust model as installing any `pip` package — only install plugins
> from sources you trust.

## Entry Point Registration

Register your plugin in the installing package's `pyproject.toml`. The entry-point
**name** is only a label; the actual tool name comes from the plugin's
`ToolDefinition.name`.

```toml
[project.entry-points."lintro.tools"]
my-tool = "my_package.plugin:MyToolPlugin"
```

The value points to the plugin **class** (`module:ClassName`).

Plugins packaged against the previously documented `lintro.plugins` group are
still discovered for backward compatibility, with a deprecation warning logged
at startup. Update existing packages to the `lintro.tools` group.

## Plugin API Version

The plugin-facing contract is versioned so that core refactors never silently break
— or crash — an installed plugin. The current version is exposed as
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

Discovery is fully fault-tolerant. A plugin that fails to import, is malformed
(missing the required methods), declares an incompatible API version, collides with
a built-in tool name, or raises on construction is **logged as a warning and
skipped**. One broken plugin never crashes Lintro and never blocks discovery of the
other plugins. Built-in tools always win a name collision, so an external plugin can
never silently shadow a curated core tool.

## Seeing Where a Tool Came From

Run `lintro list-tools` to see every registered tool with an **Origin** column:
`builtin` for core tools, or the distribution/package name for a third-party plugin.
The same field is present in `lintro list-tools --json` as `"origin"`.

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
        # Use _prepare_execution for common setup (version check, file discovery)
        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result

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

- `_prepare_execution(paths, options)` - Common setup (version check, file discovery)
- `_run_subprocess(cmd, timeout, cwd)` - Run tool command safely
- `_get_executable_command(tool_name)` - Get command with proper path
- `_discover_files(paths, patterns)` - Find files matching patterns

### Execution Isolation (important for correctness)

Registered plugin instances are process-wide singletons with mutable option state.
Lintro's parallel executor never mutates that singleton directly — it takes a
private per-invocation copy via `copy_for_execution()` so concurrent runs cannot
clobber each other's options. Subclassing `BaseToolPlugin` gives you this for free.

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
- `pyproject.toml` — with a `[project.entry-points."lintro.tools"]` entry pointing
  at your plugin class, and `lintro` as a dependency.

## Testing Your Plugin

1. Install your plugin package (`pip install .` / `uv pip install .`).
2. Run `lintro list-tools` and confirm your tool appears with your package name in
   the **Origin** column.
3. Run `lintro check --tools my-tool path/to/files` to test.

## Example Plugins

See the built-in plugins in `lintro/tools/definitions/` for complete examples:

- `ruff.py` - Python linter with fix support
- `bandit.py` - Security scanner (no fix)
- `prettier.py` - JavaScript/TypeScript formatter
- `hadolint.py` - Dockerfile linter
