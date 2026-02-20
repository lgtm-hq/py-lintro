<!-- markdownlint-disable MD024 -->

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **AI-Powered Features** (BYO API key, install with `lintro[ai]`):
  - AI-powered issue summaries with pattern analysis and prioritized actions
  - Interactive fix suggestions with AI-generated code diffs
  - AI-driven risk classification (`safe-style` vs `behavioral-risk`)
  - Multi-provider support: Anthropic Claude and OpenAI GPT
  - Post-fix summary contextualizing applied changes
  - Configurable retry, context lines, search radius, and timeout settings
  - Docker AI support via `WITH_AI` build arg

### Breaking Changes

- **Plugin Architecture Migration**: Complete migration from tool-specific classes to
  unified plugin system
  - **API Changes**:
    - Old: `from lintro.tools.implementations.tool_ruff import RuffTool` and
      `RuffTool()`
    - New: `from lintro.plugins import ToolRegistry` and `ToolRegistry.get("ruff")`
    - Tool instances now expose `tool.definition.name` instead of `tool.name`
  - **Deleted Modules**:
    - All `lintro/tools/implementations/tool_*.py` files (12 files)
    - `lintro/tools/core/tool_base.py`
    - `lintro/models/core/tool.py` and `tool_config.py`
    - All `lintro/formatters/tools/*_formatter.py` files (13 files)
    - `lintro/tools/implementations/yamllint_config.py` and `yamllint_runner.py`
  - **New Plugin System**:
    - Tool definitions now in `lintro/tools/definitions/*.py`
    - Plugins use `lintro.plugins.BaseToolPlugin` base class
    - Unified formatter at `lintro/formatters/unified.py` replaces per-tool formatters
    - `ToolRegistry.get("tool_name")` to get tool instances
- **Python Version**: Lowered minimum Python version from 3.13 to 3.11
  - `pyproject.toml` updated with classifiers for 3.11, 3.12, 3.13

### Fixed

- **Critical**: Fixed circular import bug in `lintro.parsers` module
  - Issue:
    `ImportError: cannot import name 'bandit' from partially initialized module 'lintro.parsers'`
    when running lintro as a dependency
  - Root causes:
    1. Eager imports in `parsers/__init__.py` causing circular dependencies
    2. Missing `lintro.parsers.bandit` package in setuptools configuration
  - Impact: Prevents lintro CLI from working when installed as a wheel distribution
  - Fix:
    1. Replaced eager imports with lazy loading via `__getattr__` in
       `lintro/parsers/__init__.py`
    2. Added `lintro.parsers.bandit` to setuptools packages list
  - Tests: Added comprehensive import tests for direct imports and lazy loading patterns
  - Verified: Works in both editable install (development) and built wheel (production)
- **PyPI Publication Workflow**: Fixed test failures in PyPI publish workflow by adding
  missing tool installation step
  - Added tool installation step (`./scripts/utils/install-tools.sh --local`) to PyPI
    workflow
  - Added PATH setup to ensure tools are available during test execution
  - Now matches the tool setup used in the main CI workflow
- **Tool Installation Script**: Improved compatibility with uv-based Python environments
  - Updated `install-tools.sh` to use `uv pip install` for Python packages when uv is
    available
  - Added detection for GitHub Actions environment and uv availability
  - Maintains fallback to pip for environments without uv
- **Package Distribution**: Fixed MANIFEST.in file patterns to eliminate build warnings
  - Updated Dockerfile pattern to match actual file names (`Dockerfile.*`)
  - Removed unnecessary `.rst` and `.txt` patterns for docs directory
  - Clean build process with no warnings during package creation

### Technical Details

- **Files Modified**:
  - `.github/workflows/publish-pypi.yml` - Added tool installation and PATH setup
  - `scripts/utils/install-tools.sh` - Improved uv compatibility for Python package
    installation
  - `MANIFEST.in` - Fixed file inclusion patterns

- **Root Cause**: PyPI publish workflow was missing external tool dependencies (ruff,
  darglint, prettier, yamllint, hadolint) that integration tests require
- **Impact**: All tests now pass in PyPI publication workflow, enabling successful
  package distribution
- CI script path references for coverage comments
- Package metadata and classifiers
- Logo display in README for PyPI compatibility

### Added

- Initial release preparation
- PyPI package configuration
- MANIFEST.in file for asset inclusion
- CHANGELOG.md for version tracking

## [0.1.0] - 2024-07-26

### Added

- Initial release of Lintro
- Unified CLI interface for multiple code quality tools
- Support for Ruff, Darglint, Prettier, Yamllint, and Hadolint
- Multiple output formats (grid, JSON, HTML, CSV, Markdown)
- Auto-fixing capabilities where supported
- Docker support and containerized environments
- Comprehensive test suite with 85% coverage
- CI/CD integration with GitHub Actions
- Documentation and usage examples

### Features

- **Unified CLI**: Single command interface for all tools
- **Multi-language support**: Python, JavaScript, YAML, Docker
- **Rich output formatting**: Beautiful table views and multiple formats
- **Auto-fixing**: Automatic issue resolution where possible
- **Docker ready**: Containerized execution for consistency
- **CI/CD integration**: GitHub Actions workflows for automation

### Supported Tools

- **Ruff**: Python linting and formatting
- **Darglint**: Python docstring validation
- **Prettier**: JavaScript/TypeScript/JSON formatting
- **Yamllint**: YAML syntax and style checking
- **Hadolint**: Dockerfile best practices

### Technical Details

- Python 3.13+ compatibility
- MIT License
- Comprehensive type hints
- Google-style docstrings
- Ruff and MyPy linting
- 85% test coverage
- Docker containerization
