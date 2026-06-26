"""Tests for review file classification."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.review.classifier import classify_changed_files
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.models.changed_file import ChangedFile


@pytest.mark.parametrize(
    ("path", "expected_domains"),
    [
        pytest.param(
            "scripts/ci/run.sh",
            {FileDomain.SHELL},
            id="shell_script",
        ),
        pytest.param(
            ".github/workflows/ci.yml",
            {FileDomain.CI},
            id="workflow",
        ),
        pytest.param(
            ".github/actions/deploy/action.yml",
            {FileDomain.CI},
            id="nested_ci_action",
        ),
        pytest.param(
            "src/main.py",
            {FileDomain.PYTHON},
            id="python_source",
        ),
        pytest.param(
            "tests/test_main.py",
            {FileDomain.PYTHON, FileDomain.TEST},
            id="python_test",
        ),
        pytest.param(
            "docs/guide.md",
            {FileDomain.DOCS},
            id="docs",
        ),
        pytest.param(
            "docs/nested/guide.md",
            {FileDomain.DOCS},
            id="nested_docs",
        ),
        pytest.param(
            "src/api/v2/handlers.py",
            {FileDomain.PYTHON, FileDomain.API},
            id="nested_api_module",
        ),
        pytest.param(
            "scripts/security/auth.py",
            {
                FileDomain.PYTHON,
                FileDomain.SECURITY,
            },
            id="security_script",
        ),
        pytest.param(
            "main.py",
            {FileDomain.PYTHON},
            id="root_python",
        ),
        pytest.param(
            "README.md",
            {FileDomain.DOCS},
            id="root_markdown",
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
        ChangedFile(path=path, status="modified", additions=1, deletions=0),
    ]

    classifications = classify_changed_files(files)

    assert_that(classifications).is_length(1)
    assert_that(set(classifications[0].domains)).is_equal_to(expected_domains)
