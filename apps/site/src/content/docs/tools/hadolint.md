---
title: 'hadolint'
description: ''
category: tools
order: 80
navGroup: ci-ops
---

# Hadolint Tool Analysis

## Overview

Hadolint is a Dockerfile linter that helps you build best-practice Docker images. It
parses the Dockerfile into an AST and performs rules on top of the AST. It also uses
ShellCheck to lint the Bash code inside RUN instructions. This analysis compares
Lintro's wrapper implementation with the core Hadolint tool.

## Core Tool Capabilities

Hadolint provides comprehensive Dockerfile analysis including:

- **Dockerfile parsing**: AST-based analysis of Dockerfile structure
- **Shell script linting**: Integration with ShellCheck for RUN instruction analysis
- **Best practices**: Enforces Docker best practices and security guidelines
- **Configuration options**: `--format`, `--failure-threshold`, `--ignore`,
  `--trusted-registries`
- **Output formats**: tty, json, checkstyle, codeclimate, gitlab_codeclimate, gnu,
  codacy, sonarqube, sarif
- **Rule customization**: Extensive rule configuration and ignoring
- **Security scanning**: Identifies security vulnerabilities and anti-patterns

## Lintro Implementation Analysis

### ✅ Preserved Features

**Core Functionality:**

- ✅ **Dockerfile linting**: Full preservation of Dockerfile analysis
- ✅ **Shell script analysis**: Integration with ShellCheck for RUN instructions
- ✅ **Best practice enforcement**: Supports all Hadolint rules and guidelines
- ✅ **Configuration files**: Respects `.hadolint.yaml` and other config files
- ✅ **Error categorization**: Preserves Hadolint's error code system
- ✅ **File targeting**: Supports Dockerfile patterns (`Dockerfile`, `Dockerfile.*`,
  `*.dockerfile`)
- ✅ **Error detection**: Captures syntax, style, and security violations

**Command Execution:**

```python
# From tool_hadolint.py
cmd = ["hadolint"] + self.files
result = subprocess.run(cmd, capture_output=True, text=True)
```

**Configuration Options:**

- ✅ **Output format**: `format` parameter (tty, json, checkstyle, etc.)
- ✅ **Failure threshold**: `failure_threshold` parameter (error, warning, info, style)
- ✅ **Ignore rules**: `ignore` parameter for specific rule codes
- ✅ **Trusted registries**: `trusted_registries` parameter for trusted Docker
  registries
- ✅ **Required labels**: `require_labels` parameter for required label schemas
- ✅ **Strict labels**: `strict_labels` parameter for strict label checking
- ✅ **No fail**: `no_fail` parameter to suppress exit codes
- ✅ **No color**: `no_color` parameter to disable color output

### ⚠️ Limited/Missing Features

**Advanced Configuration:**

- ⚠️ **Runtime rule customization**: Prefer config; propose
  `hadolint:only=DL3006|SC2086` for targeted checks.
- ❌ **Per-file configuration**: No support for file-specific rule overrides
- ❌ **Custom rule definitions**: No support for custom rule creation
- ❌ **Rule severity control**: Limited control over rule severity levels

**Output Customization:**

- ❌ **Detailed error context**: Limited access to detailed error explanations
- ❌ **Custom formatters**: Cannot use custom output formatters
- ❌ **Progress reporting**: No access to progress indicators for large files

**Advanced Features:**

- ❌ **Docker Compose support**: No support for docker-compose.yml analysis
- ❌ **Multi-stage optimization**: Limited analysis of multi-stage build optimization
- ❌ **Image scanning**: No integration with container image scanning tools

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

- ✅ **Issue normalization**: Converts Hadolint output to standard Issue format:

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

### 🔧 Proposed runtime pass-throughs

- `--tool-options hadolint:config=.hadolint.yaml`
- `--tool-options hadolint:only=DL3006|SC2086`
- `--tool-options hadolint:no_color=False,hadolint:format=json`

