#!/usr/bin/env python3
"""Tests for GitHub comment utility json_encode_body.py script.

Tests the functionality of json_encode_body.py utility used by
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
def json_encode_script_path() -> Path:
    """Get path to json_encode_body.py script.

    Returns:
        Path: Absolute path to the script.
    """
    return (
        Path(__file__).parent.parent.parent
        / "scripts"
        / "utils"
        / "json_encode_body.py"
    )


@pytest.fixture
def sample_data_dir() -> Path:
    """Get path to test_samples directory.

    Returns:
        Path: Absolute path to test_samples directory.
    """
    return Path(__file__).parent.parent.parent / "test_samples"


# Tests for json_encode_body.py


def test_json_encode_simple_body(
    json_encode_script_path: Path,
    sample_data_dir: Path,
) -> None:
    """Test JSON encoding of a simple comment body.

    Args:
        json_encode_script_path: Path to the json_encode_body.py script.
        sample_data_dir: Path to the test_samples directory.
    """
    input_file = (
        sample_data_dir / "fixtures" / "pr_comments" / "comment_body_simple.txt"
    )

    # Execute the script
    result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
        ["python3", str(json_encode_script_path), str(input_file)],
        capture_output=True,
        text=True,
        check=True,
    )

    # Parse the output as JSON
    output_data = json.loads(result.stdout.strip())

    # Verify the expected structure
    assert_that(output_data).has_body(
        "This is a simple comment body.\nIt has multiple lines.\n",
    )
    assert_that(len(output_data)).is_equal_to(1)  # Should only have 'body' key


def test_json_encode_special_chars(
    json_encode_script_path: Path,
    sample_data_dir: Path,
) -> None:
    """Test JSON encoding of comment body with special characters.

    Args:
        json_encode_script_path: Path to the json_encode_body.py script.
        sample_data_dir: Path to the test_samples directory.
    """
    input_file = (
        sample_data_dir / "fixtures" / "pr_comments" / "comment_body_with_quotes.txt"
    )

    # Execute the script
    result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
        ["python3", str(json_encode_script_path), str(input_file)],
        capture_output=True,
        text=True,
        check=True,
    )

    # Parse the output as JSON
    output_data = json.loads(result.stdout.strip())

    # Verify the expected structure
    expected_body = (
        "This comment has \"double quotes\" and 'single quotes'.\n"
        "It also has special chars like <>&.\n"
    )
    assert_that(output_data).has_body(expected_body)
    assert_that(len(output_data)).is_equal_to(1)  # Should only have 'body' key


def test_json_encode_from_stdin(json_encode_script_path: Path) -> None:
    """Test JSON encoding when reading from stdin.

    Args:
        json_encode_script_path: Path to the json_encode_body.py script.
    """
    test_body = "This is a test body from stdin.\nWith multiple lines."

    # Execute the script with input from stdin
    result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
        ["python3", str(json_encode_script_path)],
        input=test_body,
        capture_output=True,
        text=True,
        check=True,
    )

    # Parse the output as JSON
    output_data = json.loads(result.stdout.strip())

    # Verify the expected structure
    assert_that(output_data).has_body(test_body)
    assert_that(len(output_data)).is_equal_to(1)  # Should only have 'body' key


def test_json_encode_nonexistent_file(json_encode_script_path: Path) -> None:
    """Test handling when input file does not exist.

    Args:
        json_encode_script_path: Path to the json_encode_body.py script.
    """
    nonexistent_file = "/tmp/nonexistent_file.txt"

    # Execute the script - should exit with code 1
    result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
        ["python3", str(json_encode_script_path), nonexistent_file],
        capture_output=True,
        text=True,
    )

    # Should exit with code 1
    assert_that(result.returncode).is_equal_to(1)

    # Should output nothing to stdout
    assert_that(result.stdout.strip()).is_empty()

    # Should have error message in stderr
    assert_that(result.stderr.strip()).contains("Error reading file")


def test_json_encode_empty_body(json_encode_script_path: Path) -> None:
    """Test JSON encoding of an empty comment body.

    Args:
        json_encode_script_path: Path to the json_encode_body.py script.
    """
    # Create a temporary empty file
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("")
        empty_file = f.name

    try:
        # Execute the script
        result = subprocess.run(  # nosec B603 B607 - fixed argv run against a real binary in a controlled test; binary name resolved from PATH, not attacker-controlled; shell=False, no user shell input
            ["python3", str(json_encode_script_path), empty_file],
            capture_output=True,
            text=True,
            check=True,
        )

        # Parse the output as JSON
        output_data = json.loads(result.stdout.strip())

        # Verify the expected structure
        assert_that(output_data).has_body("")
        assert_that(len(output_data)).is_equal_to(1)  # Should only have 'body' key

    finally:
        os.unlink(empty_file)
