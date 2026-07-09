"""Tests for review glob matching helpers."""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai.review.glob_utils import path_matches_glob


def test_path_matches_glob_expands_brace_groups() -> None:
    """Brace groups in patterns match any listed alternative."""
    assert_that(
        path_matches_glob(path="pkg/uv.lock", pattern="**/{uv.lock,poetry.lock}"),
    ).is_true()
    assert_that(
        path_matches_glob(path="pkg/poetry.lock", pattern="**/{uv.lock,poetry.lock}"),
    ).is_true()
    assert_that(
        path_matches_glob(path="pkg/Cargo.lock", pattern="**/{uv.lock,poetry.lock}"),
    ).is_false()


def test_path_matches_glob_segment_patterns_without_fnmatch() -> None:
    """``**/segment/**`` matches literal path segments, not substring false positives."""
    assert_that(
        path_matches_glob(path="src/api/v2/handlers.py", pattern="**/api/**"),
    ).is_true()
    assert_that(
        path_matches_glob(path="my-api-server/foo.py", pattern="**/api/**"),
    ).is_false()


def test_path_matches_glob_root_directory_patterns_do_not_match_nested_paths() -> None:
    """Root-anchored directory globs ignore nested directories with the same name."""
    assert_that(
        path_matches_glob(path="nested/scripts/run.sh", pattern="scripts/**"),
    ).is_false()
    assert_that(
        path_matches_glob(path="scripts/run.sh", pattern="scripts/**"),
    ).is_true()
    assert_that(
        path_matches_glob(path="foo/docs/guide.md", pattern="docs/**"),
    ).is_false()


def test_path_matches_glob_root_file_patterns_do_not_match_nested_paths() -> None:
    """Root-only filenames stay at the repository root."""
    assert_that(
        path_matches_glob(path="pyproject.toml", pattern="pyproject.toml"),
    ).is_true()
    assert_that(
        path_matches_glob(path="packages/pyproject.toml", pattern="pyproject.toml"),
    ).is_false()
    assert_that(
        path_matches_glob(path="packages/pyproject.toml", pattern="**/pyproject.toml"),
    ).is_true()


def test_path_matches_glob_root_slash_patterns_do_not_match_nested_paths() -> None:
    """Slash-containing root globs are evaluated from the repository root."""
    assert_that(
        path_matches_glob(path="scripts/run.sh", pattern="scripts/*.sh"),
    ).is_true()
    assert_that(
        path_matches_glob(path="nested/scripts/run.sh", pattern="scripts/*.sh"),
    ).is_false()
    assert_that(
        path_matches_glob(path="scripts/nested/run.sh", pattern="scripts/*.sh"),
    ).is_false()
    assert_that(
        path_matches_glob(
            path=".github/workflows/ci.yml",
            pattern=".github/workflows/*.yml",
        ),
    ).is_true()
    assert_that(
        path_matches_glob(
            path="nested/.github/workflows/ci.yml",
            pattern=".github/workflows/*.yml",
        ),
    ).is_false()


def test_path_matches_glob_cross_segment_patterns_match_nested_paths() -> None:
    """``**/*.ext`` and ``**/filename`` patterns match nested repository paths."""
    assert_that(
        path_matches_glob(path="bin/ci/run.sh", pattern="**/*.sh"),
    ).is_true()
    assert_that(
        path_matches_glob(
            path="packages/pkg1/pyproject.toml",
            pattern="**/pyproject.toml",
        ),
    ).is_true()
    assert_that(
        path_matches_glob(
            path="packages/pkg-a/.pre-commit-config.yaml",
            pattern="**/.pre-commit-config.yaml",
        ),
    ).is_true()


def test_path_matches_glob_root_recursive_patterns_are_segment_aware() -> None:
    """Root recursive globs match from root without letting * cross segments."""
    assert_that(
        path_matches_glob(path="src/foo.py", pattern="src/**/*.py"),
    ).is_true()
    assert_that(
        path_matches_glob(path="src/pkg/nested/foo.py", pattern="src/**/*.py"),
    ).is_true()
    assert_that(
        path_matches_glob(path="nested/src/foo.py", pattern="src/**/*.py"),
    ).is_false()
    assert_that(
        path_matches_glob(path="src/api/v1/handler.py", pattern="**/api/*.py"),
    ).is_false()


def test_path_matches_glob_internal_double_star_directory_patterns() -> None:
    """Root patterns with internal ``**`` segments match nested directories."""
    assert_that(
        path_matches_glob(
            path="src/pkg/fixtures/data.json",
            pattern="src/**/fixtures/**",
        ),
    ).is_true()
    assert_that(
        path_matches_glob(
            path="other/pkg/fixtures/data.json",
            pattern="src/**/fixtures/**",
        ),
    ).is_false()


def test_path_matches_glob_multi_segment_trailing_double_star() -> None:
    """Multi-segment ``**/a/b/**`` patterns reach the generic ``**`` matcher."""
    assert_that(
        path_matches_glob(path="pkg/foo/bar/baz.json", pattern="**/foo/bar/**"),
    ).is_true()
    assert_that(
        path_matches_glob(path="pkg/foo/other/baz.json", pattern="**/foo/bar/**"),
    ).is_false()


def test_path_matches_glob_root_level_cross_segment_patterns() -> None:
    """``**/filename`` patterns still match repository-root files."""
    assert_that(
        path_matches_glob(path="views.py", pattern="**/views.py"),
    ).is_true()
