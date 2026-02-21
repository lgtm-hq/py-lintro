"""Unit tests for path_utils module."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.utils.path_utils import (
    find_lintro_ignore,
    load_lintro_ignore,
    normalize_file_path_for_display,
)

# =============================================================================
# Tests for find_lintro_ignore
# =============================================================================


def test_find_lintro_ignore_in_current_dir(tmp_path: Path) -> None:
    """Find .lintro-ignore in current directory.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    ignore_file = tmp_path / ".lintro-ignore"
    ignore_file.write_text("*.pyc\n")

    with patch("lintro.utils.path_utils.Path") as mock_path:
        mock_path.cwd.return_value = tmp_path
        result = find_lintro_ignore()

    assert_that(result).is_not_none()
    assert_that(str(result)).contains(".lintro-ignore")


def test_find_lintro_ignore_pyproject_stops_search(tmp_path: Path) -> None:
    """Stop search when pyproject.toml found without .lintro-ignore.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.lintro]\n")

    with patch("lintro.utils.path_utils.Path") as mock_path:
        mock_path.cwd.return_value = tmp_path
        result = find_lintro_ignore()

    # Should return None since pyproject exists but no .lintro-ignore
    assert_that(result).is_none()


def test_find_lintro_ignore_with_pyproject(tmp_path: Path) -> None:
    """Find .lintro-ignore when both it and pyproject.toml exist.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    ignore_file = tmp_path / ".lintro-ignore"
    ignore_file.write_text("*.pyc\n")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.lintro]\n")

    with patch("lintro.utils.path_utils.Path") as mock_path:
        mock_path.cwd.return_value = tmp_path
        result = find_lintro_ignore()

    assert_that(result).is_not_none()


def test_find_lintro_ignore_returns_none_when_nothing_found(tmp_path: Path) -> None:
    """Return None when no .lintro-ignore or pyproject.toml found.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    # Create a deep nested directory without any marker files
    deep_dir = tmp_path / "a" / "b" / "c"
    deep_dir.mkdir(parents=True)

    # Mock Path.cwd() to return the deep directory
    # and also mock parent traversal to eventually reach tmp_path's parent
    with patch("lintro.utils.path_utils.Path") as mock_path:
        # Create a path that has no .lintro-ignore or pyproject.toml
        mock_cwd = MagicMock()
        mock_path.cwd.return_value = mock_cwd

        # Mock the traversal to return paths without markers
        mock_cwd.__truediv__ = MagicMock(
            return_value=MagicMock(exists=MagicMock(return_value=False)),
        )
        mock_cwd.parent = mock_cwd  # Simulate reaching root

        result = find_lintro_ignore()

    assert_that(result).is_none()


# =============================================================================
# Tests for load_lintro_ignore
# =============================================================================


def test_load_lintro_ignore_patterns_from_file(tmp_path: Path) -> None:
    """Load ignore patterns from .lintro-ignore file.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    ignore_file = tmp_path / ".lintro-ignore"
    ignore_file.write_text("*.pyc\n__pycache__/\n# comment\n\nnode_modules/\n")

    with patch("lintro.utils.path_utils.find_lintro_ignore", return_value=ignore_file):
        result = load_lintro_ignore()

    assert_that(result).is_equal_to(["*.pyc", "__pycache__/", "node_modules/"])


def test_load_lintro_ignore_returns_empty_when_no_file() -> None:
    """Return empty list when no .lintro-ignore found."""
    with patch("lintro.utils.path_utils.find_lintro_ignore", return_value=None):
        result = load_lintro_ignore()

    assert_that(result).is_empty()


def test_load_lintro_ignore_handles_file_read_error(tmp_path: Path) -> None:
    """Handle file read errors gracefully.

    Args:
        tmp_path: Description of tmp_path (Path).
    """
    ignore_file = tmp_path / ".lintro-ignore"
    ignore_file.write_text("*.pyc\n")

    with (
        patch("lintro.utils.path_utils.find_lintro_ignore", return_value=ignore_file),
        patch(
            "builtins.open", side_effect=PermissionError("Access denied")
        ),
    ):
        result = load_lintro_ignore()

    assert_that(result).is_empty()


def test_load_lintro_ignore_skips_comments_and_empty_lines(tmp_path: Path) -> None:
    """Skip comments and empty lines.

    Args:
        tmp_path: Description of tmp_path (Path).
    """
    ignore_file = tmp_path / ".lintro-ignore"
    ignore_file.write_text("# This is a comment\n\n   \n*.pyc\n  # Another comment\n")

    with patch("lintro.utils.path_utils.find_lintro_ignore", return_value=ignore_file):
        result = load_lintro_ignore()

    assert_that(result).is_equal_to(["*.pyc"])


# =============================================================================
# Tests for normalize_file_path_for_display
# =============================================================================


def test_normalize_file_path_relative_path() -> None:
    """Normalize relative path to start with ./."""
    result = normalize_file_path_for_display("src/main.py")
    assert_that(result).starts_with("./")
    assert_that(result).contains("src")
    assert_that(result).contains("main.py")


@pytest.mark.parametrize(
    ("input_path", "expected"),
    [
        ("", ""),
        ("   ", "   "),
    ],
    ids=["empty_string", "whitespace_string"],
)
def test_normalize_file_path_edge_cases(input_path: str, expected: str) -> None:
    """Handle empty and whitespace strings.

    Args:
        input_path: Input path to normalize.
        expected: Expected normalized result.
    """
    result = normalize_file_path_for_display(input_path)
    assert_that(result).is_equal_to(expected)


def test_normalize_file_path_preserves_parent_path_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve ../ prefix for parent paths."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    (other_dir / "file.py").touch()

    monkeypatch.chdir(project_dir)
    result = normalize_file_path_for_display("../other/file.py")

    assert_that(result).starts_with("../")


def test_normalize_file_path_handles_absolute_path() -> None:
    """Convert absolute path to relative."""
    cwd = os.getcwd()
    abs_path = os.path.join(cwd, "test_file.py")
    result = normalize_file_path_for_display(abs_path)
    assert_that(result).is_equal_to("./test_file.py")


def test_normalize_file_path_handles_os_error() -> None:
    """Return original path on OSError during path resolution.

    The function catches OSError and returns the original path.
    """
    from pathlib import Path

    with patch.object(Path, "resolve", side_effect=OSError("Error")):
        result = normalize_file_path_for_display("some/path.py")

    assert_that(result).is_equal_to("some/path.py")


def test_normalize_file_path_adds_dot_slash_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Add ./ prefix to paths that don't have it."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "file.py").touch()

    monkeypatch.chdir(tmp_path)
    result = normalize_file_path_for_display("src/file.py")

    assert_that(result).is_equal_to("./src/file.py")
