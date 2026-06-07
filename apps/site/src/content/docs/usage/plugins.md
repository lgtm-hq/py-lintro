---
title: 'plugins'
description: 'This guide explains how to create external plugins for Lintro.'
category: usage
order: 80
navGroup: extend
---

# Creating Lintro Plugins

This guide explains how to create external plugins for Lintro.

## Overview

Lintro uses a plugin architecture that allows you to add support for new linting and
formatting tools. Plugins are discovered automatically via Python entry points.

## Entry Point Registration

Register your plugin in `pyproject.toml`:

```toml
[project.entry-points."lintro.plugins"]
my-tool = "my_package.plugin:MyToolPlugin"
```

## Plugin Implementation

Create a plugin class that inherits from `BaseToolPlugin`:

```python
from dataclasses import dataclass

from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool


@register_tool
@dataclass
class MyToolPlugin(BaseToolPlugin):
    """My custom linting tool plugin."""

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

## Testing Your Plugin

1. Install your plugin package
2. Run `lintro tools` to verify your tool is discovered
3. Run `lintro check --tool my-tool path/to/files` to test

## Example Plugins

See the built-in plugins in `lintro/tools/definitions/` for complete examples:

- `ruff.py` - Python linter with fix support
- `bandit.py` - Security scanner (no fix)
- `prettier.py` - JavaScript/TypeScript formatter
- `hadolint.py` - Dockerfile linter
