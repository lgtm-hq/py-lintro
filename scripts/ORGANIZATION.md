# Script Organization Guide

<!-- markdownlint-disable MD036 -- bold text used as descriptive subtitle, not as a heading -->

**For Contributors & Maintainers**

<!-- markdownlint-enable MD036 -->

This document defines clear responsibility boundaries for script organization following
SOLID principles. Use this as a reference when adding, modifying, or reviewing scripts
in the project.

## 📁 Directory Structure & Responsibilities

### `scripts/ci/` - Continuous Integration Scripts

**Purpose**: Scripts specifically designed for GitHub Actions workflows and CI/CD
pipelines.

**Responsibilities**:

- ✅ Workflow orchestration and automation
- ✅ CI-specific environment setup and teardown
- ✅ GitHub Actions integration (environment variables, outputs)
- ✅ PR comment generation and management
- ✅ Release automation and tagging
- ✅ Security audit and validation

**Characteristics**:

- Expect GitHub Actions environment variables (`GITHUB_TOKEN`, `GITHUB_REPOSITORY`,
  etc.)
- Handle CI-specific error reporting and logging
- Designed for headless execution
- Integration with GitHub APIs and services

**Examples**:

- `ci-lintro.sh` - Run lintro in CI context
- `ci-post-pr-comment.sh` - Post comments to PRs via GitHub API
- `auto-tag-unified.sh` - Automated tagging in CI
- `semantic-pr-title-check.sh` - PR title validation

### `scripts/local/` - Local Development Scripts

**Purpose**: Scripts for local development, testing, and developer productivity.

**Responsibilities**:

- ✅ Local development environment setup
- ✅ Interactive developer tools and utilities
- ✅ Local testing and validation
- ✅ Developer convenience functions

**Characteristics**:

- Interactive execution with user prompts
- Local environment assumptions (can install tools globally)
- Developer-friendly output and error messages
- May require user interaction or confirmation

**Examples**:

- `local-lintro.sh` - Interactive lintro execution with tool installation
- `run-tests.sh` - Comprehensive local test runner
- `local-test.sh` - Quick local testing
- `sign-all-tags.sh` - Interactive GPG tag signing

### `scripts/utils/` - Reusable Utility Components

**Purpose**: Single-responsibility, reusable components that can be used by both CI and
local scripts.

**Responsibilities**:

- ✅ Pure functions and single-purpose utilities
- ✅ Data processing and transformation
- ✅ Environment-agnostic operations
- ✅ Shared functionality between CI and local contexts

**Characteristics**:

- No assumptions about execution environment (CI vs local)
- Focus on single responsibility (SRP compliance)
- Standardized interface (`--help`, `--dry-run`, `--verbose`)
- Can be composed into larger workflows
- Minimal external dependencies

**Examples**:

- `install-uv.sh` - Install uv binary (SRP: installation only)
- `setup-python.sh` - Configure Python version (SRP: Python setup only)
- `sync-deps.sh` - Synchronize dependencies (SRP: dependency sync only)
- `coverage-manager.sh` - Coverage operations with subcommands
- `extract-version.py` - Extract version from pyproject.toml
- `utils.sh` - Shared utility functions

### `scripts/docker/` - Docker-Specific Scripts

**Purpose**: Scripts specifically for Docker container operations.

**Responsibilities**:

- ✅ Docker image building and testing
- ✅ Container-based execution environments
- ✅ Docker Compose orchestration

## 🚫 Anti-Patterns to Avoid

### Cross-Boundary Violations

❌ **CI scripts with local assumptions** (e.g., assuming interactive terminals) ❌
**Local scripts with CI-only dependencies** (e.g., requiring GITHUB_TOKEN) ❌ **Utils
scripts with environment assumptions** (e.g., hardcoded paths) ❌ **Mixed
responsibilities** (e.g., a single script doing installation + execution + reporting)

### Examples of Boundary Violations to Fix

- `scripts/local/update-coverage-badge.sh` - Should use
  `scripts/utils/coverage-manager.sh badge`
- Mixed coverage scripts - Consolidated into `scripts/utils/coverage-manager.sh`
- `bootstrap-env.sh` - Split into focused utils components

## 📋 Migration Guidelines

When moving or refactoring scripts:

1. **Identify Primary Purpose**: What is the script's main responsibility?
2. **Check Dependencies**: Does it require CI-specific or local-specific features?
3. **Extract Utils**: Can parts be extracted into reusable utils?
4. **Standardize Interface**: Ensure `--help`, `--dry-run`, `--verbose` support
5. **Update Callers**: Update workflows and scripts that reference the moved script
6. **Document Changes**: Update this file and scripts/README.md

## ✅ Compliance Checklist

For any script to be considered compliant with these boundaries:

- [ ] Placed in correct directory based on primary responsibility
- [ ] Does not violate environment assumptions for its category
- [ ] Follows Single Responsibility Principle
- [ ] Implements standard interface (`--help` at minimum)
- [ ] Documents its purpose and requirements clearly
- [ ] Does not duplicate functionality available in utils/

## 🔍 Script Audit Guidelines

When reviewing or contributing scripts, use this checklist:

### Before Adding New Scripts

1. **Determine correct directory** based on primary purpose and dependencies
2. **Check for existing functionality** in `utils/` to avoid duplication
3. **Follow naming conventions**: `kebab-case.sh` for shell, `snake_case.py` for Python
4. **Implement standard interface** (`--help` minimum, `--dry-run`/`--verbose`
   preferred)

### When Modifying Existing Scripts

1. **Verify directory placement** - does current location match responsibility?
2. **Extract reusable parts** to `utils/` if they could benefit other scripts
3. **Check environment assumptions** - does script match its directory's
   characteristics?
4. **Update documentation** if changing interfaces or moving files

### Code Review Focus Areas

- **Single Responsibility**: Does the script do exactly one thing well?
- **Environment Assumptions**: Are CI/local assumptions appropriate for the directory?
- **Error Handling**: Proper exit codes and user-friendly error messages?
- **Documentation**: Clear help text and purpose statement?
