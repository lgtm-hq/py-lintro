"""Unit tests for path_filtering module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.utils.path_filtering import (
    _is_venv_directory,
    should_exclude_path,
    walk_files_with_excludes,
)

# =============================================================================
# Tests for should_exclude_path
# =============================================================================


def test_should_exclude_path_empty_patterns() -> None:
    """Return False for empty patterns list."""
    result = should_exclude_path("src/main.py", [])
    assert_that(result).is_false()


def test_should_exclude_path_simple_glob_match() -> None:
    """Match simple glob pattern."""
    result = should_exclude_path("test.pyc", ["*.pyc"])
    assert_that(result).is_true()


def test_should_exclude_path_no_match() -> None:
    """Return False when no pattern matches."""
    result = should_exclude_path("src/main.py", ["*.pyc", "*.log"])
    assert_that(result).is_false()


def test_should_exclude_path_directory_pattern_with_slash() -> None:
    """Match directory pattern ending with /*."""
    result = should_exclude_path("test_samples/file.py", ["test_samples/*"])
    assert_that(result).is_true()


def test_should_exclude_path_directory_pattern_nested() -> None:
    """Match nested path under directory pattern."""
    result = should_exclude_path(
        "project/test_samples/subdir/file.py",
        ["test_samples/*"],
    )
    assert_that(result).is_true()


def test_should_exclude_path_simple_directory_pattern() -> None:
    """Match simple directory name without wildcards."""
    result = should_exclude_path("project/build/output.js", ["build"])
    assert_that(result).is_true()


def test_should_exclude_path_simple_directory_not_in_path() -> None:
    """Don't match when directory not in path."""
    result = should_exclude_path("src/main.py", ["build"])
    assert_that(result).is_false()


def test_should_exclude_path_empty_pattern_ignored() -> None:
    """Ignore empty patterns in list."""
    result = should_exclude_path("src/main.py", ["", "  ", "*.pyc"])
    assert_that(result).is_false()


def test_should_exclude_path_path_part_match() -> None:
    """Match pattern against path parts."""
    result = should_exclude_path("src/__pycache__/module.pyc", ["__pycache__"])
    assert_that(result).is_true()


def test_should_exclude_path_normalization_error() -> None:
    """Handle path normalization errors gracefully."""
    with patch("os.path.abspath", side_effect=ValueError("Invalid path")):
        result = should_exclude_path("some/path", ["*.py"])
        # Should still work with original path and not match since path
        # doesn't end in .py
        assert_that(result).is_false()


# =============================================================================
# Tests for walk_files_with_excludes
# =============================================================================


@pytest.fixture
def src_dir_with_files(tmp_path: Path) -> Path:
    """Create a source directory with Python and text files.

    Args:
        tmp_path: Temporary directory path.

    Returns:
        Path to the created source directory.
    """
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('main')")
    (src_dir / "utils.py").write_text("print('utils')")
    (src_dir / "readme.txt").write_text("readme")
    return src_dir


@pytest.fixture
def project_with_venv(tmp_path: Path) -> Path:
    """Create a project directory with main.py and .venv directory.

    Args:
        tmp_path: Temporary directory path.

    Returns:
        Path to the created project directory.
    """
    src_dir = tmp_path / "project"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('main')")
    venv_dir = src_dir / ".venv"
    venv_dir.mkdir()
    (venv_dir / "lib.py").write_text("lib")
    return src_dir


def test_walk_files_single_file_match(tmp_path: Path) -> None:
    """Include single file matching pattern.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "main.py"
    test_file.write_text("print('hello')")

    result = walk_files_with_excludes(
        paths=[str(test_file)],
        file_patterns=["*.py"],
        exclude_patterns=[],
    )
    assert_that(result).is_length(1)
    assert_that(result[0]).ends_with("main.py")


def test_walk_files_single_file_no_match(tmp_path: Path) -> None:
    """Exclude single file not matching pattern.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "main.txt"
    test_file.write_text("hello")

    result = walk_files_with_excludes(
        paths=[str(test_file)],
        file_patterns=["*.py"],
        exclude_patterns=[],
    )
    assert_that(result).is_empty()


def test_walk_files_directory_walk(src_dir_with_files: Path) -> None:
    """Walk directory and find matching files.

    Args:
        src_dir_with_files: Directory containing test files.
    """
    result = walk_files_with_excludes(
        paths=[str(src_dir_with_files)],
        file_patterns=["*.py"],
        exclude_patterns=[],
    )
    assert_that(result).is_length(2)


def test_walk_files_excludes_patterns(tmp_path: Path) -> None:
    """Exclude files matching exclude patterns.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('main')")
    cache_dir = src_dir / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "main.pyc").write_text("bytecode")

    result = walk_files_with_excludes(
        paths=[str(src_dir)],
        file_patterns=["*.py", "*.pyc"],
        exclude_patterns=["__pycache__"],
    )
    assert_that(result).is_length(1)
    assert_that(result[0]).ends_with("main.py")


def test_walk_files_excludes_venv_by_default(project_with_venv: Path) -> None:
    """Exclude virtual environment directories by default.

    Args:
        project_with_venv: Project directory with virtual environment.
    """
    result = walk_files_with_excludes(
        paths=[str(project_with_venv)],
        file_patterns=["*.py"],
        exclude_patterns=[],
        include_venv=False,
    )
    assert_that(result).is_length(1)


def test_walk_files_includes_venv_when_requested(project_with_venv: Path) -> None:
    """Include virtual environment when include_venv=True.

    Args:
        project_with_venv: Project directory with virtual environment.
    """
    result = walk_files_with_excludes(
        paths=[str(project_with_venv)],
        file_patterns=["*.py"],
        exclude_patterns=[],
        include_venv=True,
    )
    assert_that(result).is_length(2)


def test_walk_files_returns_sorted_results(tmp_path: Path) -> None:
    """Return sorted file paths.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "z_file.py").write_text("")
    (src_dir / "a_file.py").write_text("")
    (src_dir / "m_file.py").write_text("")

    result = walk_files_with_excludes(
        paths=[str(src_dir)],
        file_patterns=["*.py"],
        exclude_patterns=[],
    )
    assert_that(result).is_equal_to(sorted(result))


def test_walk_files_single_file_excluded(tmp_path: Path) -> None:
    """Exclude single file matching exclude pattern.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test.pyc"
    test_file.write_text("bytecode")

    result = walk_files_with_excludes(
        paths=[str(test_file)],
        file_patterns=["*.pyc"],
        exclude_patterns=["*.pyc"],
    )
    assert_that(result).is_empty()


# =============================================================================
# Tests for _is_venv_directory
# =============================================================================


@pytest.mark.parametrize(
    ("dirname", "expected"),
    [
        (".venv", True),
        ("venv", True),
        ("env", True),
        ("src", False),
        ("environment", False),
    ],
    ids=[
        "dot_venv",
        "venv_without_dot",
        "env_directory",
        "regular_directory",
        "similar_name",
    ],
)
def test_is_venv_directory(dirname: str, expected: bool) -> None:
    """Test virtual environment directory detection.

    Args:
        dirname: Description of dirname (str).
        expected: Description of expected (bool).
    """
    result = _is_venv_directory(dirname)
    assert_that(result).is_equal_to(expected)
