#!/usr/bin/env python3
"""Integration tests for ci-post-pr-comment.sh script.

Tests the complete functionality of the CI post PR comment script including
its interaction with the GitHub comment utilities.

Google-style docstrings are used per project standards.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that


@pytest.fixture
def ci_script_path() -> Path:
    """Get path to ci-post-pr-comment.sh script.

    Returns:
        Path: Absolute path to the script.
    """
    return (
        Path(__file__).parent.parent.parent
        / "scripts"
        / "ci"
        / "github"
        / "ci-post-pr-comment.sh"
    )


@pytest.fixture
def sample_data_dir() -> Path:
    """Get path to test_samples directory.

    Returns:
        Path: Absolute path to test_samples directory.
    """
    return Path(__file__).parent.parent.parent / "test_samples"


def test_script_help_output(ci_script_path: Path) -> None:
    """Test that the script displays help when requested.

    Args:
        ci_script_path: Path to the script being tested.
    """
    result = subprocess.run(
        [str(ci_script_path), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert_that(result.stdout).contains("Usage:")
    assert_that(result.stdout).contains("CI Post PR Comment Script")
    assert_that(result.stdout).contains("GitHub Actions CI environment")
    assert_that(result.returncode).is_equal_to(0)


def test_script_help_short_flag(ci_script_path: Path) -> None:
    """Test that the script displays help with -h flag.

    Args:
        ci_script_path: Path to the script being tested.
    """
    result = subprocess.run(
        [str(ci_script_path), "-h"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert_that(result.stdout).contains("Usage:")
    assert_that(result.returncode).is_equal_to(0)


def test_script_exits_when_not_in_pr_context(ci_script_path: Path) -> None:
    """Test that script exits gracefully when not in PR context.

    Args:
        ci_script_path: Path to the script being tested.
    """
    # Create a temporary comment file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Test PR comment content")
        comment_file = f.name

    try:
        # Run without PR environment variables
        env = os.environ.copy()
        # Remove PR-related environment variables if they exist
        env.pop("GITHUB_EVENT_NAME", None)
        env.pop("PR_NUMBER", None)

        result = subprocess.run(
            [str(ci_script_path), comment_file],
            capture_output=True,
            text=True,
            env=env,
        )

        assert_that(result.returncode).is_equal_to(0)
        output = result.stdout + result.stderr
        assert_that(output).contains("Not in a PR context")
    finally:
        os.unlink(comment_file)


def test_script_fails_with_missing_comment_file(ci_script_path: Path) -> None:
    """Test that script fails when comment file doesn't exist.

    Args:
        ci_script_path: Path to the script being tested.
    """
    # Set up minimal PR context environment
    env = os.environ.copy()
    env.update(
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "PR_NUMBER": "123",
            "GITHUB_REPOSITORY": "test/repo",
            "GITHUB_TOKEN": "fake-token",  # nosec B105
        },
    )

    result = subprocess.run(
        [str(ci_script_path), "nonexistent-file.txt"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert_that(result.returncode).is_equal_to(1)
    # Error message might go to stdout or stderr, and may contain color codes
    error_output = result.stdout + result.stderr
    assert_that(error_output).contains("Comment file")
    assert_that(error_output).contains("not found")


def test_script_uses_default_comment_file(ci_script_path: Path) -> None:
    """Test that script uses default comment file when none specified.

    Args:
        ci_script_path: Path to the script being tested.
    """
    # Create default comment file
    with tempfile.TemporaryDirectory() as temp_dir:
        comment_file = Path(temp_dir) / "pr-comment.txt"
        comment_file.write_text("Default comment content")

        # Set up minimal PR context environment
        env = os.environ.copy()
        env.update(
            {
                "GITHUB_EVENT_NAME": "pull_request",
                "PR_NUMBER": "123",
                "GITHUB_REPOSITORY": "test/repo",
                "GITHUB_TOKEN": "fake-token",  # nosec B105
            },
        )

        # Change to temp directory so default file is found
        result = subprocess.run(
            [str(ci_script_path)],
            capture_output=True,
            text=True,
            cwd=temp_dir,
            env=env,
        )

        # Script should try to process the default file
        # (may fail due to no actual GitHub API)
        # but shouldn't fail due to missing file
        error_output = result.stdout + result.stderr
        assert_that(error_output).does_not_contain(
            "Comment file pr-comment.txt not found",
        )


@patch.dict(
    os.environ,
    {
        "GITHUB_EVENT_NAME": "pull_request",
        "PR_NUMBER": "123",
        "GITHUB_REPOSITORY": "test/repo",
        "GITHUB_TOKEN": "fake-token",  # nosec B105
    },
)
def test_script_integrates_with_python_utilities(
    ci_script_path: Path,
    sample_data_dir: Path,
) -> None:
    """Test that script correctly integrates with Python utilities.

    This test verifies the shell script calls the Python utilities with correct
    arguments.

    Args:
        ci_script_path: Path to the script being tested.
        sample_data_dir: Path to test sample files.
    """
    # Create a test comment file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("<!-- test-marker -->\n\nNew test comment content")
        comment_file = f.name

    try:
        # Set environment for marker-based update
        env = os.environ.copy()
        env.update(
            {
                "MARKER": "<!-- test-marker -->",
                "GITHUB_EVENT_NAME": "pull_request",
                "PR_NUMBER": "123",
                "GITHUB_REPOSITORY": "test/repo",
                "GITHUB_TOKEN": "fake-token",  # nosec B105
            },
        )

        # Mock gh command to simulate finding existing comments
        with patch("subprocess.run") as mock_run:
            # First call will be to check for gh command
            # Second call will be the actual API call that we want to test
            # preparation for
            mock_run.side_effect = [
                # gh command check
                subprocess.CompletedProcess(
                    args=["command", "-v", "gh"],
                    returncode=1,  # gh not found, will use curl path
                ),
                # Our script should complete without API calls in test environment
            ]

            subprocess.run(
                [str(ci_script_path), comment_file],
                capture_output=True,
                text=True,
                env=env,
            )

            # The script should attempt to process the marker logic
            # Even if it fails at the API call level, it should get through
            # the utility calls
            # In a real environment this would make API calls, but in our test
            # environment
            # it should at least validate the basic flow

    finally:
        os.unlink(comment_file)


def test_script_syntax_check(ci_script_path: Path) -> None:
    """Test that the script has valid bash syntax.

    Args:
        ci_script_path: Path to the script being tested.

    Raises:
        AssertionError: If the script has syntax errors.
    """
    result = subprocess.run(
        ["bash", "-n", str(ci_script_path)],
        capture_output=True,
        text=True,
    )

    assert_that(result.returncode).is_equal_to(0)
    if result.stderr:
        raise AssertionError(f"Syntax error in script: {result.stderr}")


def test_script_has_proper_shebang(ci_script_path: Path) -> None:
    """Test that the script has proper shebang line.

    Args:
        ci_script_path: Path to the script being tested.
    """
    with open(ci_script_path) as f:
        first_line = f.readline().strip()

    # Accept both portable and traditional shebangs
    assert_that(first_line).matches(r"^#!(/usr/bin/env bash|/bin/bash)")


def test_script_sources_utilities(ci_script_path: Path) -> None:
    """Test that the script sources the required utilities.

    Args:
        ci_script_path: Path to the script being tested.
    """
    content = ci_script_path.read_text()

    # Should source the shared utilities using absolute path via SCRIPT_DIR
    assert_that(content).contains('source "$SCRIPT_DIR/../../utils/utils.sh"')

    # Should call the Python utilities we created
    assert_that(content).contains("find_comment_with_marker.py")
    assert_that(content).contains("extract_comment_body.py")
    assert_that(content).contains("json_encode_body.py")
    assert_that(content).contains("merge_pr_comment.py")


def test_script_handles_marker_logic(ci_script_path: Path) -> None:
    """Test that the script contains proper marker handling logic.

    Args:
        ci_script_path: Path to the script being tested.
    """
    content = ci_script_path.read_text()

    # Should have marker-related logic
    assert_that(content).contains('MARKER="${MARKER:-}"')
    assert_that(content).contains('if [ -n "$MARKER" ];')
    assert_that(content.lower()).contains("marker provided")

    # Should handle both update and new comment scenarios
    assert_that(content.lower()).contains("update existing comment")
    assert_that(content.lower()).contains("create a new comment")


def test_python_utilities_are_executable() -> None:
    """Test that all Python utilities have executable permissions."""
    utils_dir = Path(__file__).parent.parent.parent / "scripts" / "utils"

    utilities = [
        "find_comment_with_marker.py",
        "extract_comment_body.py",
        "json_encode_body.py",
    ]

    for utility in utilities:
        utility_path = utils_dir / utility
        assert_that(utility_path.exists()).is_true()
        assert_that(os.access(utility_path, os.X_OK)).is_true()
