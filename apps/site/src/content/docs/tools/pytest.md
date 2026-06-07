---
title: 'pytest'
description: ''
category: tools
order: 150
navGroup: python
---

# Pytest Tool Analysis

## Overview

Pytest is a mature full-featured Python testing tool that helps you write better
programs. This analysis compares Lintro's wrapper implementation with the core pytest
tool.

## Core Tool Capabilities

Pytest provides extensive testing capabilities including:

- **Test Discovery**: Automatic discovery of test files and functions
- **Assertions**: Simple assert statements with detailed failure reporting
- **Fixtures**: Dependency injection system for test setup and teardown
- **Parametrization**: Run the same test with different data sets
- **Markers**: Categorize and selectively run tests
- **Plugins**: Extensive plugin ecosystem for additional functionality
- **Output Formats**: Multiple output formats including JSON, JUnit XML, and plain text
- **Configuration**: Support for `pyproject.toml`, `pytest.ini`, and command-line
  options
- **Coverage Integration**: Works with coverage.py for test coverage reporting
- **Parallel Execution**: Support for parallel test execution with pytest-xdist

## Lintro Implementation Analysis

### ✅ Preserved Features

**Core Functionality:**

- ✅ **Test Execution**: Full preservation through `pytest` command
- ✅ **Output Formats**: Supports JSON, JUnit XML, and plain text output
- ✅ **Configuration**: Respects `pyproject.toml` and `pytest.ini`
- ✅ **File Targeting**: Supports Python test file patterns (`test_*.py`, `*_test.py`)
- ✅ **Failure Detection**: Captures test failures and errors
- ✅ **Verbose Output**: Configurable verbosity levels
- ✅ **Traceback Format**: Configurable traceback display
- ✅ **Max Failures**: Configurable maximum number of failures before stopping

**Command Execution:**

```python
# From tool_pytest.py
cmd = self._get_executable_command("pytest") + ["-v", "--tb", "short", "--maxfail", "1"]
# For JSON output:
cmd = self._get_executable_command("pytest") + ["--json-report", "--json-report-file=pytest-report.json"]
# For JUnit XML output:
cmd = self._get_executable_command("pytest") + ["--junitxml", "report.xml"]
```

**Configuration Options:**

- ✅ **Verbosity**: `verbose` parameter
- ✅ **Traceback Format**: `tb` parameter (short, long, auto, line, native)
- ✅ **Max Failures**: `maxfail` parameter
- ✅ **Header Control**: `no_header` parameter
- ✅ **Warnings**: `disable_warnings` parameter
- ✅ **JSON Report**: `json_report` parameter
- ✅ **JUnit XML**: `junitxml` parameter

### 🔄 Enhanced Features

**Lintro-Specific Enhancements:**

- 🔄 **Test Mode Isolation**: Adds `--strict-markers` and `--strict-config` in test mode
- 🔄 **Timeout Management**: Configurable timeout (default 300 seconds)
- 🔄 **Priority System**: High priority (90) for test execution
- 🔄 **File Pattern Matching**: Automatic discovery of test files
- 🔄 **Output Parsing**: Multiple output format parsing with fallback

### ❌ Limitations & Supported Features

**Important:** Lintro's pytest integration focuses on core test execution and reporting.
Advanced pytest features require using pytest directly.

#### ✅ What IS Supported

The following sections outline lintro's pytest support. **Baseline Features** represent
core functionality available from the initial implementation, while **Enhanced
Features** are recently added capabilities that extend the baseline functionality.

##### Baseline Features

**Core Test Execution:**

- ✅ Running tests via `pytest` command
- ✅ Test discovery (automatic file pattern matching)
- ✅ Test failure and error detection
- ✅ Test result parsing and reporting
- ✅ Multiple output formats (JSON, JUnit XML, plain text)
- ✅ Configuration via `pyproject.toml` and `pytest.ini`
- ✅ Docker test filtering via markers
- ✅ Parallel execution via pytest-xdist (`workers` option)
- ✅ Coverage threshold enforcement (`coverage_threshold` option)
- ✅ Performance metrics (slow test detection, execution time warnings)

**Command-Line Options:**

- ✅ Verbosity control (`verbose`)
- ✅ Traceback format (`tb`: short, long, auto, line, native)
- ✅ Max failures (`maxfail`)
- ✅ Header and warnings control (`no_header`, `disable_warnings`)
- ✅ JSON report output (`json_report`)
- ✅ JUnit XML output (`junitxml`)
- ✅ Docker test control (`run_docker_tests`)