- ✅ **Regex-based parsing**: Robust parsing of Hadolint's output format
- ✅ **Multi-line support**: Handles complex error messages
- ✅ **Position tracking**: Accurate line and column number extraction

**File Management:**

- ✅ **Extension filtering**: Automatic Dockerfile detection
- ✅ **Batch processing**: Efficient handling of multiple files
- ✅ **Error aggregation**: Collects all issues across files

## Usage Comparison

### Core Hadolint

```bash
# Basic checking
hadolint Dockerfile

# With custom format
hadolint --format json Dockerfile

# With failure threshold
hadolint --failure-threshold warning Dockerfile

# Ignore specific rules
hadolint --ignore DL3006,SC2086 Dockerfile

# Multiple files
hadolint Dockerfile* *.dockerfile
```

### Lintro Wrapper

```python
# Basic checking
hadolint_tool = HadolintTool()
hadolint_tool.set_files(["Dockerfile"])
issues = hadolint_tool.check()

# With options
hadolint_tool.set_options(
    format="json",
    failure_threshold="warning",
    ignore=["DL3006", "SC2086"]
)
```

## Configuration Strategy

### Core Tool Configuration

Hadolint uses configuration files:

- `.hadolint.yaml`
- `.hadolint.yml`
- `hadolint.yaml`
- `hadolint.yml`

### Lintro Approach

The wrapper supports both configuration files and runtime options:

- **Configuration files**: Primary configuration method
- **Runtime options**: Override specific settings via `set_options()`
- **Combined approach**: Configuration files provide defaults, runtime options override

## Error Code Mapping

Lintro preserves all Hadolint error codes:

| Category              | Prefix | Description                            |
| --------------------- | ------ | -------------------------------------- |
| **Dockerfile Rules**  | DL     | Dockerfile-specific best practices     |
| **ShellCheck Rules**  | SC     | Shell script analysis (via ShellCheck) |
| **Security Rules**    | DL     | Security-related Dockerfile issues     |
| **Performance Rules** | DL     | Performance optimization suggestions   |
| **Style Rules**       | DL     | Dockerfile style and formatting issues |

### Common Rule Examples

- **DL3006**: Always tag the version in `FROM`
- **DL3008**: Pin versions in `apt-get`
- **DL3009**: Delete the apt-get lists after installing packages
- **SC2086**: Double quote to prevent globbing and word splitting
- **SC2154**: Referenced but not assigned

## Recommendations

### When to Use Core Hadolint

- Need runtime configuration changes
- Require specific output formats for CI/CD integration
- Want detailed error explanations
- Need custom rule definitions
- Require Docker Compose analysis

### When to Use Lintro Wrapper

- Part of multi-tool linting pipeline
- Need consistent issue reporting across tools
- Want Python object integration
- Require aggregated results across multiple tools
- Need standardized error handling

## Limitations and Workarounds

### Missing Runtime Configuration

**Problem**: Cannot modify individual rules at runtime **Workaround**: Use configuration
files (`.hadolint.yaml`) for complex setups

### No Auto-fixing

**Problem**: Hadolint cannot automatically fix issues **Workaround**: Manual fixes based
on Hadolint recommendations

### Limited Docker Compose Support

**Problem**: No direct support for docker-compose.yml files **Workaround**: Use separate
tools for Docker Compose validation

## Future Enhancement Opportunities

1. **Configuration Pass-through**: Allow runtime rule customization via `set_options()`
2. **Docker Compose Integration**: Support for docker-compose.yml analysis
3. **Auto-fixing**: Integration with Dockerfile formatters for automatic fixes
4. **Advanced Filtering**: Post-processing filters for issue selection
5. **Performance**: Parallel processing for multiple Dockerfiles
6. **Custom Rules**: Plugin system for custom rule definitions
7. **Image Scanning**: Integration with container image scanning tools
8. **Multi-stage Analysis**: Enhanced analysis of multi-stage build optimization
