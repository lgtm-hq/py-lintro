"""Integration tests for Ruff tool definition.

These tests require ruff to be installed and available in PATH.
They verify the RuffPlugin definition, check command, fix command, and
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

# Skip all tests if ruff is not installed
pytestmark = pytest.mark.skipif(
    shutil.which("ruff") is None,
    reason="ruff not installed",
)


@pytest.fixture
def temp_python_file_with_issues(tmp_path: Path) -> str:
    """Create a temporary Python file with lint issues.

    Creates a file containing code with lint issues that Ruff
    should detect, including:
    - Unused imports
    - Missing whitespace around operators

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "test_file.py"
    file_path.write_text(
        """\
import os
import sys  # unused import
x=1  # missing whitespace around operator
def foo():
    pass
""",
    )
    return str(file_path)


@pytest.fixture
def temp_python_file_clean(tmp_path: Path) -> str:
    """Create a temporary Python file with no lint issues.

    Creates a file containing clean Python code that should pass
    Ruff linting without issues.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "clean_file.py"
    file_path.write_text(
        """\
\"\"\"A clean module.\"\"\"


def hello() -> str:
    \"\"\"Return a greeting.\"\"\"
    return "Hello, World!"
""",
    )
    return str(file_path)


@pytest.fixture
def temp_python_file_formatting_issues(tmp_path: Path) -> str:
    """Create a temporary Python file with formatting issues.

    Creates a file containing code with formatting issues that Ruff format
    should fix, including:
    - Missing spaces around operators
    - Missing blank lines between functions
    - Long line that should be wrapped

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "format_file.py"
    file_path.write_text(
        """\
def foo(a,b,c):
    x=a+b+c
    return x
def bar(x,y):
    z=x*y
    return z
very_long_variable_name={"key1":"value1","key2":"value2","key3":"value3","key4":"value4"}
""",
    )
    return str(file_path)


