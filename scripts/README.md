# Scripts Directory

This directory contains utility scripts for development, CI/CD, Docker operations, and
local testing. All scripts are organized into logical subdirectories for easy
navigation.

## 📁 Directory Structure

```text
scripts/
├── build/        # Build and distribution scripts
├── ci/           # CI/CD and GitHub Actions scripts
├── docker/       # Docker-related scripts
├── homebrew/     # Homebrew formula templates
├── local/        # Local development scripts
└── utils/        # Utility scripts and shared functions
```

## 🚀 Quick Start

### For New Contributors

<!-- markdownlint-disable MD029 -->

1. **Install dependencies:**

```bash
./scripts/utils/install.sh
```

2. **Run tests:**

```bash
./scripts/local/run-tests.sh
```

3. **Use Lintro locally:**

```bash
./scripts/local/local-lintro.sh check --output-format grid
```

### For Docker Users

```bash
# Build and test Docker image
./scripts/docker/docker-test.sh

# Run Lintro in Docker
./scripts/docker/docker-lintro.sh check --output-format grid
```

## 📋 Script Categories

### 📦 Build Scripts (`build/`)

Scripts for building standalone binaries and distribution packages.

| Script           | Purpose                                  | Usage                                        |
| ---------------- | ---------------------------------------- | -------------------------------------------- |
| `build_macos.py` | Build macOS binary using Nuitka compiler | `uv run python scripts/build/build_macos.py` |

### 🍺 Homebrew Templates (`homebrew/`)

Homebrew formula templates for binary distribution.

| File            | Purpose                                       |
| --------------- | --------------------------------------------- |
| `lintro-bin.rb` | Homebrew formula template for binary releases |

### 🔧 CI/CD Scripts (`ci/`)

Scripts for GitHub Actions workflows and continuous integration.

