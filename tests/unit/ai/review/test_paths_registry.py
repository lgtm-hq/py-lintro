"""Tests for review interaction path generation."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.models.file_classification import FileClassification
from lintro.ai.review.paths_registry import generate_interaction_paths

_REPETITIVE_THRESHOLD = 34


def test_generate_interaction_paths_emits_ci_shell_path() -> None:
    """CI and shell domains produce workflow-to-script trace path."""
    classifications = [
        FileClassification(
            path=".github/workflows/ci.yml",
            domains=[FileDomain.CI],
        ),
        FileClassification(
            path="scripts/ci/run.sh",
            domains=[FileDomain.SHELL, FileDomain.SECURITY],
        ),
    ]
    changed_files = [".github/workflows/ci.yml", "scripts/ci/run.sh"]

    paths = generate_interaction_paths(
        classifications=classifications,
        changed_files=changed_files,
    )

    assert_that(paths.lower()).contains("workflow")
    assert_that(paths.lower()).contains("script")
    assert_that(paths.count("**Path A —")).is_equal_to(1)


def test_generate_interaction_paths_skips_shell_only_when_ci_present() -> None:
    """Shell-only path is omitted when CI+shell combined path already matches."""
    classifications = [
        FileClassification(
            path=".github/workflows/ci.yml",
            domains=[FileDomain.CI],
        ),
        FileClassification(
            path="scripts/ci/run.sh",
            domains=[FileDomain.SHELL],
        ),
    ]

    paths = generate_interaction_paths(
        classifications=classifications,
        changed_files=[".github/workflows/ci.yml", "scripts/ci/run.sh"],
    )

    assert_that(paths).contains("**Path A — CI + shell:**")
    assert_that(paths).does_not_contain("**Path A — Shell exit semantics:**")


def test_generate_interaction_paths_emits_test_vs_production_path() -> None:
    """Test domain triggers test-vs-production default comparison path."""
    classifications = [
        FileClassification(
            path="tests/test_main.py",
            domains=[FileDomain.SOURCE, FileDomain.TEST],
        ),
        FileClassification(
            path="src/main.py",
            domains=[FileDomain.SOURCE],
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
                FileDomain.SOURCE,
                FileDomain.SHELL,
                FileDomain.SECURITY,
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


def test_generate_interaction_paths_emits_bulk_repetitive_path() -> None:
    """Large diffs emit a bulk repetitive sampling path."""
    changed_files = [f"src/module_{index}.py" for index in range(_REPETITIVE_THRESHOLD)]
    classifications = [
        FileClassification(path=path, domains=[FileDomain.SOURCE])
        for path in changed_files
    ]

    paths = generate_interaction_paths(
        classifications=classifications,
        changed_files=changed_files,
    )

    assert_that(paths).contains("Bulk repetitive changes")
    assert_that(paths).contains("34 files")


def test_generate_interaction_paths_emits_server_client_path() -> None:
    """Rust and TypeScript source files trigger server-client interaction path."""
    classifications = [
        FileClassification(
            path="src/auth.rs",
            domains=[FileDomain.SOURCE, FileDomain.SECURITY],
        ),
        FileClassification(
            path="src/api.ts",
            domains=[FileDomain.SOURCE, FileDomain.API],
        ),
    ]

    paths = generate_interaction_paths(
        classifications=classifications,
        changed_files=["src/auth.rs", "src/api.ts"],
    )

    assert_that(paths).contains("Server ↔ client")


def test_generate_interaction_paths_caps_at_seven_paths() -> None:
    """At most seven interaction paths are emitted."""
    changed_files = [
        ".github/workflows/ci.yml",
        "scripts/ci/run.sh",
        "docs/guide.md",
        "tests/test_main.py",
        "src/main.py",
        "src/auth.rs",
        "src/api.ts",
        "openapi.yaml",
        "src/security.py",
    ]
    classifications = [
        FileClassification(
            path=".github/workflows/ci.yml",
            domains=[FileDomain.CI, FileDomain.DOCS],
        ),
        FileClassification(
            path="scripts/ci/run.sh",
            domains=[FileDomain.SHELL, FileDomain.SECURITY],
        ),
        FileClassification(path="docs/guide.md", domains=[FileDomain.DOCS]),
        FileClassification(
            path="tests/test_main.py",
            domains=[FileDomain.SOURCE, FileDomain.TEST],
        ),
        FileClassification(path="src/main.py", domains=[FileDomain.SOURCE]),
        FileClassification(
            path="src/auth.rs",
            domains=[FileDomain.SOURCE, FileDomain.SECURITY],
        ),
        FileClassification(
            path="src/api.ts",
            domains=[FileDomain.SOURCE, FileDomain.API],
        ),
        FileClassification(path="openapi.yaml", domains=[FileDomain.API]),
        FileClassification(
            path="src/security.py",
            domains=[FileDomain.SOURCE, FileDomain.SECURITY],
        ),
    ]

    paths = generate_interaction_paths(
        classifications=classifications,
        changed_files=changed_files,
    )

    assert_that(paths.count("**Path ")).is_less_than_or_equal_to(7)
    assert_that(paths).contains("OpenAPI vs routes")