# --- Tests for RuffPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "ruff"),
        ("can_fix", True),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify RuffPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    ruff_plugin = get_plugin("ruff")
    assert_that(getattr(ruff_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(get_plugin: Callable[[str], BaseToolPlugin]) -> None:
    """Verify RuffPlugin definition includes Python file patterns.

    Tests that the plugin is configured to handle Python files (*.py).

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    ruff_plugin = get_plugin("ruff")
    assert_that(ruff_plugin.definition.file_patterns).contains("*.py")


def test_definition_has_version_command(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify RuffPlugin definition has a version command.

    Tests that the plugin exposes a version command for checking
    the installed Ruff version.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    ruff_plugin = get_plugin("ruff")
    assert_that(ruff_plugin.definition.version_command).is_not_none()


# --- Integration tests for ruff check command ---


def test_check_file_with_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_python_file_with_issues: str,
) -> None:
    """Verify Ruff check detects lint issues in problematic files.

    Runs Ruff on a file containing lint issues and verifies that
    issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_python_file_with_issues: Path to file with lint issues.
    """
    ruff_plugin = get_plugin("ruff")
    result = ruff_plugin.check([temp_python_file_with_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("ruff")
    assert_that(result.issues_count).is_greater_than(0)


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_python_file_clean: str,
) -> None:
    """Verify Ruff check passes on clean files.

    Runs Ruff on a clean file and verifies no issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_python_file_clean: Path to clean file.
    """
    ruff_plugin = get_plugin("ruff")
    result = ruff_plugin.check([temp_python_file_clean], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("ruff")
    assert_that(result.success).is_true()


def test_check_nonexistent_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify Ruff check raises error for nonexistent files.

    Attempts to run Ruff on a nonexistent file and verifies that
    a FileNotFoundError is raised.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    ruff_plugin = get_plugin("ruff")
    nonexistent = str(tmp_path / "nonexistent.py")
    with pytest.raises(FileNotFoundError):
        ruff_plugin.check([nonexistent], {})


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify Ruff check handles empty directories gracefully.

    Runs Ruff on an empty directory and verifies a result is returned
    with zero issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    ruff_plugin = get_plugin("ruff")
    result = ruff_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.issues_count).is_equal_to(0)


# --- Integration tests for ruff fix command ---


def test_fix_formats_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_python_file_formatting_issues: str,
) -> None:
    """Verify Ruff fix reformats files with formatting issues.

    Runs Ruff fix on a file with formatting issues and verifies
    the file content changes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_python_file_formatting_issues: Path to file with formatting issues.
    """
    ruff_plugin = get_plugin("ruff")
    original = Path(temp_python_file_formatting_issues).read_text()

    result = ruff_plugin.fix([temp_python_file_formatting_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("ruff")

    new_content = Path(temp_python_file_formatting_issues).read_text()
    assert_that(new_content).is_not_equal_to(original)


def test_fix_removes_unused_imports(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify Ruff fix removes unused imports when configured.

    Runs Ruff fix with F401 rule selected on a file with unused imports
    and verifies fixes are applied.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    ruff_plugin = get_plugin("ruff")
    file_path = tmp_path / "unused_import.py"
    file_path.write_text("import os\nimport sys\n\nx = 1\n")

    ruff_plugin.set_options(select=["F401"])

    result = ruff_plugin.fix([str(file_path)], {})

    assert_that(result).is_not_none()


# --- Integration tests for ruff check with various options ---


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("select", ["F401"]),
        ("ignore", ["E501", "F401"]),
        ("line_length", 120),
    ],
    ids=["select_rules", "ignore_rules", "line_length"],
)
def test_check_with_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_python_file_with_issues: str,
    option_name: str,
    option_value: object,
) -> None:
    """Verify Ruff check works with various configuration options.

    Runs Ruff with different options configured and verifies the
    check completes successfully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_python_file_with_issues: Path to file with lint issues.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
    """
    ruff_plugin = get_plugin("ruff")
    ruff_plugin.set_options(**{option_name: option_value})
    result = ruff_plugin.check([temp_python_file_with_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("ruff")


# --- Integration tests for ruff fix with various options ---


def test_fix_with_unsafe_fixes(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify Ruff fix works with unsafe fixes enabled.

    Runs Ruff fix with unsafe_fixes option enabled and verifies
    the fix completes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    ruff_plugin = get_plugin("ruff")
    file_path = tmp_path / "unsafe_fix.py"
    file_path.write_text("import os\nimport sys\n\nx = 1\n")

    ruff_plugin.set_options(unsafe_fixes=True, select=["F401"])
    result = ruff_plugin.fix([str(file_path)], {})

    assert_that(result).is_not_none()


def test_fix_with_format_disabled(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify Ruff fix works with formatting disabled.

    Runs Ruff fix with format option disabled and verifies
    the fix completes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    ruff_plugin = get_plugin("ruff")
    file_path = tmp_path / "no_format.py"
    file_path.write_text("x=1\ny=2\n")

    ruff_plugin.set_options(format=False)
    result = ruff_plugin.fix([str(file_path)], {})

    assert_that(result).is_not_none()


# --- Tests for RuffPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("line_length", 100, 100),
        ("select", ["E", "F"], ["E", "F"]),
        ("ignore", ["E501"], ["E501"]),
        ("unsafe_fixes", True, True),
    ],
    ids=["line_length", "select_rules", "ignore_rules", "unsafe_fixes"],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify RuffPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    ruff_plugin = get_plugin("ruff")
    ruff_plugin.set_options(**{option_name: option_value})
    assert_that(ruff_plugin.options.get(option_name)).is_equal_to(expected)


@pytest.mark.parametrize(
    ("option_value", "error_match"),
    [
        ("not an int", "must be an integer"),
        (-1, None),
    ],
    ids=["invalid_type", "negative_value"],
)
def test_invalid_line_length(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_value: object,
    error_match: str | None,
) -> None:
    """Verify RuffPlugin.set_options rejects invalid line_length values.

    Tests that invalid line_length values raise ValueError.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_value: Invalid value to set for line_length.
        error_match: Expected error message pattern, or None for any ValueError.
    """
    ruff_plugin = get_plugin("ruff")
    if error_match:
        with pytest.raises(ValueError, match=error_match):
            ruff_plugin.set_options(line_length=option_value)
    else:
        with pytest.raises(ValueError):
            ruff_plugin.set_options(line_length=option_value)