| Script                              | Purpose                                                            | Usage                                                                    |
| ----------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------ |
| `backfill-compute-tags.sh`          | Compute Docker image tags for a backfill release                   | `TAG=v0.52.2 SHA=876464d... ./scripts/ci/backfill-compute-tags.sh`       |
| `backfill-generate-matrix.sh`       | Generate JSON tag matrix for backfill batch                        | `BATCH=1 ./scripts/ci/backfill-generate-matrix.sh`                       |
| `coverage-manager.sh`               | Unified coverage ops (extract/badge/comment/threshold)             | `./scripts/utils/coverage-manager.sh --help`                             |
| `ci-extract-coverage.sh`            | Extract coverage percentage                                        | `./scripts/ci/ci-extract-coverage.sh`                                    |
| `ci-lintro.sh`                      | Run Lintro analysis in Docker for CI                               | `./scripts/ci/ci-lintro.sh`                                              |
| `ci-log.sh`                         | Generic CI logging utility for workflow status messages            | `./scripts/ci/ci-log.sh <message>`                                       |
| `ci-post-pr-comment.sh`             | Post comments to PRs using GitHub API                              | `./scripts/ci/ci-post-pr-comment.sh [file]`                              |
| `ci-pr-comment.sh`                  | Generate PR comments with Lintro results                           | `./scripts/ci/ci-pr-comment.sh`                                          |
| `fail-on-lint.sh`                   | Fail CI job when lint checks fail                                  | `CHK_EXIT_CODE=1 ./scripts/ci/fail-on-lint.sh`                           |
| `post-pr-delete-previous.sh`        | Delete previous PR comments by marker                              | `./scripts/ci/post-pr-delete-previous.sh --help`                         |
| `lintro-report-generate.sh`         | Generate comprehensive Lintro reports                              | `./scripts/ci/lintro-report-generate.sh`                                 |
| `pull-lintro-image.sh`              | Pull lintro Docker image from GHCR and log digest                  | `./scripts/ci/testing/pull-lintro-image.sh`                              |
| `pages-deploy.sh`                   | Deploy coverage reports to GitHub Pages                            | `./scripts/ci/pages-deploy.sh`                                           |
| `ghcr_prune_untagged.py`            | Prune untagged GHCR package versions                               | `uv run python scripts/ci/ghcr_prune_untagged.py`                        |
| `deployments-prune.sh`              | Prune GitHub deployments via gh (keep-n/ref)                       | `./scripts/ci/deployments-prune.sh --help`                               |
| `coverage-badge-update.sh`          | Generate and update coverage badge                                 | `./scripts/ci/testing/coverage-badge-update.sh --help`                   |
| `coverage-pr-comment.sh`            | Generate PR comments with coverage info                            | `./scripts/ci/github/coverage-pr-comment.sh --help`                      |
| `enforce-coverage-threshold.sh`     | Enforce minimum coverage threshold                                 | `./scripts/ci/testing/enforce-coverage-threshold.sh --help`              |
| `auto-tag-unified.sh`               | Unified auto-tagging functions (check/read/create)                 | `./scripts/ci/auto-tag-unified.sh --help`                                |
| `ci-auto-fix.sh`                    | Auto-format via Docker and push changes                            | `./scripts/ci/ci-auto-fix.sh`                                            |
| `pypi-version-exists.sh`            | Check if version exists on PyPI                                    | `./scripts/ci/pypi-version-exists.sh <project> <version>`                |
| `ensure-tag-on-main.sh`             | Ensure tag ref points to commit on main                            | `./scripts/ci/ensure-tag-on-main.sh --help`                              |
| `guard-release-commit.sh`           | Check last commit is release bump                                  | `./scripts/ci/guard-release-commit.sh --help`                            |
| `pre-release-quality.sh`            | Run Lintro format and check                                        | `./scripts/ci/pre-release-quality.sh --help`                             |
| `semantic_release_compute_next.py`  | Compute next version (tag-only baseline)                           | `uv run python scripts/ci/semantic_release_compute_next.py --print-only` |
| `validate-action-pinning.sh`        | Scan for unpinned GitHub Actions                                   | `./scripts/ci/validate-action-pinning.sh --help`                         |
| `semantic-pr-title-check.sh`        | Validate PR title against Conventional Commits                     | `./scripts/ci/semantic-pr-title-check.sh --help`                         |
| `verify-tag-matches-pyproject.sh`   | Verify tag matches `pyproject.toml` version                        | `./scripts/ci/verify-tag-matches-pyproject.sh --help`                    |
| `sbom-generate.sh`                  | Generate and export SBOMs via bomctl                               | `./scripts/ci/sbom-generate.sh --help`                                   |
| `sbom-rename-artifacts.sh`          | Prefix SBOMs with tag and SHA for traceability                     | `./scripts/ci/sbom-rename-artifacts.sh dist/sbom`                        |
| `sbom-attest-artifacts.sh`          | Create cosign attestations for SBOM artifacts                      | `./scripts/ci/sbom-attest-artifacts.sh dist/sbom`                        |
| `sbom-fetch-github-api.sh`          | Fetch repo SBOM via GitHub API; export via script                  | `./scripts/ci/sbom-fetch-github-api.sh --help`                           |
| `semantic-release-helpers.sh`       | Helpers for semantic-release workflow steps                        | `./scripts/ci/semantic-release-helpers.sh --help`                        |
| `reusable-quality-entry.sh`         | Quality gate wrapper for reusable workflow                         | `./scripts/ci/reusable-quality-entry.sh --help`                          |
| `configure-git-user.sh`             | Configure git user/email and safe.directory                        | `./scripts/ci/configure-git-user.sh --help`                              |
| `egress-audit-lite.sh`              | Audit reachability of allowed endpoints                            | `./scripts/ci/egress-audit-lite.sh --help`                               |
| `sbom-install-binary-gh.sh`         | Install bomctl from GitHub Releases via gh                         | `./scripts/ci/sbom-install-binary-gh.sh --help`                          |
| `fail-if-semantic-invalid.sh`       | Fail step if semantic title validation failed                      | `OK=true ./scripts/ci/fail-if-semantic-invalid.sh`                       |
| `detect-changes.sh`                 | Detect repo diffs and set has_changes output                       | `./scripts/ci/detect-changes.sh --help`                                  |
| `security-audit.sh`                 | Comprehensive security audit for workflows/scripts                 | `./scripts/ci/security-audit.sh --help`                                  |
| `bomctl-help-test.sh`               | Test bomctl binary installation                                    | `./scripts/ci/bomctl-help-test.sh`                                       |
| `sbom-generate-safe.sh`             | Generate SBOMs with consolidated error handling                    | `./scripts/ci/sbom-generate-safe.sh`                                     |
| `test-install-package.sh`           | Install and verify built package in isolated venv                  | `./scripts/ci/test-install-package.sh wheel`                             |
| `test-built-package-integration.sh` | Run integration tests for built package in isolated venv           | `./scripts/ci/test-built-package-integration.sh`                         |
| `test-venv-setup.sh`                | Create isolated Python 3.13 virtual environment                    | `./scripts/ci/test-venv-setup.sh`                                        |
| `test-verify-cli.sh`                | Verify lintro CLI entry points in installed package                | `./scripts/ci/test-verify-cli.sh`                                        |
| `test-verify-imports.sh`            | Verify critical package imports in installed lintro                | `./scripts/ci/test-verify-imports.sh wheel`                              |
| `extract-test-summary.sh`           | Extract pytest test summary to JSON for PR comments                | `./scripts/ci/testing/extract-test-summary.sh <log> <out.json>`          |
| `extract-version-from-tag.sh`       | Extract version from git tag (strips v prefix)                     | `./scripts/ci/extract-version-from-tag.sh`                               |
| `git-commit-push.sh`                | Stage, commit, and push with github-actions[bot]                   | `./scripts/ci/git-commit-push.sh <pattern> <message>`                    |
| `tools-image-push.sh`               | Push tools image tags with per-tag error handling                  | `./scripts/ci/tools-image-push.sh --help`                                |
| `tools-image-summary.sh`            | Generate GitHub step summary for tools image build                 | `./scripts/ci/tools-image-summary.sh --help`                             |
| `tools-image-tags.sh`               | Generate Docker image tags for tools image build                   | `./scripts/ci/tools-image-tags.sh --help`                                |
| `tools-image-resolve-tag.sh`        | Resolve tools image tag for reusable workflow callers              | `./scripts/ci/tools-image-resolve-tag.sh --help`                         |
| `tools-image-verify.sh`             | Verify required tools are installed in tools image                 | `./scripts/ci/tools-image-verify.sh --help`                              |
| `tools-image-update-digest.sh`      | Update pinned tools image digest in repo files                     | `./scripts/ci/tools-image-update-digest.sh --help`                       |
| `tools-image-detect-changes.sh`     | Detect tool file changes requiring fresh image build               | `./scripts/ci/tools-image-detect-changes.sh --help`                      |
| `tools-image-wait.sh`               | Wait for tools-image workflow to complete                          | `./scripts/ci/tools-image-wait.sh --help`                                |
| `tools-image-resolve.sh`            | Resolve tools image tag based on event context                     | `./scripts/ci/tools-image-resolve.sh --help`                             |
| `verify-manifest-tools.py`          | Verify tools in image match manifest versions                      | `python scripts/ci/verify-manifest-tools.py --help`                      |
| `verify-manifest-sync.py`           | Verify manifest versions match pyproject and package.json          | `python scripts/ci/verify-manifest-sync.py --help`                       |
| `verify-tool-version-sync.py`       | Verify tool versions match between package.json and pyproject.toml | `python scripts/ci/verify-tool-version-sync.py`                          |

