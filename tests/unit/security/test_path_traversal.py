"""Tests for path traversal prevention.

These tests verify that the path validation functions properly prevent
path traversal attacks that could access files outside the project root.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.utils.path_utils import (
    normalize_file_path_for_display,
    validate_safe_path,
)

# =============================================================================
# Tests for validate_safe_path function
# =============================================================================


def test_validate_safe_path_relative_path_within_project(tmp_path: Path) -> None:
    """Verify relative path within project is safe.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    test_file = tmp_path / "src" / "file.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.touch()

    result = validate_safe_path(str(test_file), base_dir=tmp_path)
    assert_that(result).is_true()


def test_validate_safe_path_dot_relative_path_is_safe(tmp_path: Path) -> None:
    """Verify ./relative paths are safe.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    test_file = tmp_path / "file.py"
    test_file.touch()

    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = validate_safe_path("./file.py")
        assert_that(result).is_true()
    finally:
        os.chdir(old_cwd)


def test_validate_safe_path_traversal_single_level_blocked(tmp_path: Path) -> None:
    """Verify single-level path traversal is blocked.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    result = validate_safe_path("../outside.txt", base_dir=tmp_path)
    assert_that(result).is_false()


def test_validate_safe_path_traversal_multiple_levels_blocked(tmp_path: Path) -> None:
    """Verify multi-level path traversal is blocked.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    result = validate_safe_path("../../../etc/passwd", base_dir=tmp_path)
    assert_that(result).is_false()


def test_validate_safe_path_traversal_encoded_blocked(tmp_path: Path) -> None:
    """Verify path with traversal in middle is blocked.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    result = validate_safe_path("subdir/../../outside.txt", base_dir=tmp_path)
    assert_that(result).is_false()


def test_validate_safe_path_absolute_path_outside_project_blocked(
    tmp_path: Path,
) -> None:
    """Verify absolute path outside project is blocked.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    result = validate_safe_path("/etc/passwd", base_dir=tmp_path)
    assert_that(result).is_false()


def test_validate_safe_path_absolute_path_inside_project_allowed(
    tmp_path: Path,
) -> None:
    """Verify absolute path inside project is allowed.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    test_file = tmp_path / "inside.txt"
    test_file.touch()

    result = validate_safe_path(str(test_file), base_dir=tmp_path)
    assert_that(result).is_true()


def test_validate_safe_path_symlink_escape_blocked(tmp_path: Path) -> None:
    """Verify symlink that points outside project is blocked.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    link_path = tmp_path / "evil_link"
    try:
        link_path.symlink_to("/etc")
        result = validate_safe_path(str(link_path / "passwd"), base_dir=tmp_path)
        assert_that(result).is_false()
    except OSError:
        pytest.skip("Symlinks not supported on this platform")


def test_validate_safe_path_uses_cwd_when_no_base_dir(tmp_path: Path) -> None:
    """Verify function uses cwd when base_dir not specified.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        test_file = tmp_path / "file.py"
        test_file.touch()

        result = validate_safe_path("./file.py")
        assert_that(result).is_true()

        # Path traversal should be blocked
        result = validate_safe_path("../outside.txt")
        assert_that(result).is_false()
    finally:
        os.chdir(old_cwd)


def test_validate_safe_path_empty_path_behavior(tmp_path: Path) -> None:
    """Verify empty path behavior - resolves relative to cwd, not base_dir.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        # Empty string resolves to cwd, which equals base_dir when chdir'd
        result = validate_safe_path("", base_dir=tmp_path)
        assert_that(result).is_true()
    finally:
        os.chdir(old_cwd)


def test_validate_safe_path_current_dir_is_safe(tmp_path: Path) -> None:
    """Verify current directory path is safe when cwd matches base.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        # "." resolves to cwd, which equals base_dir when chdir'd
        result = validate_safe_path(".", base_dir=tmp_path)
        assert_that(result).is_true()
    finally:
        os.chdir(old_cwd)


def test_validate_safe_path_deeply_nested_path_is_safe(tmp_path: Path) -> None:
    """Verify deeply nested path within project is safe.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    deep_path = tmp_path / "a" / "b" / "c" / "d" / "e" / "file.py"
    deep_path.parent.mkdir(parents=True, exist_ok=True)
    deep_path.touch()

    result = validate_safe_path(str(deep_path), base_dir=tmp_path)
    assert_that(result).is_true()


# =============================================================================
# Tests for normalize_file_path_for_display function
# =============================================================================


def test_normalize_file_path_for_display_relative_path_gets_dot_prefix(
    tmp_path: Path,
) -> None:
    """Verify relative path gets ./ prefix for consistency.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        test_file = tmp_path / "file.py"
        test_file.touch()

        result = normalize_file_path_for_display("file.py")
        assert_that(result).starts_with("./")
    finally:
        os.chdir(old_cwd)


def test_normalize_file_path_for_display_already_prefixed_path_unchanged(
    tmp_path: Path,
) -> None:
    """Verify path already starting with ./ isn't double-prefixed.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        test_file = tmp_path / "file.py"
        test_file.touch()

        result = normalize_file_path_for_display("./file.py")
        assert_that(result).is_equal_to("./file.py")
    finally:
        os.chdir(old_cwd)


def test_normalize_file_path_for_display_empty_path_returned_as_is() -> None:
    """Verify empty path is returned unchanged."""
    result = normalize_file_path_for_display("")
    assert_that(result).is_equal_to("")


def test_normalize_file_path_for_display_whitespace_path_returned_as_is() -> None:
    """Verify whitespace-only path is returned unchanged."""
    result = normalize_file_path_for_display("   ")
    assert_that(result).is_equal_to("   ")


def test_normalize_file_path_for_display_absolute_path_inside_project(
    tmp_path: Path,
) -> None:
    """Verify absolute path inside project is relativized.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        test_file = tmp_path / "src" / "file.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.touch()

        result = normalize_file_path_for_display(str(test_file))
        assert_that(result).starts_with("./")
        assert_that(result).contains("src")
        assert_that(result).contains("file.py")
    finally:
        os.chdir(old_cwd)


def test_normalize_file_path_for_display_path_outside_project_returns_relative_with_dotdot(  # noqa: E501
    tmp_path: Path,
) -> None:
    """Verify path outside project returns path with .. prefix.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    old_cwd = os.getcwd()
    try:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        os.chdir(project_dir)

        outside_file = tmp_path / "outside.txt"
        outside_file.touch()

        result = normalize_file_path_for_display(str(outside_file))
        assert_that(result).contains("..")
    finally:
        os.chdir(old_cwd)


def test_normalize_file_path_for_display_nested_path_normalized(tmp_path: Path) -> None:
    """Verify nested path is properly normalized.

    Args:
        tmp_path: Pytest fixture providing temporary directory.
    """
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        test_file = tmp_path / "src" / "utils" / "helper.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.touch()

        result = normalize_file_path_for_display(str(test_file))
        assert_that(result).is_equal_to("./src/utils/helper.py")
    finally:
        os.chdir(old_cwd)
