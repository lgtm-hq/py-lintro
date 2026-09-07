"""Integration tests for Bandit tool definition.

These tests require bandit to be installed and available in PATH.
They verify the BanditPlugin definition, check command, and set_options method.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from tests.integration._tools import require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = require_tool("bandit")


@pytest.fixture
def temp_python_file_with_security_issues(tmp_path: Path) -> str:
    """Create a temporary Python file with security issues.

    Creates a file containing code with deliberate security vulnerabilities
    that bandit should detect, including:
    - B602: subprocess call with shell=True
    - B105: Hardcoded password
    - B311: Use of standard pseudo-random generators

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "insecure.py"
    file_path.write_text(
        """\
import subprocess
import os

def run_command(cmd):
    # B602: subprocess call with shell=True
    subprocess.call(cmd, shell=True)

def read_file(filename):
    # Potential path traversal
    with open(filename) as f:
        return f.read()

# B105: Hardcoded password
password = "secret123"

# B311: Standard pseudo-random generators not suitable for security
import random
token = random.randint(0, 1000000)
""",
    )
    return str(file_path)


@pytest.fixture
def temp_python_file_secure(tmp_path: Path) -> str:
    """Create a temporary Python file with no security issues.

    Creates a file containing secure Python code that should pass
    bandit security analysis without issues.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "secure.py"
    file_path.write_text(
        """\
\"\"\"A secure module.\"\"\"

import secrets


def generate_token() -> str:
    \"\"\"Generate a secure token.\"\"\"
    return secrets.token_hex(32)


def add_numbers(a: int, b: int) -> int:
    \"\"\"Add two numbers securely.\"\"\"
    return a + b
""",
    )
    return str(file_path)


# --- Tests for BanditPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "bandit"),
        ("can_fix", False),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify BanditPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    bandit_plugin = get_plugin("bandit")
    assert_that(getattr(bandit_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(get_plugin: Callable[[str], BaseToolPlugin]) -> None:
    """Verify BanditPlugin definition includes Python file patterns.

    Tests that the plugin is configured to handle Python files (*.py).

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    bandit_plugin = get_plugin("bandit")
    assert_that(bandit_plugin.definition.file_patterns).contains("*.py")


# --- Integration tests for bandit check command ---


def test_check_file_with_security_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_python_file_with_security_issues: str,
) -> None:
    """Verify bandit check detects security issues in problematic files.

    Runs bandit on a file containing deliberate security vulnerabilities
    and verifies that issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_python_file_with_security_issues: Path to file with security issues.
    """
    bandit_plugin = get_plugin("bandit")
    result = bandit_plugin.check([temp_python_file_with_security_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("bandit")
    assert_that(result.issues_count).is_greater_than(0)


def test_check_secure_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_python_file_secure: str,
) -> None:
    """Verify bandit check passes on secure files.

    Runs bandit on a properly secured file and verifies no issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_python_file_secure: Path to file with no security issues.
    """
    bandit_plugin = get_plugin("bandit")
    result = bandit_plugin.check([temp_python_file_secure], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("bandit")
    assert_that(result.issues_count).is_equal_to(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify bandit check handles empty directories gracefully.

    Runs bandit on an empty directory and verifies a result is returned
    without errors.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    bandit_plugin = get_plugin("bandit")
    result = bandit_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()


# --- Tests for BanditPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("severity", "high"),
        ("confidence", "high"),
        ("skip", ["B101", "B102"]),
    ],
    ids=["severity", "confidence", "skip_tests"],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
) -> None:
    """Verify BanditPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
    """
    bandit_plugin = get_plugin("bandit")
    bandit_plugin.set_options(**{option_name: option_value})
    assert_that(bandit_plugin.options.get(option_name)).is_not_none()
    if option_name == "skip":
        assert_that(bandit_plugin.options.get(option_name)).is_equal_to(option_value)