#### Homebrew Scripts (`ci/homebrew/`)

Scripts for generating and updating Homebrew formulas.

| Script                       | Purpose                                            | Usage                                                      |
| ---------------------------- | -------------------------------------------------- | ---------------------------------------------------------- |
| `wait-for-pypi.sh`           | Poll PyPI until package version is available       | `./scripts/ci/homebrew/wait-for-pypi.sh lintro 1.0.0`      |
| `generate-pypi-formula.sh`   | Generate lintro.rb formula from PyPI               | `./scripts/ci/homebrew/generate-pypi-formula.sh 1.0.0 out` |
| `generate-binary-formula.sh` | Generate lintro-bin.rb formula for binaries        | `./scripts/ci/homebrew/generate-binary-formula.sh ...`     |
| `pypi_utils.py`              | Shared PyPI API utilities module                   | Imported by other Python scripts                           |
| `fetch_package_info.py`      | Fetch package tarball info from PyPI               | `python3 fetch_package_info.py lintro 1.0.0`               |
| `fetch_wheel_info.py`        | Fetch wheel info and generate resource stanzas     | `python3 fetch_wheel_info.py pydoclint --type universal`   |
| `render_formula.py`          | Render Homebrew formula from template              | `python3 render_formula.py --tarball-url ... -o out.rb`    |
| `generate_resources.py`      | Generate Homebrew resource stanzas (replaces poet) | `python3 generate_resources.py lintro --exclude pkg1 pkg2` |

