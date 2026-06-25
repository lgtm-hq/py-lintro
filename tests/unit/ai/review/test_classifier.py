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
            {FileDomain.SHELL.value},
            id="shell_script",
        ),
        pytest.param(
            ".github/workflows/ci.yml",
            {FileDomain.CI.value},
            id="workflow",
        ),
        pytest.param(
            "src/main.py",
            {FileDomain.PYTHON.value},
            id="python_source",
        ),
        pytest.param(
            "tests/test_main.py",
            {FileDomain.PYTHON.value, FileDomain.TEST.value},
            id="python_test",
        ),
        pytest.param(
            "docs/guide.md",
            {FileDomain.DOCS.value},
            id="docs",
        ),
        pytest.param(
            "scripts/security/auth.py",
            {
                FileDomain.PYTHON.value,
                FileDomain.SECURITY.value,
            },
            id="security_script",
        ),
        pytest.param(
            "main.py",
            {FileDomain.PYTHON.value},
            id="root_python",
        ),
        pytest.param(
            "README.md",
            {FileDomain.DOCS.value},
            id="root_markdown",
        ),
    ],
)
def test_classify_changed_files_assigns_expected_domains(
    *,
    path: str,
    expected_domains: set[str],
) -> None:
    """Changed files match the expected review domains for their paths."""
    files = [
        ChangedFile(path=path, status="modified", additions=1, deletions=0),
    ]

    classifications = classify_changed_files(files)

    assert_that(classifications).is_length(1)
    assert_that(set(classifications[0].domains)).is_equal_to(expected_domains)
