"""Integration tests for Black tool definition.

These tests require black to be installed and available in PATH.
They verify the BlackPlugin definition, check command, fix command, and
set_options method.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if black is not installed
pytestmark = pytest.mark.skipif(
    shutil.which("black") is None,
    reason="black not installed",
)


@pytest.fixture
def temp_python_file_unformatted(tmp_path: Path) -> str:
    """Create a temporary Python file with formatting issues.

    Creates a file containing code with formatting issues that Black
    should fix, including:
    - Missing spaces around operators
    - Missing spaces after commas
    - Compact dictionary and list formatting

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "unformatted.py"
    file_path.write_text(
        """\
def foo(x,y,z):
    return x+y+z

class MyClass:
    def __init__(self,name,value):
        self.name=name
        self.value=value

x = {"a":1,"b":2,"c":3}
y = [1,2,3,4,5]
""",
    )
    return str(file_path)


@pytest.fixture
def temp_python_file_formatted(tmp_path: Path) -> str:
    """Create a temporary Python file that is already formatted.

    Creates a file containing properly formatted Python code that
    Black should leave unchanged.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "formatted.py"
    file_path.write_text(
        '''\
"""A properly formatted module."""


def foo(x, y, z):
    """Add three numbers."""
    return x + y + z


class MyClass:
    """A simple class."""

    def __init__(self, name, value):
        """Initialize the class."""
        self.name = name
        self.value = value
''',
    )
    return str(file_path)


# --- Tests for BlackPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "black"),
        ("can_fix", True),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify BlackPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    black_plugin = get_plugin("black")
    assert_that(getattr(black_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(get_plugin: Callable[[str], BaseToolPlugin]) -> None:
    """Verify BlackPlugin definition includes Python file patterns.

    Tests that the plugin is configured to handle Python files (*.py).

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    black_plugin = get_plugin("black")
    assert_that(black_plugin.definition.file_patterns).contains("*.py")


# --- Integration tests for black check command ---


@pytest.mark.parametrize(
    ("file_fixture", "expect_issues"),
    [
        ("temp_python_file_unformatted", True),
        ("temp_python_file_formatted", False),
    ],
    ids=["unformatted_file", "formatted_file"],
)
def test_check_file_formatting_state(
    get_plugin: Callable[[str], BaseToolPlugin],
    file_fixture: str,
    expect_issues: bool,
    request: pytest.FixtureRequest,
) -> None:
    """Verify Black check correctly detects formatting state.

    Runs Black in check mode on files with different formatting states
    and verifies the expected issue count.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        file_fixture: Name of the fixture providing the file path.
        expect_issues: Whether issues are expected (True for unformatted).
        request: Pytest request fixture for dynamic fixture access.
    """
    file_path = request.getfixturevalue(file_fixture)
    black_plugin = get_plugin("black")
    result = black_plugin.check([file_path], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("black")
    if expect_issues:
        assert_that(result.issues_count).is_greater_than(0)
    else:
        assert_that(result.issues_count).is_equal_to(0)


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify Black check handles empty directories gracefully.

    Runs Black on an empty directory and verifies a result is returned
    without errors.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    black_plugin = get_plugin("black")
    result = black_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()


# --- Integration tests for black fix command ---


def test_fix_formats_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_python_file_unformatted: str,
) -> None:
    """Verify Black fix reformats unformatted files.

    Runs Black fix on a file with formatting issues and verifies
    the file content changes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_python_file_unformatted: Path to file with formatting issues.
    """
    black_plugin = get_plugin("black")
    original = Path(temp_python_file_unformatted).read_text()

    result = black_plugin.fix([temp_python_file_unformatted], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("black")
    assert_that(result.success).is_true()

    new_content = Path(temp_python_file_unformatted).read_text()
    assert_that(new_content).is_not_equal_to(original)


def test_fix_preserves_formatted_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_python_file_formatted: str,
) -> None:
    """Verify Black fix does not change already formatted files.

    Runs Black fix on a properly formatted file and verifies
    the file content remains unchanged.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_python_file_formatted: Path to properly formatted file.
    """
    black_plugin = get_plugin("black")
    original = Path(temp_python_file_formatted).read_text()

    result = black_plugin.fix([temp_python_file_formatted], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()

    new_content = Path(temp_python_file_formatted).read_text()
    assert_that(new_content).is_equal_to(original)


# --- Tests for BlackPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("line_length", 100, 100),
        ("target_version", "py311", "py311"),
    ],
    ids=["line_length", "target_version"],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify BlackPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    black_plugin = get_plugin("black")
    black_plugin.set_options(**{option_name: option_value})
    assert_that(black_plugin.options.get(option_name)).is_equal_to(expected)