### 🐳 Docker Scripts (`docker/`)

Scripts for containerized development and testing.

| Script                 | Purpose                         | Usage                                     |
| ---------------------- | ------------------------------- | ----------------------------------------- |
| `docker-build-test.sh` | Build and test Docker image     | `./scripts/docker/docker-build-test.sh`   |
| `docker-lintro.sh`     | Run Lintro in Docker container  | `./scripts/docker/docker-lintro.sh check` |
| `docker-test.sh`       | Run integration tests in Docker | `./scripts/docker/docker-test.sh`         |
| `entrypoint.sh`        | Docker container entrypoint     | Internal use by Dockerfile                |
| `fix-permissions.sh`   | Fix mounted volume permissions  | Internal use by Dockerfile                |

### 💻 Local Development Scripts (`local/`)

Scripts for local development and testing.

| Script                      | Purpose                                     | Usage                                       |
| --------------------------- | ------------------------------------------- | ------------------------------------------- |
| `local-lintro.sh`           | Enhanced local Lintro runner                | `./scripts/local/local-lintro.sh check`     |
| `sign-all-tags.sh`          | Re-sign annotated git tags (GPG/SSH) safely | `./scripts/local/sign-all-tags.sh --help`   |
| `validate-docker-buildx.sh` | Validate Docker Buildx driver configuration | `./scripts/local/validate-docker-buildx.sh` |
| `local-test.sh`             | Local test runner stub                      | `./scripts/local/local-test.sh --help`      |
| `run-tests.sh`              | Universal test runner (local + Docker)      | `./scripts/local/run-tests.sh`              |
| `normalize-ascii-art.sh`    | Normalize ASCII art to fixed size           | `./scripts/local/normalize-ascii-art.sh`    |
| `update-coverage-badge.sh`  | Update coverage badge from coverage.xml     | `./scripts/local/update-coverage-badge.sh`  |

Notes:

- Most scripts support `--help` for usage.
- `local-lintro.sh` supports `--install` to install missing tools and `--yes` for
  non-interactive acceptance.
- Set `COVERAGE_DEBUG=1` to enable verbose output in `extract-coverage.py`.

### 🛠️ Utility Scripts (`utils/`)

Shared utilities and helper scripts.

| Script                               | Purpose                                             | Usage                                                                   |
| ------------------------------------ | --------------------------------------------------- | ----------------------------------------------------------------------- |
| `check-pypi-version.py`              | Check if version exists on PyPI                     | `python scripts/utils/check-pypi-version.py <version>`                  |
| `create-release.py`                  | Create GitHub release with assets                   | `python scripts/utils/create-release.py <version>`                      |
| `delete-previous-lintro-comments.py` | Delete old PR comments                              | `python scripts/utils/delete-previous-lintro-comments.py`               |
| `merge_pr_comment.py`                | Merge-update PR comment body, collapsing history    | `python scripts/utils/merge_pr_comment.py --help`                       |
| `determine-release.py`               | Determine next release version from commits         | `python scripts/utils/determine-release.py`                             |
| `extract-coverage.py`                | Extract coverage from XML files                     | `python scripts/utils/extract-coverage.py`                              |
| `extract_comment_body.py`            | Extract comment body from GitHub API JSON by ID     | `python scripts/utils/extract_comment_body.py <json> <comment_id>`      |
| `extract-version.py`                 | Print `version=X.Y.Z` from TOML                     | `python scripts/utils/extract-version.py`                               |
| `find_comment_with_marker.py`        | Find GitHub comment ID containing a specific marker | `python scripts/utils/find_comment_with_marker.py <json> <marker>`      |
| `generate_docs.py`                   | Generate documentation from docstrings              | `python scripts/utils/generate_docs.py`                                 |
| `install-tools.sh`                   | Install external tools (hadolint, prettier, etc.)   | `./scripts/utils/install-tools.sh [--dry-run] [--verbose] --local`      |
| `install.sh`                         | Install Lintro with dependencies                    | `./scripts/utils/install.sh`                                            |
| `json_encode_body.py`                | JSON encode comment body for GitHub API requests    | `python scripts/utils/json_encode_body.py <file_or_stdin>`              |
| `update-version.py`                  | Update version in pyproject.toml                    | `python scripts/utils/update-version.py <version>`                      |
| `utils.sh`                           | Shared utilities for other scripts                  | Sourced by other scripts                                                |
| `bootstrap-env.sh`                   | Bootstrap CI env with uv and tools                  | `./scripts/utils/bootstrap-env.sh [--dry-run] [--verbose] --help`       |
| `install-uv.sh`                      | Install uv from GitHub Releases                     | `./scripts/utils/install-uv.sh [--dry-run] [--verbose]`                 |
| `setup-python.sh`                    | Install/configure specific Python via uv            | `./scripts/utils/setup-python.sh [--dry-run] [--verbose] [3.13]`        |
| `sync-deps.sh`                       | Sync Python dependencies via uv                     | `./scripts/utils/sync-deps.sh [--dry-run] [--verbose] [--dev/--no-dev]` |
| `bump_deps.py`                       | Bump exact pinned versions in pyproject             | `uv run python scripts/utils/bump_deps.py --help`                       |
| `convert_asserts_to_assertpy.py`     | Migrate bare asserts in tests to assertpy           | `uv run python scripts/utils/convert_asserts_to_assertpy.py`            |