**Integration:**

- ✅ CI/CD pipeline integration
- ✅ Docker test isolation
- ✅ Error handling and reporting
- ✅ Performance tracking

##### Enhanced Features

**Plugin Management:**

- ✅ **Plugin discovery** - List installed pytest plugins via `--list-plugins` flag
- ✅ **Plugin checking** - Check if required plugins are installed via `--check-plugins`
  flag
- ✅ **Plugin configuration** - Configure plugin-specific settings via `--tool-options`
- **Usage:** `lintro test --list-plugins` or
  `lintro test --check-plugins --tool-options pytest:required_plugins=pytest-cov,pytest-xdist`

**Advanced Pytest Features:**

- ✅ **Custom marker listing** - List all available markers via `--markers` flag
- ✅ **Fixture management** - List fixtures and get fixture information via `--fixtures`
  and `--fixture-info` flags
- ✅ **Parametrization help** - Show parametrization examples via `--parametrize-help`
  flag
- ✅ **Test collection without execution** - List tests without running them via
  `--collect-only` flag
- **Usage:** `lintro test --markers`, `lintro test --fixtures`,
  `lintro test --collect-only`

**Coverage Integration:**

- ✅ **Coverage threshold enforcement** - Enforce minimum coverage via
  `coverage_threshold` option
- ✅ **Coverage HTML generation** - Generate HTML coverage reports via `coverage_html`
  option
- ✅ **Coverage XML generation** - Generate XML coverage reports via `coverage_xml`
  option
- ✅ **Combined coverage reports** - Generate both HTML and XML via `coverage_report`
  option
- **Usage:**
  `lintro test --tool-options pytest:coverage_html=htmlcov,pytest:coverage_xml=coverage.xml`
  or `pytest:coverage_report=True`

**Parallel Execution:**

- ✅ **Basic support** - Workers option available (`pytest:workers=auto|N`)
- ✅ **Parallel execution presets** - Preset options available
  (`pytest:parallel_preset=small|medium|large|auto`)
- **Note:** Uses pytest-xdist plugin (must be installed separately)
- **Presets:**
  - `auto`: Uses all available CPU cores
  - `small`: 2 workers (for small test suites)
  - `medium`: 4 workers (for medium test suites)
  - `large`: Up to 8 workers (limited by CPU count)

**Test Result Analysis:**

- ❌ **No test result trending** - Cannot track test results over time
- ✅ **Flaky test detection** - Automatically detects intermittent failures
- ❌ **No test impact analysis** - Cannot determine which tests to run based on code
  changes
- ❌ **No mutation testing** - No mutation testing integration

**HTML Reports:**

- ✅ **pytest-html integration** - HTML report generation via `html_report` option
- **Usage:** `lintro test --tool-options pytest:html_report=report.html`
- **Note:** Requires pytest-html plugin to be installed

**pytest-timeout integration** - Individual test timeouts via `timeout` option

- **Usage:** `lintro test --tool-options pytest:timeout=300`
- **Note:** Requires pytest-timeout plugin to be installed
- **Options:** `timeout_method` (signal/thread, default: signal)

**pytest-rerunfailures integration** - Automatic retry of failed tests

- **Usage:** `lintro test --tool-options pytest:reruns=2,pytest:reruns_delay=1`
- **Note:** Requires pytest-rerunfailures plugin to be installed

#### When to Use Lintro vs. Pytest Directly

**Use Lintro when:**

- You want unified test execution with other linting/formatting tools
- You need a consistent CLI across all tools
- You want integrated error reporting and formatting
- You're running basic test execution and reporting

**Use pytest directly when:**

- You need advanced plugin internals or complex plugin development
- You require test impact analysis based on code changes
- You need mutation testing integration
- You want features not yet exposed through lintro's CLI

