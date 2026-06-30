"""Tests for review file classification."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.review.classifier import classify_changed_files
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.models.changed_file import ChangedFile


@pytest.mark.parametrize(
    ("path", "expected_domains"),
    [
        pytest.param(
            "test_samples/tools/shell/shellcheck/shellcheck_clean.sh",
            {FileDomain.SHELL, FileDomain.SOURCE},
            id="shell_script",
        ),
        pytest.param(
            ".github/workflows/ci.yml",
            {FileDomain.CI},
            id="workflow",
        ),
        pytest.param(
            ".github/dependabot.yml",
            {FileDomain.CI},
            id="github_root_automation_config_yml",
        ),
        pytest.param(
            ".github/renovate.yaml",
            {FileDomain.CI},
            id="github_root_automation_config_yaml",
        ),
        pytest.param(
            ".github/workflows/auth-gate.yml",
            {FileDomain.CI, FileDomain.SECURITY},
            id="security_workflow",
        ),
        pytest.param(
            ".github/workflows/security-lint.yml",
            {FileDomain.CI, FileDomain.SECURITY},
            id="security_workflow_directory_token",
        ),
        pytest.param(
            "src/auth_service/login.py",
            {FileDomain.SOURCE, FileDomain.SECURITY},
            id="security_directory_token",
        ),
        pytest.param(
            ".github/actions/deploy/action.yml",
            {FileDomain.CI},
            id="nested_ci_action",
        ),
        pytest.param(
            "test_samples/tools/python/ruff/ruff_clean.py",
            {FileDomain.SOURCE},
            id="python_source",
        ),
        pytest.param(
            "tests/test_main.py",
            {FileDomain.TEST},
            id="python_test",
        ),
        pytest.param(
            "test_samples/tools/config/markdown/markdownlint_violations.md",
            {FileDomain.DOCS},
            id="docs",
        ),
        pytest.param(
            "packages/pkg-a/.pre-commit-config.yaml",
            {FileDomain.CONFIG},
            id="nested_pre_commit_config",
        ),
        pytest.param(
            "docs/nested/guide.md",
            {FileDomain.DOCS},
            id="nested_docs",
        ),
        pytest.param(
            "src/api/v2/handlers.py",
            {FileDomain.SOURCE, FileDomain.API},
            id="nested_api_module",
        ),
        pytest.param(
            "src/api/README.md",
            {FileDomain.API, FileDomain.DOCS},
            id="api_tree_readme_not_source",
        ),
        pytest.param(
            "src/pkg/util.py",
            {FileDomain.SOURCE},
            id="deep_nested_source",
        ),
        pytest.param(
            "scripts/security/auth.py",
            {FileDomain.SHELL, FileDomain.SOURCE, FileDomain.SECURITY},
            id="security_script",
        ),
        pytest.param(
            "src/auth.py",
            {FileDomain.SOURCE, FileDomain.SECURITY},
            id="security_filename",
        ),
        pytest.param(
            "main.py",
            {FileDomain.SOURCE},
            id="root_python",
        ),
        pytest.param(
            "README.md",
            {FileDomain.DOCS},
            id="root_markdown",
        ),
        pytest.param(
            "test_samples/tools/security/cargo_audit/Cargo.lock",
            {FileDomain.DEPS},
            id="deps_lockfile",
        ),
        pytest.param(
            "test_samples/tools/rust/clippy/Cargo.toml",
            {FileDomain.CONFIG, FileDomain.DEPS},
            id="config_manifest",
        ),
        pytest.param(
            "bun.lock",
            {FileDomain.DEPS},
            id="bun_lockfile",
        ),
        pytest.param(
            "api/users.py",
            {FileDomain.SOURCE, FileDomain.API},
            id="root_api_module",
        ),
        pytest.param(
            "Makefile",
            {FileDomain.SOURCE},
            id="makefile_fallback_source",
        ),
        pytest.param(
            "Dockerfile",
            {FileDomain.SOURCE},
            id="dockerfile_fallback_source",
        ),
        pytest.param(
            "justfile",
            {FileDomain.SOURCE},
            id="justfile_fallback_source",
        ),
        pytest.param(
            "scripts/Makefile",
            {FileDomain.SHELL},
            id="script_makefile_shell_only",
        ),
        pytest.param(
            "scripts/Dockerfile",
            {FileDomain.SHELL},
            id="script_dockerfile_shell_only",
        ),
        pytest.param(
            "scripts/justfile",
            {FileDomain.SHELL},
            id="script_justfile_shell_only",
        ),
        pytest.param(
            "scripts/package.json",
            {FileDomain.SHELL, FileDomain.CONFIG, FileDomain.DEPS},
            id="script_package_json_not_source",
        ),
        pytest.param(
            "package.json",
            {FileDomain.CONFIG, FileDomain.DEPS},
            id="root_package_json",
        ),
        pytest.param(
            "pyproject.toml",
            {FileDomain.CONFIG, FileDomain.DEPS},
            id="root_pyproject_toml",
        ),
        pytest.param(
            "go.mod",
            {FileDomain.DEPS},
            id="root_go_mod",
        ),
        pytest.param(
            "composer.json",
            {FileDomain.CONFIG, FileDomain.DEPS},
            id="root_composer_json",
        ),
        pytest.param(
            "scripts/requirements-dev.txt",
            {FileDomain.SHELL, FileDomain.DEPS},
            id="script_requirements_not_source",
        ),
        pytest.param(
            "build",
            {FileDomain.SOURCE},
            id="extensionless_non_doc_fallback",
        ),
        pytest.param(
            "scripts/auth/README.md",
            {FileDomain.SHELL, FileDomain.DOCS},
            id="scripts_auth_readme_not_security",
        ),
        pytest.param(
            "scripts/README.rst",
            {FileDomain.SHELL, FileDomain.DOCS},
            id="scripts_rst_readme",
        ),
        pytest.param(
            "scripts/NOTES.txt",
            {FileDomain.SHELL},
            id="scripts_txt_notes",
        ),
        pytest.param(
            "scripts/auth/CHANGELOG",
            {FileDomain.SHELL, FileDomain.DOCS},
            id="scripts_auth_changelog_not_security",
        ),
        pytest.param(
            "subpackage/requirements-dev.txt",
            {FileDomain.DEPS},
            id="nested_requirements_txt",
        ),
        pytest.param(
            "subpackage/constraints-dev.txt",
            {FileDomain.DEPS},
            id="nested_constraints_txt",
        ),
        pytest.param(
            ".osv-scanner.toml",
            {FileDomain.CONFIG},
            id="root_hidden_toml_config",
        ),
        pytest.param(
            "auth/settings.yaml",
            {FileDomain.CONFIG, FileDomain.SECURITY},
            id="security_config_fallback",
        ),
        pytest.param("README", {FileDomain.DOCS}, id="root_readme"),
        pytest.param(
            "src/api/README",
            {FileDomain.API, FileDomain.DOCS},
            id="extensionless_readme_in_api_dir",
        ),
        pytest.param("LICENSE", {FileDomain.DOCS}, id="root_license"),
        pytest.param("asset.unknown", {FileDomain.SOURCE}, id="generic_fallback"),
        pytest.param("data.txt", {FileDomain.SOURCE}, id="non_doc_txt_source"),
        pytest.param(
            "package-lock.json",
            {FileDomain.CONFIG, FileDomain.DEPS},
            id="root_npm_lockfile",
        ),
        pytest.param(
            "pnpm-lock.yaml",
            {FileDomain.CONFIG, FileDomain.DEPS},
            id="root_pnpm_lockfile",
        ),
        pytest.param(
            "bun.lockb",
            {FileDomain.DEPS},
            id="root_bun_binary_lockfile",
        ),
        pytest.param(
            "src/readme.py",
            {FileDomain.SOURCE},
            id="source_file_named_readme",
        ),
        pytest.param(
            "tools/license.py",
            {FileDomain.SOURCE},
            id="source_file_named_license",
        ),
    ],
)
def test_classify_changed_files_assigns_expected_domains(
    *,
    path: str,
    expected_domains: set[FileDomain],
) -> None:
    """Changed files match the expected review domains for their paths."""
    files = [
        ChangedFile(
            path=path,
            status=ChangedFileStatus.MODIFIED,
            additions=1,
            deletions=0,
        ),
    ]

    classifications = classify_changed_files(files)

    assert_that(classifications).is_length(1)
    assert_that(set(classifications[0].domains)).is_equal_to(expected_domains)
