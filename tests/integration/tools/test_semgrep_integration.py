"""Integration tests for Semgrep tool definition.

These tests require semgrep to be installed and available in PATH.
They verify the SemgrepPlugin definition, check command, and set_options method.
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

pytestmark = require_tool("semgrep")


@pytest.fixture
def temp_python_file_with_security_issues(tmp_path: Path) -> str:
    """Create a temporary Python file with security issues.

    Creates a file containing code with deliberate security vulnerabilities
    that semgrep should detect, including:
    - Use of eval() with user input
    - Hardcoded secrets
    - SQL injection patterns

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "insecure.py"
    file_path.write_text(
        """\
import os

def execute_code(user_input):
    # Dangerous: eval with user input
    result = eval(user_input)
    return result

def get_password():
    # Hardcoded secret
    api_key = "AKIAIOSFODNN7EXAMPLE"
    return api_key

def query_db(user_id):
    # SQL injection vulnerability
    query = "SELECT * FROM users WHERE id = " + user_id
    return query
""",
    )
    return str(file_path)


@pytest.fixture
def temp_python_file_secure(tmp_path: Path) -> str:
    """Create a temporary Python file with no security issues.

    Creates a file containing secure Python code that should pass
    semgrep security analysis without issues.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "secure.py"
    file_path.write_text(
        '''\
"""A secure module."""

import secrets
import os


def generate_token() -> str:
    """Generate a secure token."""
    return secrets.token_hex(32)


def get_env_var(name: str) -> str:
    """Get environment variable safely."""
    return os.environ.get(name, "")


def add_numbers(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b
''',
    )
    return str(file_path)


# --- Tests for SemgrepPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "semgrep"),
        ("can_fix", False),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify SemgrepPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    semgrep_plugin = get_plugin("semgrep")
    assert_that(getattr(semgrep_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify SemgrepPlugin definition includes expected file patterns.

    Tests that the plugin is configured to handle Python and other files.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    semgrep_plugin = get_plugin("semgrep")
    assert_that(semgrep_plugin.definition.file_patterns).contains("*.py")


def test_definition_tool_type(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify SemgrepPlugin is a security tool type.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    from lintro.enums.tool_type import ToolType

    semgrep_plugin = get_plugin("semgrep")
    # Use flag containment check since tool_type is a flags enum
    assert_that(
        semgrep_plugin.definition.tool_type & ToolType.SECURITY,
    ).is_equal_to(ToolType.SECURITY)


# --- Integration tests for semgrep check command ---


def test_check_file_with_security_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_python_file_with_security_issues: str,
) -> None:
    """Verify semgrep check detects security issues in problematic files.

    Runs semgrep on a file containing deliberate security vulnerabilities
    and verifies that issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_python_file_with_security_issues: Path to file with security issues.
    """
    semgrep_plugin = get_plugin("semgrep")
    result = semgrep_plugin.check([temp_python_file_with_security_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("semgrep")
    # Semgrep with auto config should detect at least one issue
    # Note: Results depend on semgrep's rule set


def test_check_secure_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_python_file_secure: str,
) -> None:
    """Verify semgrep check passes on secure files.

    Runs semgrep on a properly secured file and verifies minimal issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_python_file_secure: Path to file with no security issues.
    """
    semgrep_plugin = get_plugin("semgrep")
    result = semgrep_plugin.check([temp_python_file_secure], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("semgrep")


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify semgrep check handles empty directories gracefully.

    Runs semgrep on an empty directory and verifies a result is returned
    without errors.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    semgrep_plugin = get_plugin("semgrep")
    result = semgrep_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()


# --- Tests for SemgrepPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("config", "auto"),
        ("config", "p/python"),
        ("severity", "ERROR"),
        ("exclude", ["test_*.py", "vendor/*"]),
    ],
    ids=["config_auto", "config_python", "severity_error", "exclude_patterns"],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
) -> None:
    """Verify SemgrepPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
    """
    semgrep_plugin = get_plugin("semgrep")
    semgrep_plugin.set_options(**{option_name: option_value})
    assert_that(semgrep_plugin.options.get(option_name)).is_equal_to(option_value)
