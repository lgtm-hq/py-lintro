"""Fixtures for tools/core tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def temp_python_file(tmp_path: Path) -> Path:
    """Create a temporary Python file with long lines for testing.

    Args:
        tmp_path: Pytest's built-in temporary path fixture.

    Returns:
        Path: Path to the temporary Python file.
    """
    file_path = tmp_path / "test_file.py"
    # Create a file with lines of varying lengths
    # NOTE: Long lines below are intentional test data for E501 detection
    content = '''"""Test module."""

x = 1
y = 2

# This is a long comment line that definitely exceeds the 88 character limit and should trigger E501 detection
long_string = "This is a very long string literal that definitely exceeds the 88 character limit for testing purposes"
'''
    file_path.write_text(content)
    return file_path
