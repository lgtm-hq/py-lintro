<!-- markdownlint-disable MD024 -- duplicate headings are standard in changelogs -->

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Deprecated

### Removed

### Fixed

### Security

## [0.65.0] - 2026-07-02

### Added

- **ai/review**: add GitHub PR review posting (#1037) (091df5d)
- **ai/review**: add terminal and JSON output formatters (#1036) (3761712)
- **cli**: add lintro review command (#1035) (b1b0b09)
- **ai/review**: add review orchestrator with depth and chunking (#1034) (45cbbd1)
- **ai/review**: add review prompt templates (#1011) (8e16733)
- **ai/review**: add checklist registry with file-glob triggers (#1003) (05f60c5)
- **ai/review**: add diff collection, classifier, and chunker (#1000) (4ae62b3)
- **ci**: migrate thin reusables to lgtm-ci v0.46.0 (#990) (b13f92d)

## [0.64.5] - 2026-06-19

### Changed

- **ci**: bump lgtm-ci from v0.32.3 to v0.45.2 (#986) (36f047d)

### Fixed

- **ci**: grant missing permissions for lgtm-ci v0.45.2 reusable workflows (#988)
  (4636b7a)

## [0.64.4] - 2026-06-05

### Bug Fixes

- **renovate**: scope postUpgradeTasks to regex managers only (#970) (f196589)

### Other Changes

- **deps**: update lgtm-hq/lgtm-ci to v0.32.0 (minor) (#971) (90e6727)

## [0.64.3] - 2026-05-26

### Bug Fixes

- **ci**: repin coverage Pages publish to lgtm-ci v0.19.2 (#947) (88cd875)
- **ci**: annotate SHA pins and enable Renovate for lgtm-ci updates (#946) (9a54b4a)
- **ci**: repin lgtm-ci to v0.18.3 and fix egress allowlists (#943) (070e106)
- **ci**: merge coverage artifacts, add grype and Docker Hub egress (#942) (c6b4442)

### Other Changes

- **ci**: migrate to lgtm-ci v0.18.1 reusable workflows (#939) (7bfa7e7)

### Previously Unreleased

- **install**: Default `pip install lintro` is now lightweight (CLI only); bundled
  Python tools (ruff, black, mypy, bandit, pydoclint, yamllint) moved to `lintro[full]`
- **install**: Install profiles (`minimal`, `recommended`, `python`, `web`, `ci`,
  `full`) driven by `manifest.json`
- **install**: Interactive profile and tool selection in TTY mode; `--yes` / `-y` for
  non-interactive use
- **install**: `--write-lock` exports resolved plan to `.lintro-install.lock.json`
- **install**: Manual install bucket for tools whose package manager is unavailable
- **init**: `lintro init` detects project languages and generates `.lintro-config.yaml`;
  merges with existing config on rerun instead of clobbering
- **doctor**: Config-aware — respects `enabled_tools` and per-tool `enabled: false`;
  `--all` overrides; `--tools` takes explicit precedence
- **doctor**: `INCOMPATIBLE` status for versions below `min_version`; structured JSON
  output includes `min_version`, `recommended`, `disabled` counts
- **doctor**: `--fix` now also remediates `INCOMPATIBLE` tools
- **version**: `min_version` field in manifest for version tolerance; execution warns
  when installed version is below recommended but above minimum
- **parser**: `parse_failures_count` on `ToolResult`; surfaced in CLI and JSON output
- **onboarding**: First-run guidance when no tools are available; post-install next
  steps suggest `lintro init`, `lintro doctor`, `lintro check .`
- **homebrew**: Lightweight `lintro` formula (binary); `lintro-full` formula (PyPI with
  bundled tools)
- **tsc/vue-tsc**: Support TypeScript project references in monorepos — automatic
  sub-project discovery via `references` and directory walking (#803, #805)
- **tsc/vue-tsc**: Per-project framework detection in monorepos — Astro/Vue/Svelte
  detection is scoped per sub-project, not globally
- **tsc/vue-tsc**: "Deepest tsconfig wins" partitioning — overlapping parent/child
  configs no longer cause duplicate checking under conflicting compiler options
- **AI-Powered Features** (BYO API key, install with `'lintro[ai]'`):
  - AI-powered issue summaries with pattern analysis and prioritized actions
  - Interactive fix suggestions with AI-generated code diffs
  - AI-driven risk classification (`safe-style` vs `behavioral-risk`)
  - Multi-provider support: Anthropic Claude and OpenAI GPT
  - Post-fix summary contextualizing applied changes
  - Configurable retry, context lines, search radius, and timeout settings
  - Docker AI support via `WITH_AI` build arg
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
- **tsc/vue-tsc**: Respect tsconfig.json `include`/`exclude`/`files` scoping instead of
  overriding with all discovered files (#851)
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

- Python 3.13+ compatibility (historical; minimum later lowered to 3.11)
- MIT License
- Comprehensive type hints
- Google-style docstrings
- Ruff and MyPy linting
- 85% test coverage
- Docker containerization