## 🔍 Detailed Script Documentation

### CI/CD Scripts

#### `ci-lintro.sh`

Runs Lintro analysis in Docker for CI pipeline.

**Features:**

- Runs Lintro in Docker container
- Excludes test files via `.lintro-ignore`
- Generates GitHub Actions summaries
- Stores exit code for PR comment step

**Usage:**

```bash
./scripts/ci/ci-lintro.sh
```

#### `coverage-badge-update.sh`

Generates and updates the coverage badge with color coding.

**Features:**

- Extracts coverage percentage from coverage.xml
- Generates SVG badge with color coding (green ≥80%, yellow ≥60%, red <60%)
- Commits and pushes badge updates in CI
- Creates default badge if no coverage data

**Usage:**

```bash
./scripts/ci/coverage-badge-update.sh
```

#### `extract-test-summary.sh`

Extracts test results from pytest output and generates a JSON summary for PR comments.

**Features:**

- Parses standard pytest output format (`N passed, M failed, K skipped in X.XXs`)
- Parses lintro table format output (pipe-separated columns)
- Extracts coverage data from `coverage.xml` when present
- Falls back to environment variables when no input file is provided
- Supports quiet mode (`--quiet`) to suppress console output

**JSON Output Structure:**

```json
{
  "tests": {
    "passed": 100,
    "failed": 2,
    "skipped": 5,
    "errors": 0,
    "total": 107,
    "duration": 12.34
  },
  "coverage": {
    "percentage": 85.5,
    "lines_covered": 1200,
    "lines_total": 1404,
    "lines_missing": 204,
    "files": 42
  }
}
```

**Usage:**

```bash
# Extract from pytest output file
./scripts/ci/testing/extract-test-summary.sh test-output.log test-summary.json

# Extract with quiet mode (no console output)
./scripts/ci/testing/extract-test-summary.sh --quiet test-output.log

# Use environment variables (when file not provided)
export TEST_PASSED=100 TEST_FAILED=0 TEST_TOTAL=100
./scripts/ci/testing/extract-test-summary.sh "" test-summary.json
```

**Integration:**

This script integrates with `coverage-pr-comment.sh` which reads the generated
`test-summary.json` to build PR comment content. The JSON format uses single-space after
colons for compatibility with grep-based parsers.

#### `sbom-generate.sh`

Generate and export SBOMs using `bomctl` with optional merge and multiple output formats
(CycloneDX/SPDX). Supports dry-run planning. The script requires the `bomctl` binary to
be installed (no container fallback).

Features:

- Fetch from GitHub dependency graph (public repos) via `bomctl fetch`
- Import local SBOM files and optionally merge them
- Export CycloneDX (1.5/1.6) JSON/XML and SPDX 2.3 JSON files
- Dry-run mode to preview actions; optional `--netrc` for private repos

Usage:

```bash
# Show help
./scripts/ci/sbom-generate.sh --help

# Basic: fetch current repo and export CycloneDX 1.5 JSON to dist/sbom/
./scripts/ci/sbom-generate.sh

# Multiple formats and XML encoding for CycloneDX
./scripts/ci/sbom-generate.sh \
  --format cyclonedx-1.6 --format spdx-2.3 \
  --encoding xml \
  --output-dir dist/sbom

# Import additional SBOMs and merge
./scripts/ci/sbom-generate.sh \
  --skip-fetch \
  --import sboms/app.cdx.json \
  --import sboms/image.cdx.json \
  --alias combined --name lintro-sbom

# Dry run to preview commands
./scripts/ci/sbom-generate.sh --dry-run
```

Notes:

- For private GitHub repos, use `--netrc` with a configured `~/.netrc`.
- Outputs are written under `dist/sbom/` by default.

#### `sbom-rename-artifacts.sh`

Rename SBOM artifacts to include the current tag and commit SHA for easier traceability.

Usage:

```bash
./scripts/ci/sbom-rename-artifacts.sh dist/sbom
```

#### `sbom-attest-artifacts.sh`

Create keyless cosign attestations for generated SBOM artifacts (best effort).

Usage:

```bash
./scripts/ci/sbom-attest-artifacts.sh dist/sbom
```

### Docker Scripts

#### `docker-lintro.sh`

Run Lintro in a Docker container without installing dependencies locally.

**Features:**

- Builds Docker image if not exists
- Mounts current directory to container
- Handles permission issues
- Delegates to local-lintro.sh inside container

**Usage:**

```bash
# Basic check
./scripts/docker/docker-lintro.sh check

# With specific tools
./scripts/docker/docker-lintro.sh check --tools ruff,prettier

# Format code
./scripts/docker/docker-lintro.sh format --tools ruff
```

#### `docker-test.sh`

Run integration tests in Docker container with all tools pre-installed.

**Features:**

- Uses Docker Compose for test environment
- All tools pre-installed in container
- Delegates to run-tests.sh inside container
- Provides clear success/failure output

**Usage:**

```bash
./scripts/docker/docker-test.sh
```

### Local Development Scripts

#### `local-lintro.sh`

Enhanced local Lintro runner with automatic tool installation.

**Features:**

- Automatically sets up Python environment with uv
- Checks for missing tools and offers installation
- Works in both local and Docker environments
- Provides helpful error messages and tips

**Usage:**

```bash
# Basic usage
./scripts/local/local-lintro.sh check

# Install missing tools first
./scripts/local/local-lintro.sh --install check

# Format code
./scripts/local/local-lintro.sh format --tools ruff
```

#### `run-tests.sh`

Universal test runner that works both locally and in Docker.

**Features:**

- Automatically sets up Python environment
- Runs all tests with coverage reporting
- Generates HTML, XML, and terminal coverage reports
- Handles Docker environment differences
- Copies coverage files to host directory in Docker

**Usage:**

```bash
# Run tests
./scripts/local/run-tests.sh

# Verbose output
./scripts/local/run-tests.sh --verbose
```

#### `update-coverage-badge.sh`

Updates the coverage badge based on current coverage.xml file.

**Features:**

- Extracts coverage percentage from coverage.xml
- Generates SVG badge with appropriate color coding
- Updates assets/images/coverage-badge.svg file locally
- Provides helpful error messages if coverage.xml missing

**Usage:**

```bash
# Update badge from existing coverage.xml
./scripts/local/update-coverage-badge.sh

# Run tests then update badge
./scripts/local/run-tests.sh && ./scripts/local/update-coverage-badge.sh
```

### Utility Scripts

#### `install-tools.sh`

Installs all external tools required by Lintro.

**Features:**

- Installs hadolint, prettier, ruff, yamllint, pydoclint
- Supports local and Docker installation modes
- Uses consistent installation methods
- Verifies installations

**Usage:**

```bash
# Local installation
./scripts/utils/install-tools.sh --local

# Docker installation
./scripts/utils/install-tools.sh --docker
```

#### `utils.sh`

Shared utilities used by multiple scripts. See
[Shell Script Style Guide](../docs/SHELL-SCRIPT-STYLE-GUIDE.md) for usage patterns.

**Logging Functions:**

| Function      | Purpose               | Example                      |
| ------------- | --------------------- | ---------------------------- |
| `log_info`    | Blue info message     | `log_info "Processing..."`   |
| `log_success` | Green success message | `log_success "Done!"`        |
| `log_warning` | Yellow warning        | `log_warning "File missing"` |
| `log_error`   | Red error message     | `log_error "Failed"`         |
| `log_verbose` | Debug (VERBOSE=1)     | `log_verbose "Details..."`   |

