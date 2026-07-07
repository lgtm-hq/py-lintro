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
├── local/        # Local development scripts
└── utils/        # Utility scripts and shared functions
```

## 🚀 Quick Start

### For New Contributors

<!-- markdownlint-disable MD029 -- numbering restarts after nested blocks -->

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
| `build_linux.py` | Build Linux binary using Nuitka compiler | `uv run python scripts/build/build_linux.py` |

### 📦 npm Distribution Scripts (`ci/npm/`)

Scripts that package the platform binaries into the npm meta-package + per-platform
packages and (dry-run) publish them. See the
[npm distribution design](../docs/npm-distribution.md).

| Script                         | Purpose                                                    | Usage                                                           |
| ------------------------------ | ---------------------------------------------------------- | --------------------------------------------------------------- |
| `sync_npm_version.py`          | Sync/check versions across all `npm/*/package.json`        | `python scripts/ci/npm/sync_npm_version.py --version 1.2.3`     |
| `stage_binaries.py`            | Copy downloaded platform binaries into the npm tree        | `python scripts/ci/npm/stage_binaries.py --artifacts-dir <dir>` |
| `download_release_binaries.sh` | Download release binaries for staging                      | `./scripts/ci/npm/download_release_binaries.sh v1.2.3 <dir>`    |
| `smoke_test.sh`                | Pack + install the meta-package and run `lintro --version` | `./scripts/ci/npm/smoke_test.sh`                                |
| `publish_packages.sh`          | Publish npm packages (dry-run unless `LIVE=1`)             | `./scripts/ci/npm/publish_packages.sh`                          |

### 🍺 Homebrew Formulas (`ci/homebrew/`)

Formula templates and generators live under `scripts/ci/homebrew/` (see Homebrew Scripts
below). Release automation writes `Formula/lintro.rb` (binary) and
`Formula/lintro-full.rb` (PyPI full install).

### 🔧 CI/CD Scripts (`ci/`)

Scripts for GitHub Actions workflows and continuous integration.

| Script                               | Purpose                                                               | Usage                                                                              |
| ------------------------------------ | --------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `coverage-manager.sh`                | Unified coverage ops (extract/badge/comment/threshold)                | `./scripts/utils/coverage-manager.sh --help`                                       |
| `ci-log.sh`                          | Generic CI logging utility for workflow status messages               | `./scripts/ci/ci-log.sh <message>`                                                 |
| `ci-post-pr-comment.sh`              | Post comments to PRs using GitHub API                                 | `./scripts/ci/ci-post-pr-comment.sh [file]`                                        |
| `post-pr-delete-previous.sh`         | Delete previous PR comments by marker                                 | `./scripts/ci/post-pr-delete-previous.sh --help`                                   |
| `lintro-report-generate.sh`          | Generate comprehensive Lintro reports                                 | `./scripts/ci/lintro-report-generate.sh`                                           |
| `pull-lintro-image.sh`               | Pull lintro Docker image from GHCR and log digest                     | `./scripts/ci/testing/pull-lintro-image.sh`                                        |
| `maintenance/delete-ci-ghcr-tags.sh` | Delete ephemeral CI GHCR tags after PR merge or close                 | `./scripts/ci/maintenance/delete-ci-ghcr-tags.sh`                                  |
| `coverage-badge-update.sh`           | Generate and update coverage badge                                    | `./scripts/ci/coverage-badge-update.sh --help`                                     |
| `sbom-generate.sh`                   | Generate and export SBOMs via bomctl                                  | `./scripts/ci/sbom-generate.sh --help`                                             |
| `egress-audit-lite.sh`               | Audit reachability of allowed endpoints                               | `./scripts/ci/egress-audit-lite.sh --help`                                         |
| `detect-changes.sh`                  | Detect repo diffs and set has_changes output                          | `./scripts/ci/detect-changes.sh --help`                                            |
| `detect-fork-pr.sh`                  | Detect fork PRs and set `is-fork` output for conditional steps        | `EVENT_NAME=pull_request ./scripts/ci/detect-fork-pr.sh`                           |
| `evaluate-test-gate.sh`              | Evaluate upstream compat/coverage results for required-check gate     | `COMPAT_RESULT=success COVERAGE_RESULT=success ./scripts/ci/evaluate-test-gate.sh` |
| `fail-on-security-audit.sh`          | Fail CI when security audit finds vulnerabilities                     | `./scripts/ci/fail-on-security-audit.sh`                                           |
| `free-disk-space.sh`                 | Free disk space on CI runner for Docker builds                        | `./scripts/ci/free-disk-space.sh`                                                  |
| `security-comment.sh`                | Run osv-scanner via lintro in Docker and generate security PR comment | `./scripts/ci/security-comment.sh --help`                                          |
| `run-ai-review.sh`                   | Dogfood `lintro review` on a PR using trusted base-branch lintro      | `PR_NUMBER=123 ./scripts/ci/run-ai-review.sh`                                      |
| `enable_review_config.py`            | Enable AI review + cost cap in `.lintro-config.yaml` for a CI run     | `python3 scripts/ci/enable_review_config.py --help`                                |
| `classify-osv-results.py`            | Classify osv_scanner JSON as ok, vulns, or error for CI status        | `python3 scripts/ci/classify-osv-results.py osv-results.json`                      |
| `format-security-comment.py`         | Format lintro osv_scanner JSON as security PR comment markdown        | `python3 scripts/ci/format-security-comment.py osv-results.json`                   |
| `test-install-package.sh`            | Install and verify built package in isolated venv                     | `./scripts/ci/test-install-package.sh wheel`                                       |
| `test-built-package-integration.sh`  | Run integration tests for built package in isolated venv              | `./scripts/ci/test-built-package-integration.sh`                                   |
| `test-venv-setup.sh`                 | Create isolated Python 3.13 virtual environment                       | `./scripts/ci/test-venv-setup.sh`                                                  |
| `test-verify-cli.sh`                 | Verify lintro CLI entry points in installed package                   | `./scripts/ci/test-verify-cli.sh`                                                  |
| `test-verify-imports.sh`             | Verify critical package imports in installed lintro                   | `./scripts/ci/test-verify-imports.sh wheel`                                        |
| `extract-test-summary.sh`            | Extract pytest test summary to JSON for PR comments                   | `./scripts/ci/testing/extract-test-summary.sh <log> <out.json>`                    |
| `load-ci-docker-images.sh`           | Load Docker images from CI tarball artifact                           | `./scripts/ci/testing/load-ci-docker-images.sh`                                    |
| `pull-ci-docker-images.sh`           | Pull CI Docker images from GHCR for testing                           | `./scripts/ci/testing/pull-ci-docker-images.sh`                                    |
| `resolve-vue-tsc-version.sh`         | Read installed vue-tsc version from bun's global install root         | `./scripts/ci/resolve-vue-tsc-version.sh --help`                                   |
| `verify-manifest-tools.py`           | Verify tools in image match manifest versions                         | `python scripts/ci/verify-manifest-tools.py --help`                                |
| `generate-tool-versions.py`          | Generate `_generated_versions.py` and sync `manifest.json` versions   | `python scripts/ci/generate-tool-versions.py [--check]`                            |
| `stage-python-coverage-html.sh`      | Stage flat HTML coverage for GitHub Pages bundling                    | `./scripts/ci/testing/stage-python-coverage-html.sh --help`                        |

#### Documentation Site Scripts (`ci/site/`)

Scripts for building, testing, and deploying the Astro documentation site at
`apps/site/`.

| Script                          | Purpose                                                    | Usage                                                    |
| ------------------------------- | ---------------------------------------------------------- | -------------------------------------------------------- |
| `build.sh`                      | Build the docs site for GitHub Pages                       | `./scripts/ci/site/build.sh --help`                      |
| `check.sh`                      | Run Astro type-check (`astro check`)                       | `./scripts/ci/site/check.sh --help`                      |
| `test.sh`                       | Run Vitest with coverage in `apps/site`                    | `./scripts/ci/site/test.sh --help`                       |
| `test-python.sh`                | Run pytest for site maintenance scripts                    | `./scripts/ci/site/test-python.sh --help`                |
| `test-all.sh`                   | Run Vitest and site script pytest                          | `./scripts/ci/site/test-all.sh --help`                   |
| `preview-serve.sh`              | Serve built `dist/` with production `ASTRO_BASE`           | `./scripts/ci/site/preview-serve.sh --help`              |
| `preview-pages-local.sh`        | Build Pages-like dist with optional local coverage bundles | `./scripts/ci/site/preview-pages-local.sh --help`        |
| `prepare-lychee-action-args.sh` | Prepare lychee-action args for post-build link checking    | `./scripts/ci/site/prepare-lychee-action-args.sh --help` |
| `migrate-docs-content.py`       | Copy `docs/` into `apps/site/src/content/docs/`            | `uv run python scripts/ci/site/migrate-docs-content.py`  |
| `fix-markdown-docs.py`          | Fix markdownlint issues in migrated Astro docs content     | `uv run python scripts/ci/site/fix-markdown-docs.py`     |

#### Homebrew Scripts (`ci/homebrew/`)

Scripts for generating and updating Homebrew formulas.

| Script                       | Purpose                                            | Usage                                                                                                           |
| ---------------------------- | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `wait-for-pypi.sh`           | Poll PyPI until package version is available       | `./scripts/ci/homebrew/wait-for-pypi.sh lintro 1.0.0`                                                           |
| `get-release-info.sh`        | Resolve release tag and prerelease metadata        | `GITHUB_EVENT_NAME=workflow_dispatch ./scripts/ci/homebrew/get-release-info.sh`                                 |
| `create-tap-pr.sh`           | Create/update Homebrew tap PR                      | `./scripts/ci/homebrew/create-tap-pr.sh Formula/lintro.rb Formula/lintro-full.rb "chore(homebrew): update ..."` |
| `create-lintro-tap-pr.sh`    | Create/update lintro's generated formula PR        | `./scripts/ci/homebrew/create-lintro-tap-pr.sh 1.0.0 --skip-if-empty`                                           |
| `generate-pypi-formula.sh`   | Generate lintro.rb formula from PyPI               | `./scripts/ci/homebrew/generate-pypi-formula.sh 1.0.0 out`                                                      |
| `generate-binary-formula.sh` | Generate `Formula/lintro.rb` for binary releases   | `./scripts/ci/homebrew/generate-binary-formula.sh ...`                                                          |
| `pypi_utils.py`              | Shared PyPI API utilities module                   | Imported by other Python scripts                                                                                |
| `fetch_package_info.py`      | Fetch package tarball info from PyPI               | `python3 scripts/ci/homebrew/fetch_package_info.py lintro 1.0.0`                                                |
| `fetch_wheel_info.py`        | Fetch wheel info and generate resource stanzas     | `python3 scripts/ci/homebrew/fetch_wheel_info.py pydoclint --type universal`                                    |
| `render_formula.py`          | Render Homebrew formula from template              | `python3 scripts/ci/homebrew/render_formula.py --tarball-url ... -o out.rb`                                     |
| `generate_resources.py`      | Generate Homebrew resource stanzas (replaces poet) | `python3 scripts/ci/homebrew/generate_resources.py lintro --exclude pkg1 pkg2`                                  |

### 🐳 Docker Scripts (`docker/`)

Scripts for containerized development and testing.

| Script                      | Purpose                                                 | Usage                                        |
| --------------------------- | ------------------------------------------------------- | -------------------------------------------- |
| `docker-build-test.sh`      | Build and test Docker image                             | `./scripts/docker/docker-build-test.sh`      |
| `docker-lintro.sh`          | Run Lintro in Docker container                          | `./scripts/docker/docker-lintro.sh check`    |
| `docker-test.sh`            | Run integration tests in Docker                         | `./scripts/docker/docker-test.sh`            |
| `entrypoint.sh`             | Docker container entrypoint                             | Internal use by Dockerfile                   |
| `fix-permissions.sh`        | Fix mounted volume permissions                          | Internal use by Dockerfile                   |
| `run-docker-test-suite.sh`  | Run the full Docker test suite against built images     | `./scripts/docker/run-docker-test-suite.sh`  |
| `save-ci-images-tarball.sh` | Save built Docker images as tarball for downstream jobs | `./scripts/docker/save-ci-images-tarball.sh` |
| `smoke-test-base-image.sh`  | Smoke-test the base Docker image                        | `./scripts/docker/smoke-test-base-image.sh`  |

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
| `delete-previous-lintro-comments.py` | Delete old PR comments                              | `python scripts/utils/delete-previous-lintro-comments.py`               |
| `merge_pr_comment.py`                | Merge-update PR comment body, collapsing history    | `python scripts/utils/merge_pr_comment.py --help`                       |
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

`test-ci.yml` uses lgtm-ci `reusable-test-python.yml` for coverage PR comments. The JSON
format uses single-space after colons for compatibility with grep-based parsers.

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

<!-- markdownlint-disable MD029 -- numbering restarts after nested blocks -->

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
