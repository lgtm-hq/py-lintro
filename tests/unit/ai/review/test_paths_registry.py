"""Tests for review interaction path generation."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.models.file_classification import FileClassification
from lintro.ai.review.paths_registry import generate_interaction_paths


def test_generate_interaction_paths_emits_ci_shell_path() -> None:
    """CI and shell domains produce workflow-to-script trace path."""
    classifications = [
        FileClassification(
            path=".github/workflows/ci.yml",
            domains=[FileDomain.CI.value],
        ),
        FileClassification(
            path="scripts/ci/run.sh",
            domains=[FileDomain.SHELL.value, FileDomain.SECURITY.value],
        ),
    ]
    changed_files = [".github/workflows/ci.yml", "scripts/ci/run.sh"]

    paths = generate_interaction_paths(
        classifications=classifications,
        changed_files=changed_files,
    )

    assert_that(paths.lower()).contains("workflow")
    assert_that(paths.lower()).contains("script")


def test_generate_interaction_paths_emits_test_vs_production_path() -> None:
    """Test domain triggers test-vs-production default comparison path."""
    classifications = [
        FileClassification(
            path="tests/test_main.py",
            domains=[FileDomain.PYTHON.value, FileDomain.TEST.value],
        ),
        FileClassification(
            path="src/main.py",
            domains=[FileDomain.PYTHON.value],
        ),
    ]

    paths = generate_interaction_paths(
        classifications=classifications,
        changed_files=["tests/test_main.py", "src/main.py"],
    )

    assert_that(paths.lower()).contains("test")
    assert_that(paths.lower()).contains("production")


def test_generate_interaction_paths_includes_security_path() -> None:
    """Security domain triggers security exit semantics path."""
    classifications = [
        FileClassification(
            path="scripts/security/auth.py",
            domains=[
                FileDomain.PYTHON.value,
                FileDomain.SHELL.value,
                FileDomain.SECURITY.value,
            ],
        ),
    ]

    paths = generate_interaction_paths(
        classifications=classifications,
        changed_files=["scripts/security/auth.py"],
    )

    assert_that(paths.lower()).contains("security")


def test_generate_interaction_paths_handles_empty_changed_files() -> None:
    """Empty changed file list returns a placeholder message."""
    paths = generate_interaction_paths(
        classifications=[],
        changed_files=[],
    )

    assert_that(paths).contains("No interaction paths")