**GitHub Actions Helpers:**

| Function                | Purpose                  | Example                               |
| ----------------------- | ------------------------ | ------------------------------------- |
| `set_github_output`     | Set workflow output      | `set_github_output "version" "1.0.0"` |
| `set_github_env`        | Set environment variable | `set_github_env "MY_VAR" "value"`     |
| `configure_git_ci_user` | Set github-actions[bot]  | `configure_git_ci_user`               |

**Utility Functions:**

| Function                  | Purpose               | Example                             |
| ------------------------- | --------------------- | ----------------------------------- |
| `create_temp_dir`         | Temp dir with cleanup | `tmpdir=$(create_temp_dir)`         |
| `show_help`               | Display help message  | `show_help "script" "desc" "usage"` |
| `get_coverage_percentage` | Extract from XML      | `pct=$(get_coverage_percentage)`    |
| `check_file_exists`       | Check and log         | `check_file_exists "$f" "Config"`   |
| `check_dir_exists`        | Check and log         | `check_dir_exists "$d" "Output"`    |

**Usage:**

```bash
# Source in other scripts
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/utils.sh"
```

## 🔧 Script Dependencies

### System Requirements

- **Bash**: All shell scripts require bash
- **Python 3.11+**: For Python utility scripts
- **Docker**: For Docker-related scripts
- **uv**: Python package manager

### External Tools (installed by `install-tools.sh`)

- **hadolint**: Docker linting
- **prettier**: JavaScript/JSON formatting
- **ruff**: Python linting and formatting
- **yamllint**: YAML linting
- **pydoclint**: Python docstring validation

### GitHub Actions Requirements

- **GITHUB_TOKEN**: For PR comment scripts
- **GITHUB_REPOSITORY**: Repository information
- **GITHUB_RUN_ID**: Workflow run ID

## 🚨 Troubleshooting

### Common Issues

<!-- markdownlint-disable MD029 -->

1. **Permission Denied**

```bash
chmod +x scripts/**/*.sh
```

2. **Missing Tools**

```bash
./scripts/utils/install-tools.sh --local
```

3. **Docker Not Running**

```bash
# Start Docker Desktop or Docker daemon
docker info
```

4. **Python Environment Issues**

```bash
# Reinstall with uv
uv sync --dev
```

### Getting Help

- **Script Help**: Most scripts support `--help` flag
- **Verbose Output**: Use `--verbose` flag for detailed output
- **Debug Mode**: Set `VERBOSE=1` environment variable

## 📝 Contributing

When adding new scripts:

1. **Follow the style guide**: See
   [Shell Script Style Guide](../docs/SHELL-SCRIPT-STYLE-GUIDE.md)
2. **Use standard preamble**: `set -euo pipefail` and source `utils.sh`
3. **Add help documentation** with `--help` flag
4. **Use shared utilities** from `utils.sh` (don't duplicate logging, colors, etc.)
5. **Add to this README** with purpose and usage
6. **Test in both local and Docker environments**

## 🔗 Related Documentation

- [Main README](../README.md) - Project overview
- [Getting Started](../docs/getting-started.md) - Installation guide
- [Docker Usage](../docs/docker.md) - Docker documentation
- [GitHub Integration](../docs/github-integration.md) - CI/CD setup

## CI Scripts

- `ci/codecov-upload.sh`: Legacy helper to download and run the Codecov uploader via
  GitHub CLI with checksum verification. Prefer using the official GitHub Action in the
  workflow:

  ```yaml
  - name: Upload coverage to Codecov
    if: success()
    uses: codecov/codecov-action@v5
    with:
      files: coverage.xml
      flags: python-3.14
      fail_ci_if_error: true
      # token: ${{ secrets.CODECOV_TOKEN }} # for private repos only
  ```

  - Notes:
    - Requires `gh` (GitHub CLI) available on the runner.
    - In GitHub Actions, `gh` expects `GH_TOKEN`. The script will automatically set
      `GH_TOKEN` from `GITHUB_TOKEN`.
    - Set `CODECOV_VERSION` (and optionally `CODECOV_SHA256`) via organization or repo
      vars.