> **Note:** Many pytest features are supported through lintro—see the
> [Enhanced Features](#enhanced-features) section for supported capabilities including
> custom marker listing (`--markers`), fixture management (`--fixtures`,
> `--fixture-info`), test collection (`--collect-only`), HTML reports (`html_report`),
> and coverage integration.

#### Configuration Priority

Lintro respects pytest's configuration priority order:

1. CLI `--tool-options` (highest priority - user override)
2. Environment variables
3. `pyproject.toml` `[tool.pytest.ini_options]` (pytest convention)
4. `pyproject.toml` `[tool.pytest]` (backward compatibility)
5. `pytest.ini` `[pytest]`
6. Built-in defaults (lowest priority)

## Implementation Details

### Parser Support

The pytest parser supports multiple output formats:

1. **JSON Format**: Parses pytest-json-report output
2. **JUnit XML**: Parses JUnit XML output
3. **Plain Text**: Parses standard pytest text output

### Issue Model

```python
@dataclass
class PytestIssue:
    file: str
    line: int
    test_name: str
    message: str
    test_status: str
    duration: float | None = None
    node_id: str | None = None
```

### Formatter Support

The pytest formatter provides table-based output with columns:

- File: Test file path
- Line: Line number of failure
- Test Name: Name of the failing test
- Status: Test status (FAILED, ERROR, etc.)
- Message: Error message or failure description

## Usage Examples

### Basic Test Execution

```bash
# Run all tests
lintro test

# Run specific test files
lintro test tests/unit/test_example.py

# Run with custom options
lintro test --verbose --tool-options verbose=True,tb=short,maxfail=5

# Generate HTML report
lintro test --tool-options html_report=report.html

# Use parallel execution preset
lintro test --tool-options parallel_preset=medium

# Set test timeouts
lintro test --tool-options timeout=300

# Retry failed tests
lintro test --tool-options reruns=2,reruns_delay=1
```

### Plugin Management

```bash
# List all installed pytest plugins
lintro test --list-plugins

# Check if required plugins are installed
lintro test --check-plugins --tool-options pytest:required_plugins=pytest-cov,pytest-xdist
```

### Coverage Reports

```bash
# Generate HTML coverage report
lintro test --tool-options pytest:coverage_html=htmlcov

# Generate XML coverage report
lintro test --tool-options pytest:coverage_xml=coverage.xml

# Generate both HTML and XML reports
lintro test --tool-options pytest:coverage_report=True

# Custom coverage report paths
lintro test --tool-options pytest:coverage_html=custom/htmlcov,pytest:coverage_xml=custom/coverage.xml
```

### Test Discovery

```bash
# List all tests without executing them
lintro test --collect-only

# List tests in specific directory
lintro test tests/unit --collect-only
```

### Fixture Management

```bash
# List all available fixtures
lintro test --fixtures

# Get detailed information about a specific fixture
lintro test --fixture-info sample_data

# List fixtures in specific directory
lintro test tests/unit --fixtures
```

### Markers

```bash
# List all available markers
lintro test --markers
```

### Parametrization Help

```bash
# Show parametrization examples and documentation
lintro test --parametrize-help
```

### Configuration File Support

```toml
# pyproject.toml
[tool.pytest]
addopts = "-v --tb=short --maxfail=1"
testpaths = ["tests"]
python_files = ["test_*.py", "*_test.py"]
```

### Output Formats

```bash
# Grid output (default)
lintro test --output-format grid

# JSON output
lintro test --output-format json

# Markdown output
lintro test --output-format markdown

# Plain text output
lintro test --output-format plain
```

### Command Chaining

```bash
# Run multiple commands in sequence
lintro fmt, chk, test

# With specific tools
lintro fmt --tools black, chk --tools ruff, test

# With aliases
lintro fmt, chk, tst
```

## Integration with Lintro

### Command

- **Command**: `lintro test` (alias: `lintro tst`)
- **Type**: Separate test runner (not included in check/fmt operations)
- **Priority**: Not applicable (runs independently)
- **Timeout**: 300 seconds (5 minutes)
- **Can Fix**: False (pytest doesn't fix code, it runs tests)

### File Patterns

- `test_*.py`: Standard pytest test file pattern
- `*_test.py`: Alternative test file pattern

### Dependencies

- Requires pytest to be installed
- Optional: pytest-json-report for JSON output
- Optional: pytest-xdist for parallel execution

### Separation from Linting Tools

- Pytest is **not** available via `lintro check --tools`
- Pytest is **not** available via `lintro fmt --tools`
- Pytest runs **only** via the dedicated `lintro test` command
- Pytest can be **chained** with other commands: `lintro fmt, chk, test`

## Docker Test Support

Lintro's pytest integration includes built-in support for Docker-only tests through
environment variable control and pytest markers.

### Docker Test Markers

Tests that require Docker or Docker-specific dependencies should be marked with the
`@pytest.mark.docker_only` marker:

```python
import pytest

@pytest.mark.docker_only
def test_docker_feature():
    """Test that requires Docker."""
    # Your Docker-specific test code here
    pass
```

### Environment Variable Control

Docker tests are controlled via the `LINTRO_RUN_DOCKER_TESTS` environment variable:

- **Default Behavior**: Docker tests are **disabled** by default (skipped)
- **Enable Docker Tests**: Set `LINTRO_RUN_DOCKER_TESTS=1` to include Docker tests
- **Disable Docker Tests**: Unset the variable or set it to any value other than `"1"`

### CLI Integration

Use the `--enable-docker` flag to enable Docker tests via the CLI:

```bash
# Docker tests disabled (default)
lintro test

# Docker tests enabled
lintro test --enable-docker

# Docker tests enabled with custom options
lintro test --enable-docker --tool-options verbose=True,tb=short
```

### Configuration in pytest.ini

The `docker_only` marker should be registered in your `pytest.ini`:

```ini
[pytest]
markers =
    docker_only: mark test as requiring Docker or Docker-specific dependencies
    # ... other markers
```

### How It Works

1. **Test Collection**: When collecting tests, lintro identifies tests marked with
   `@pytest.mark.docker_only`
2. **Environment Control**: Based on `LINTRO_RUN_DOCKER_TESTS`:
   - If set to `"1"`: All tests (including Docker tests) are collected and run
   - If not set or set to other value: Docker tests are skipped during collection
3. **Reporting**: Skipped Docker tests are reported in the test summary output

### Example Usage

```python
# tests/integration/test_docker_feature.py
import pytest

@pytest.mark.docker_only
def test_docker_connection():
    """Test Docker connection."""
    import docker
    client = docker.from_env()
    assert client.ping()

def test_regular_feature():
    """Regular test that doesn't require Docker."""
    assert True
```

```bash
# Run without Docker tests
$ lintro test
[LINTRO] Docker tests disabled (1 tests not collected). Use --enable-docker to include them.

# Run with Docker tests
$ lintro test --enable-docker
[LINTRO] Docker tests enabled (1 tests) - this may take longer than usual.
```

### CI/CD Integration

In CI/CD environments, Docker tests are typically enabled:

```yaml
# .github/workflows/test.yml
- name: Run tests with Docker
  env:
    LINTRO_RUN_DOCKER_TESTS: 1
  run: lintro test --enable-docker
```

Or use the Docker test script which automatically enables Docker tests:

```bash
# scripts/docker/docker-test.sh automatically sets LINTRO_RUN_DOCKER_TESTS=1
./scripts/docker/docker-test.sh
```

## Best Practices

1. **Test Organization**: Use consistent test file naming conventions
2. **Configuration**: Use `pyproject.toml` for pytest configuration
3. **Output Format**: Choose appropriate output format for your CI/CD pipeline
4. **Timeout**: Set appropriate timeout for your test suite
5. **Max Failures**: Use `maxfail=1` for fast feedback in development
6. **Docker Tests**: Mark Docker-requiring tests with `@pytest.mark.docker_only` and use
   `--enable-docker` when needed

## Implemented Features

The following features have been implemented:

1. ✅ **Plugin Support**: List and check pytest plugins via `--list-plugins` and
   `--check-plugins`
2. ✅ **Coverage Integration**: HTML/XML report generation via `coverage_html`,
   `coverage_xml`, and `coverage_report` options
3. ✅ **Parallel Execution**: Support for parallel test execution via `workers` and
   `parallel_preset` options
4. ✅ **Test Discovery**: Test collection without execution via `--collect-only` flag
5. ✅ **Fixture Management**: List fixtures and get fixture info via `--fixtures` and
   `--fixture-info` flags
6. ✅ **Parametrization Help**: Show parametrization examples via `--parametrize-help`
   flag
7. ✅ **Custom Markers**: List all markers via `--markers` flag
8. ✅ **Performance Metrics**: Test execution time tracking and slow test detection
9. ✅ **Test Results**: Test result summary and statistics with flaky test detection
10. ✅ **CI Integration**: CI-specific configurations with auto-junitxml and docker test
    support
11. ✅ **Plugin Integrations**: Support for pytest-html, pytest-timeout, and
    pytest-rerunfailures

## Future Enhancements

Potential improvements for the pytest integration:

1. **Plugin Installation**: Add ability to install pytest plugins via lintro
2. **Fixture Creation**: Add ability to create fixtures via lintro CLI
3. **Parametrized Test Creation**: Add ability to generate parametrized test templates
4. **Custom Marker Definition**: Add ability to define new markers via lintro CLI
5. **Test Impact Analysis**: Determine which tests to run based on code changes
6. **Mutation Testing**: Integration with mutation testing tools
