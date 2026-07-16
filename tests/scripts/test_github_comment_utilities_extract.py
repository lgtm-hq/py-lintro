#!/usr/bin/env python3
"""Tests for GitHub comment utility extract_comment_body.py script.

Tests the functionality of extract_comment_body.py utility used by
ci-post-pr-comment.sh.

Google-style docstrings are used per project standards.
"""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path

import pytest
from assertpy import assert_that


@pytest.fixture
def extract_comment_script_path() -> Path:
    """Get path to extract_comment_body.py script.

    Returns:
        Path: Absolute path to the script.
    """
    return (
        Path(__file__).parent.parent.parent
        / "scripts"
        / "utils"
        / "extract_comment_body.py"
    )


@pytest.fixture
def sample_data_dir() -> Path:
    """Get path to test_samples directory.

    Returns:
        Path: Absolute path to test_samples directory.
    """
    return Path(__file__).parent.parent.parent / "test_samples"


# Tests for extract_comment_body.py


def test_extract_comment_body_success(
    extract_comment_script_path: Path,
    sample_data_dir: Path,
) -> None:
    """Test successful comment body extraction.

    Args:
        extract_comment_script_path: Path to the extract_comment_body.py script.
        sample_data_dir: Path to the test_samples directory.
    """
    input_file = (
        sample_data_dir / "fixtures" / "github" / "github_comments_with_marker.json"
    )

    # Execute the script with JSON via stdin
    with open(input_file) as f:
        result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
            ["python3", str(extract_comment_script_path), "1234567890"],
            input=f.read(),
            capture_output=True,
            text=True,
            check=True,
        )

    # Should output the comment body
    expected_body = (
        "## Lintro Report\n\n### Summary\n- Total issues: 42\n<!-- lintro-report -->"
    )
    assert_that(result.stdout.strip()).is_equal_to(expected_body)


def test_extract_comment_body_paginated(
    extract_comment_script_path: Path,
    sample_data_dir: Path,
) -> None:
    """Test comment body extraction from paginated results.

    Args:
        extract_comment_script_path: Path to the extract_comment_body.py script.
        sample_data_dir: Path to the test_samples directory.
    """
    input_file = (
        sample_data_dir / "fixtures" / "github" / "github_comments_paginated.json"
    )

    # Execute the script with JSON via stdin
    with open(input_file) as f:
        result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
            ["python3", str(extract_comment_script_path), "1234567892"],
            input=f.read(),
            capture_output=True,
            text=True,
            check=True,
        )

    # Should output the comment body
    expected_body = (
        "## Lintro Report (Page 2)\n\n### Summary\n- Total issues: 15\n"
        "<!-- lintro-report -->"
    )
    assert_that(result.stdout.strip()).is_equal_to(expected_body)


def test_extract_comment_body_not_found(
    extract_comment_script_path: Path,
    sample_data_dir: Path,
) -> None:
    """Test when the specified comment ID is not found.

    Args:
        extract_comment_script_path: Path to the extract_comment_body.py script.
        sample_data_dir: Path to the test_samples directory.
    """
    input_file = (
        sample_data_dir / "fixtures" / "github" / "github_comments_with_marker.json"
    )

    # Execute the script with non-existent comment ID via stdin
    with open(input_file) as f:
        result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
            ["python3", str(extract_comment_script_path), "9999999999"],
            input=f.read(),
            capture_output=True,
            text=True,
        )

    # Should exit with code 0 but output empty body
    assert_that(result.returncode).is_equal_to(0)

    # Should output nothing to stdout
    assert_that(result.stdout.strip()).is_empty()


def test_extract_comment_body_invalid_json(extract_comment_script_path: Path) -> None:
    """Test handling of invalid JSON input for extract_comment_body.py.

    Args:
        extract_comment_script_path: Path to the extract_comment_body.py script.
    """
    # Create a temporary file with invalid JSON
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write('{"invalid": json}')
        invalid_json_file = f.name

    try:
        # Execute the script with invalid JSON via stdin
        with open(invalid_json_file) as f:
            result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
                ["python3", str(extract_comment_script_path), "1234567890"],
                input=f.read(),
                capture_output=True,
                text=True,
            )

        # Should exit with code 0 but output empty body
        # (invalid JSON handled gracefully)
        assert_that(result.returncode).is_equal_to(0)

        # Should output nothing to stdout
        assert_that(result.stdout.strip()).is_empty()

    finally:
        os.unlink(invalid_json_file)
