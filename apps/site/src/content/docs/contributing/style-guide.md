---
title: 'style guide'
description:
  'This document outlines the coding standards and best practices for the Lintro
  project.'
category: contributing
order: 20
navGroup: standards
---

# Lintro Style Guide

This document outlines the coding standards and best practices for the Lintro project.

## Python Code Style

### General Guidelines

- Follow [PEP 8](https://peps.python.org/pep-0008/) for code style
- Use [pydoclint](https://github.com/jsh9/pydoclint) for docstring argument checking
- Use [Oxfmt](https://github.com/oxc-project/oxc) for JavaScript/TypeScript formatting
- Use [Prettier](https://prettier.io/) for CSS, HTML, JSON, YAML, and Markdown
  formatting

### Type Hints

- All functions and methods must include type hints
- All classes must include type hints for attributes
- Prefer using pipe operator (`|`) over `Optional` for optional types
- Avoid importing from `typing` for built-in types like `list`, `dict`, etc.
- Use `Any` sparingly and only when absolutely necessary

```python
# ❌ Bad
from typing import Dict, List, Optional

def process_data(data: Optional[List[Dict[str, str]]] = None) -> Optional[Dict[str, str]]:
    pass

# ✅ Good
def process_data(data: list[dict[str, str]] | None = None) -> dict[str, str] | None:
    pass
```

### Docstrings

- All modules, classes, functions, and methods must have docstrings
- Use Google-style docstrings
- Include parameter descriptions, return value descriptions, and raised exceptions

```python
def calculate_total(items: list[dict[str, float]]) -> float:
    """
    Calculate the total value of all items.

    Args:
        items: List of items with their prices

    Returns:
        Total value of all items

    Raises:
        ValueError: If any item has a negative price
    """
    pass
```

### Function and Method Definitions

- For function/method declarations with more than 1 parameter, use trailing commas and
  format with Ruff
- Same applies to function/method calls with multiple arguments

```python
# Function definition with multiple parameters
def complex_function(
    param1: str,
    param2: int,
    param3: bool = False,
) -> str:
    pass

# Function call with multiple arguments
result = complex_function(
    "value1",
    42,
    True,
)
```

### Imports

- Use `ruff` to automatically sort imports
- Group imports in the following order:
  1. Standard library imports
  2. Third-party imports
  3. Local application imports
- Use absolute imports
- Use explicit imports
- Use `from __future__ import annotations` in Python files

```python
# Standard library imports
import os
import sys
from pathlib import Path

# Third-party imports
import click
from tabulate import tabulate

# Local imports
from lintro.tools import Tool
from lintro.utils import format_output
```

### Error Handling

- Use specific exception types rather than catching all exceptions
- Include meaningful error messages
- Use context managers (`with` statements) for resource management

```python
# ❌ Bad
try:
    process_file(filename)
except Exception as e:
    print(f"Error: {e}")

# ✅ Good
try:
    process_file(filename)
except FileNotFoundError:
    print(f"File {filename} not found")
except PermissionError:
    print(f"Permission denied when accessing {filename}")
```

### Variable Naming

- Use descriptive variable names
- Use `snake_case` for variables, functions, and methods
- Use `PascalCase` for classes
- Use `UPPER_CASE` for constants

```python
# Constants
MAX_RETRY_COUNT = 5
DEFAULT_TIMEOUT = 30

# Variables
user_input = input("Enter your name: ")
file_path = "/path/to/file.txt"

# Functions
def calculate_total(items):
    pass

# Classes
class FileProcessor:
    pass
```

## Project Structure

### Directory Layout

- Keep related files together
- Use meaningful directory names
- Separate code, tests, and documentation

```text
lintro/
├── __init__.py
├── __main__.py
├── cli.py
├── cli_utils/
│   ├── __init__.py
│   └── commands/
│       ├── check.py
│       ├── config.py
│       ├── format.py
│       ├── init.py
│       ├── list_tools.py
│       ├── test.py
│       └── versions.py
├── config/
│   ├── config_loader.py
│   ├── lintro_config.py
│   └── tool_config_generator.py
├── enums/
│   ├── action.py
│   ├── tool_name.py
│   └── tool_type.py
├── exceptions/
│   └── errors.py
├── formatters/
│   ├── core/
│   ├── styles/
│   └── tools/
├── models/
│   └── core/
├── parsers/
│   ├── actionlint/
│   ├── bandit/
│   ├── black/
│   ├── clippy/
│   ├── pydoclint/
│   ├── hadolint/
│   ├── markdownlint/
│   ├── mypy/
│   ├── oxfmt/
│   ├── oxlint/
│   ├── prettier/
│   ├── pytest/
│   ├── ruff/
│   └── yamllint/
├── tools/
│   ├── core/
│   ├── definitions/
│   │   ├── actionlint.py
│   │   ├── bandit.py
│   │   ├── black.py
│   │   ├── clippy.py
│   │   ├── pydoclint.py
│   │   ├── hadolint.py
│   │   ├── markdownlint.py
│   │   ├── mypy.py
│   │   ├── oxfmt.py
│   │   ├── oxlint.py
│   │   ├── prettier.py
│   │   ├── pytest.py
│   │   ├── ruff.py
│   │   └── yamllint.py
│   └── implementations/
│       ├── pytest/
│       └── ruff/
└── utils/
    ├── console_logger.py
    ├── logger_setup.py
    ├── output_formatting.py
    ├── result_formatters.py
    ├── summary_tables.py
    └── tool_executor.py
tests/
├── cli/
├── config/
├── formatters/
├── integration/
├── unit/
└── utils/
```

### File Organization

- Each module should have a single, well-defined responsibility
- Keep files to a reasonable size (< 500 lines if possible)
- Use meaningful file names that reflect their contents

## Commit Messages

- Use the imperative mood in commit messages
- Start with a prefix indicating the type of change
- Include a brief summary of changes in the first line
- Optionally include a more detailed description in subsequent lines

Prefixes:

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code changes that neither fix bugs nor add features
- `perf`: Performance improvements
- `test`: Adding or modifying tests
- `build`: Changes to build system or dependencies
- `ci`: Changes to CI configuration

```text
feat: add support for mypy integration

- Add MyPyTool class
- Update CLI to include mypy in available tools
- Add tests for mypy integration
```

## Testing

- Write tests for all new features and bug fixes
- Aim for high test coverage (>= 90%)
- Use pytest for testing
- Use fixtures to reduce test code duplication
- Use meaningful test names that describe what is being tested

```python
def test_pydoclint_tool_checks_docstrings_correctly():
    # Test implementation
    pass

def test_prettier_tool_formats_code_correctly():
    # Test implementation
    pass
```

## Documentation

- Keep documentation up-to-date with code changes
- Document all public APIs
- Include examples in documentation
- Use Markdown for documentation files

## Tool Configuration

When adding new tools to Lintro, ensure they follow these guidelines:

1. Inherit from `BaseToolPlugin` in `lintro.plugins.base`
2. Use `@register_tool` decorator from `lintro.plugins.registry`
3. Implement `definition` property returning `ToolDefinition`
4. Implement `check()` and optionally `fix()` methods
5. Create a parser in `lintro.parsers.{tool_name}/` to parse tool output
6. Use unified formatter in `lintro.formatters.unified`
7. Include comprehensive docstrings
8. Add appropriate tests in `tests/unit/tools/` and `tests/integration/`
9. Update documentation

Example tool configuration:

```python
from dataclasses import dataclass

from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool

@register_tool
@dataclass
class ExamplePlugin(BaseToolPlugin):
    """Example tool integration."""

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition."""
        return ToolDefinition(
            name="example",
            description="Example tool for demonstration",
            can_fix=False,
            tool_type=ToolType.LINTER,
            file_patterns=["*.py"],
            priority=60,
            conflicts_with=[],
            native_configs=["pyproject.toml"],
            version_command=["example", "--version"],
            min_version="1.0.0",
            default_options={},
            default_timeout=30,
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files with example tool."""
        # Implementation here
        pass
```

## Code Review

When reviewing code, check for:

1. Adherence to this style guide
2. Correctness and completeness
3. Test coverage
4. Documentation
5. Performance considerations
6. Security considerations

## Continuous Integration

All code should pass the following checks before being merged:

1. All tests pass
2. Code is checked with Lintro
3. Test coverage meets minimum threshold

## Code Formatting

We use the Lintro tool for code formatting and linting:

### Python Code Formatting

1. Use Lintro for checking:

   ```bash
   lintro check [PATH]
   ```

2. Use Lintro for formatting:

   ```bash
   lintro format [PATH]
   ```

3. Format specific files:

   ```bash
   lintro format file1.py file2.py
   ```

4. Format with custom options:

   ```bash
   lintro format --tools ruff --core-options "ruff:--line-length=100" [PATH]
   ```

### Style Guide Project Structure

The current project structure follows a modular design with clear separation of
concerns:

```text
py-lintro/
├── lintro/                       # Main Python package
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                    # Main CLI entry point
│   ├── cli_utils/                # CLI command implementations
│   │   └── commands/
│   │       ├── check.py          # Check command
│   │       ├── format.py         # Format command
│   │       └── test.py          # Test command
│   ├── config/                   # Configuration management
│   │   ├── config_loader.py     # Loads .lintro-config.yaml
│   │   └── tool_config_generator.py
│   ├── tools/                    # Tool implementations
│   │   ├── core/                 # Base tool classes
│   │   └── implementations/     # Specific tool implementations
│   │       ├── tool_ruff.py
│   │       ├── tool_black.py
│   │       ├── tool_mypy.py
│   │       └── ...
│   ├── parsers/                  # Output parsers for each tool
│   │   ├── ruff/
│   │   ├── black/
│   │   └── ...
│   ├── formatters/              # Output formatters
│   │   ├── styles/               # Output styles (grid, markdown, etc.)
│   │   └── tools/                # Tool-specific formatters
│   └── utils/                    # Utility functions
│       ├── tool_executor.py      # Main execution logic
│       ├── console_logger.py     # Console output handling
│       └── logger_setup.py       # Loguru configuration
└── tests/
    ├── unit/                     # Unit tests
    ├── integration/              # Integration tests
    └── ...
```

## Formatter and Output Style Architecture

## Overview

Lintro supports flexible, extensible output formatting for all tools. This is achieved
by separating:

- **Table structure** (columns, extraction) per tool
- **Output style** (plain, markdown, etc.)

This allows:

- Each tool to define its own columns and row extraction logic
- Easy addition of new output styles (Markdown, HTML, JSON, etc.)
- Consistent, DRY, and testable formatting logic

---

## Key Components

### 1. TableDescriptor (per tool)

- Describes the columns and how to extract them from an issue object.
- Each tool provides its own TableDescriptor if needed.

```python
from lintro.formatters.base_formatter import TableDescriptor
from typing import List


class PydoclintTableDescriptor(TableDescriptor):
  def get_columns(self) -> List[str]:
    return ["File", "Line", "Code", "Message"]

  def get_row(self, issue) -> List[str]:
    return [issue.file, str(issue.line), issue.code, issue.message]
```

### 2. OutputStyle (per output format)

- Defines how to render a table (columns + rows) as a string.
- Implemented in `lintro/formatters/styles/`.

```python

from lintro.formatters.core.output_style import OutputStyle
from typing import List, Any


class MarkdownStyle(OutputStyle):
  def format(self, columns: List[str], rows: List[List[Any]]) -> str:
# ...
```

### 3. Tool Formatter

- Use the unified formatter for all tools.
- Example usage:

```python
from lintro.formatters.unified import format_issues

# Format issues from any tool using the unified formatter
formatted_output = format_issues(issues, output_format="plain")
```

---

## How to Add a New Output Style

1. Create a new class in `lintro/formatters/styles/` inheriting from `OutputStyle`.
2. Implement the `format(columns, rows)` method.
3. Register the new style in the tool's `STYLE_MAP`.

---

## How to Add/Change a Tool's Table Structure

1. Create a new `TableDescriptor` for the tool if needed.
2. Implement `get_columns()` and `get_row(issue)`.
3. Use this descriptor in the tool's formatter.

---

## CLI Integration

- The CLI can expose a `--output-style` flag to let users select the output style (e.g.,
  `plain`, `markdown`).
- The selected style is passed to the tool formatter, which uses the appropriate
  OutputStyle.

---

## Example Usage

```python
issues = parse_pydoclint_output(raw_output)
print(format_pydoclint_issues(issues, style="markdown"))
```

---

## Benefits

- Extensible: Add new styles or tool structures easily
- Consistent: All tools use the same formatting pipeline
- Testable: Each style and descriptor can be unit tested
- Flexible: Tools with different columns or special needs are supported
