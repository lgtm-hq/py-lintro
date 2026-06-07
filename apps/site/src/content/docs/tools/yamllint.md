---
title: 'yamllint'
description: ''
category: tools
order: 200
navGroup: config
---

# Yamllint Tool Analysis

## Overview

Yamllint is a linter for YAML files that checks for syntax errors, formatting issues,
and other YAML best practices. This analysis compares Lintro's wrapper implementation
with the core Yamllint tool.

## Core Tool Capabilities

Yamllint provides comprehensive YAML analysis including:

- **Syntax validation**: Detects YAML syntax errors and parsing issues
- **Style checking**: Enforces consistent formatting and indentation
- **Document structure**: Validates document structure and key ordering
- **Configuration options**: `--format`, `--config-file`, `--config-data`, `--strict`
- **Output formats**: parsable, standard, colored, github, auto
- **Rule customization**: Extensive rule configuration via config files
- **Error codes**: Specific codes for different violation types

## Lintro Implementation Analysis

### ✅ Preserved Features

**Core Functionality:**

- ✅ **Syntax validation**: Full preservation of YAML syntax checking
- ✅ **Style enforcement**: Supports all Yamllint style rules
- ✅ **Configuration files**: Respects `.yamllint`, `setup.cfg`, `pyproject.toml`
- ✅ **Error categorization**: Preserves Yamllint's error code system
- ✅ **File targeting**: Supports YAML file patterns (`*.yml`, `*.yaml`)
- ✅ **Error detection**: Captures syntax and style violations

**Command Execution:**

```python
# From tool_yamllint.py
cmd = ["yamllint"] + self.files
result = subprocess.run(cmd, capture_output=True, text=True)
```

**Configuration Options:**

- ✅ **Output format**: `format` parameter (parsable, standard, colored, github, auto)
- ✅ **Config file**: `config_file` parameter for custom configuration
- ✅ **Inline config**: `config_data` parameter for YAML configuration string
- ✅ **Strict mode**: `strict` parameter for treating warnings as errors
- ✅ **Relaxed mode**: `relaxed` parameter for relaxed configuration
- ✅ **No warnings**: `no_warnings` parameter to show only errors

### ⚠️ Limited/Missing Features

**Advanced Configuration:**

- ❌ **Runtime rule customization**: Cannot modify individual rules at runtime
- ❌ **Per-file configuration**: No support for file-specific rule overrides
- ❌ **Custom rule definitions**: No support for custom rule creation
- ❌ **Rule severity control**: Limited control over rule severity levels

**Output Customization:**

- ❌ **Detailed error context**: Limited access to detailed error explanations
- ❌ **Custom formatters**: Cannot use custom output formatters
- ❌ **Progress reporting**: No access to progress indicators for large files

**Advanced Features:**

- ❌ **Document validation**: Limited schema validation capabilities
- ❌ **Key ordering**: No runtime control over key ordering rules
- ❌ **Comment handling**: Limited control over comment-related rules

**Performance Options:**

- ❌ **Parallel processing**: No access to parallel file processing
- ❌ **Caching**: No access to incremental checking features

### 🚀 Enhancements

**Unified Interface:**

- ✅ **Consistent API**: Same interface as other linting tools (`check()`,
  `set_options()`)
- ✅ **Structured output**: Issues formatted as standardized `Issue` objects
- ✅ **Python integration**: Native Python object handling vs CLI parsing
- ✅ **Pipeline integration**: Seamless integration with other tools

**Enhanced Error Processing:**

- ✅ **Issue normalization**: Converts Yamllint output to standard Issue format:

  ```python
  Issue(
      file_path=match.group(1),
      line_number=int(match.group(2)),
      column_number=int(match.group(3)) if match.group(3) else None,
      error_code=match.group(4),
      message=match.group(5).strip(),
      severity="error"
  )
  ```

**Error Parsing:**

- ✅ **Regex-based parsing**: Robust parsing of Yamllint's output format
- ✅ **Multi-line support**: Handles complex error messages
- ✅ **Position tracking**: Accurate line and column number extraction

**File Management:**

### 🔧 Proposed runtime pass-throughs

- `--tool-options yamllint:format=standard` (switch output style)
- `--tool-options yamllint:config_file=.yamllint` (explicit config path)
- `--tool-options yamllint:no_warnings=True` (errors only)

- ✅ **Extension filtering**: Automatic YAML file detection
- ✅ **Batch processing**: Efficient handling of multiple files
- ✅ **Error aggregation**: Collects all issues across files

## Usage Comparison

### Core Yamllint

```bash
# Basic checking
yamllint config.yml

# With custom format
yamllint --format parsable config.yml

# With custom config
yamllint --config-file .yamllint-custom config.yml

# Strict mode
yamllint --strict config.yml

# Multiple files
yamllint *.yml *.yaml
```

### Lintro Wrapper

```python
# Basic checking
yamllint_tool = YamllintTool()
yamllint_tool.set_files(["config.yml"])
issues = yamllint_tool.check()

# With options
yamllint_tool.set_options(
    format="parsable",
    strict=True,
    no_warnings=True
)
```

## Configuration Strategy

### Core Tool Configuration

Yamllint uses configuration files:

- `.yamllint`
- `setup.cfg` `[yamllint]` section
- `pyproject.toml` `[tool.yamllint]` section

### Lintro Approach

The wrapper supports both configuration files and runtime options:

- **Configuration files**: Primary configuration method
- **Runtime options**: Override specific settings via `set_options()`
- **Combined approach**: Configuration files provide defaults, runtime options override

## Error Code Mapping

Lintro preserves all Yamllint error codes:

| Category               | Codes                        | Description                      |
| ---------------------- | ---------------------------- | -------------------------------- |
| **Syntax Errors**      | syntax-error                 | YAML syntax violations           |
| **Document Structure** | document-start, document-end | Document structure issues        |
| **Indentation**        | indentation                  | Indentation and spacing issues   |
| **Line Length**        | line-length                  | Line length violations           |
| **Comments**           | comments                     | Comment formatting and placement |
| **Key Ordering**       | key-ordering                 | Key ordering in mappings         |
| **Trailing Spaces**    | trailing-spaces              | Trailing whitespace issues       |
| **Truthy Values**      | truthy                       | Boolean value formatting         |

## Recommendations

### When to Use Core Yamllint

- Need runtime configuration changes
- Require specific output formats for tooling integration
- Want detailed error explanations
- Need custom rule definitions
- Require schema validation capabilities

### When to Use Lintro Wrapper

- Part of multi-tool linting pipeline
- Need consistent issue reporting across tools
- Want Python object integration
- Require aggregated results across multiple tools
- Need standardized error handling

## Limitations and Workarounds

### Missing Runtime Configuration

**Problem**: Cannot modify individual rules at runtime **Workaround**: Use configuration
files (`.yamllint`, `setup.cfg`, `pyproject.toml`)

### Limited Schema Validation

**Problem**: No built-in schema validation **Workaround**: Use external tools like
`jsonschema` for schema validation

### No Auto-fixing

**Problem**: Yamllint cannot automatically fix issues **Workaround**: Use external
formatters like Prettier for YAML formatting

## Future Enhancement Opportunities

1. **Configuration Pass-through**: Allow runtime rule customization via `set_options()`
2. **Schema Integration**: Built-in schema validation capabilities
3. **Auto-fixing**: Integration with YAML formatters for automatic fixes
4. **Advanced Filtering**: Post-processing filters for issue selection
5. **Performance**: Parallel processing for large YAML files
6. **Custom Rules**: Plugin system for custom rule definitions
