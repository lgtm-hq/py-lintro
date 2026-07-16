#!/usr/bin/env python3
"""Tests for GitHub comment utility find_comment_with_marker.py script.

Tests the functionality of find_comment_with_marker.py utility used by
ci-post-pr-comment.sh.

Google-style docstrings are used per project standards.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path

import pytest
from assertpy import assert_that


@pytest.fixture
def find_comment_script_path() -> Path:
    """Get path to find_comment_with_marker.py script.

    Returns:
        Path: Absolute path to the script.
    """
    return (
        Path(__file__).parent.parent.parent
        / "scripts"
        / "utils"
        / "find_comment_with_marker.py"
    )


@pytest.fixture
def sample_data_dir() -> Path:
    """Get path to test_samples directory.

    Returns:
        Path: Absolute path to test_samples directory.
    """
    return Path(__file__).parent.parent.parent / "test_samples"


# Tests for find_comment_with_marker.py


def test_find_comment_with_marker_success(
    find_comment_script_path: Path,
    sample_data_dir: Path,
) -> None:
    """Test successful comment finding with marker.

    Args:
        find_comment_script_path: Path to the find_comment_with_marker.py script.
        sample_data_dir: Path to the test_samples directory.
    """
    input_file = (
        sample_data_dir / "fixtures" / "github" / "github_comments_with_marker.json"
    )

    # Execute the script with JSON via stdin
    with open(input_file) as f:
        result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
            ["python3", str(find_comment_script_path), "<!-- lintro-report -->"],
            input=f.read(),
            capture_output=True,
            text=True,
            check=True,
        )

    # The script outputs just the comment ID
    output_id = result.stdout.strip()

    # Verify the expected ID
    assert_that(output_id).is_equal_to("1234567890")


def test_find_comment_with_marker_paginated(
    find_comment_script_path: Path,
    sample_data_dir: Path,
) -> None:
    """Test comment finding with marker in paginated results.

    Args:
        find_comment_script_path: Path to the find_comment_with_marker.py script.
        sample_data_dir: Path to the test_samples directory.
    """
    input_file = (
        sample_data_dir / "fixtures" / "github" / "github_comments_paginated.json"
    )

    # Execute the script with JSON via stdin
    with open(input_file) as f:
        result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
            ["python3", str(find_comment_script_path), "<!-- lintro-report -->"],
            input=f.read(),
            capture_output=True,
            text=True,
            check=True,
        )

    # Parse the output as JSON
    json.loads(result.stdout.strip())

    # The script outputs just the comment ID
    output_id = result.stdout.strip()

    # Verify the expected ID
    assert_that(output_id).is_equal_to("1234567892")


def test_find_comment_no_marker_found(
    find_comment_script_path: Path,
    sample_data_dir: Path,
) -> None:
    """Test when no comment with the specified marker is found.

    Args:
        find_comment_script_path: Path to the find_comment_with_marker.py script.
        sample_data_dir: Path to the test_samples directory.
    """
    input_file = (
        sample_data_dir / "fixtures" / "github" / "github_comments_no_marker.json"
    )

    # Execute the script with JSON via stdin - should exit with code 1
    with open(input_file) as f:
        result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
            ["python3", str(find_comment_script_path), "<!-- lintro-report -->"],
            input=f.read(),
            capture_output=True,
            text=True,
        )

    # Should exit with code 0 and output empty string when no marker found
    assert_that(result.returncode).is_equal_to(0)

    # Should output empty string to stdout
    assert_that(result.stdout.strip()).is_empty()


def test_find_comment_invalid_json(find_comment_script_path: Path) -> None:
    """Test handling of invalid JSON input.

    Args:
        find_comment_script_path: Path to the find_comment_with_marker.py script.
    """
    # Create a temporary file with invalid JSON
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write('{"invalid": json}')
        invalid_json_file = f.name

    try:
        # Execute the script with invalid JSON via stdin - should exit with code 1
        with open(invalid_json_file) as f:
            result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
                ["python3", str(find_comment_script_path), "<!-- lintro-report -->"],
                input=f.read(),
                capture_output=True,
                text=True,
            )

        # Should exit with code 0 and output empty (invalid JSON handled gracefully)
        assert_that(result.returncode).is_equal_to(0)

        # Should output nothing to stdout
        assert_that(result.stdout.strip()).is_empty()

    finally:
        os.unlink(invalid_json_file)


def test_find_comment_empty_marker(
    find_comment_script_path: Path,
    sample_data_dir: Path,
) -> None:
    """Test behavior with an empty marker.

    Args:
        find_comment_script_path: Path to the find_comment_with_marker.py script.
        sample_data_dir: Path to the test_samples directory.
    """
    input_file = (
        sample_data_dir / "fixtures" / "github" / "github_comments_with_marker.json"
    )

    # Execute the script with empty marker via stdin
    with open(input_file) as f:
        result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
            ["python3", str(find_comment_script_path), ""],
            input=f.read(),
            capture_output=True,
            text=True,
        )

    # Should exit with code 0 and output empty (empty marker handled gracefully)
    assert_that(result.returncode).is_equal_to(0)

    # Should output nothing to stdout
    assert_that(result.stdout.strip()).is_empty()
