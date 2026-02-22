"""Integration tests for shfmt tool definition.

These tests require shfmt to be installed and available in PATH.
They verify the ShfmtPlugin definition, check command, fix command, and
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

# Skip all tests if shfmt is not installed
pytestmark = pytest.mark.skipif(
    shutil.which("shfmt") is None,
    reason="shfmt not installed",
)


@pytest.fixture
def temp_shell_file_with_issues(tmp_path: Path) -> str:
    """Create a temporary shell script with formatting issues.

    Creates a file containing shell code with formatting issues that shfmt
    should detect, including:
    - Inconsistent indentation
    - Missing spaces around operators

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "test_script.sh"
    file_path.write_text(
        """\
#!/bin/bash
if [ "$1" = "test" ];then
echo "hello"
  echo "world"
fi
""",
    )
    return str(file_path)


@pytest.fixture
def temp_shell_file_clean(tmp_path: Path) -> str:
    """Create a temporary shell script with no formatting issues.

    Creates a file containing properly formatted shell code that should pass
    shfmt checking without issues.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "clean_script.sh"
    file_path.write_text(
        """\
#!/bin/bash

# A clean shell script
say_hello() {
	echo "Hello, World!"
}

if [ -n "$1" ]; then
	say_hello
fi
""",
    )
    return str(file_path)


@pytest.fixture
def temp_shell_file_complex_issues(tmp_path: Path) -> str:
    """Create a temporary shell script with multiple formatting issues.

    Creates a file containing code with various formatting issues that shfmt
    should fix, including:
    - No space after semicolon in for loop
    - Inconsistent indentation
    - Binary operator at end of line instead of start

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "complex_script.sh"
    file_path.write_text(
        """\
#!/bin/bash
for i in 1 2 3;do
echo $i
done
if [ "$1" = "a" ] ||
[ "$1" = "b" ]; then
    echo "match"
fi
""",
    )
    return str(file_path)


# --- Tests for ShfmtPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "shfmt"),
        ("can_fix", True),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify ShfmtPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    shfmt_plugin = get_plugin("shfmt")
    assert_that(getattr(shfmt_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify ShfmtPlugin definition includes shell file patterns.

    Tests that the plugin is configured to handle shell files (*.sh, *.bash, *.ksh).

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    shfmt_plugin = get_plugin("shfmt")
    assert_that(shfmt_plugin.definition.file_patterns).contains("*.sh")
    assert_that(shfmt_plugin.definition.file_patterns).contains("*.bash")


def test_definition_has_version_command(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify ShfmtPlugin definition has a version command.

    Tests that the plugin exposes a version command for checking
    the installed shfmt version.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    shfmt_plugin = get_plugin("shfmt")
    assert_that(shfmt_plugin.definition.version_command).is_not_none()


# --- Integration tests for shfmt check command ---


def test_check_file_with_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_shell_file_with_issues: str,
) -> None:
    """Verify shfmt check detects formatting issues in problematic files.

    Runs shfmt on a file containing formatting issues and verifies that
    issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_shell_file_with_issues: Path to file with formatting issues.
    """
    shfmt_plugin = get_plugin("shfmt")
    result = shfmt_plugin.check([temp_shell_file_with_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("shfmt")
    assert_that(result.issues_count).is_greater_than(0)


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_shell_file_clean: str,
) -> None:
    """Verify shfmt check passes on clean files.

    Runs shfmt on a clean file and verifies no issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_shell_file_clean: Path to clean file.
    """
    shfmt_plugin = get_plugin("shfmt")
    result = shfmt_plugin.check([temp_shell_file_clean], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("shfmt")
    assert_that(result.success).is_true()


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify shfmt check handles empty directories gracefully.

    Runs shfmt on an empty directory and verifies a result is returned
    with zero issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    shfmt_plugin = get_plugin("shfmt")
    result = shfmt_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.issues_count).is_equal_to(0)


# --- Integration tests for shfmt fix command ---


def test_fix_formats_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_shell_file_with_issues: str,
) -> None:
    """Verify shfmt fix reformats files with formatting issues.

    Runs shfmt fix on a file with formatting issues and verifies
    the file content changes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_shell_file_with_issues: Path to file with formatting issues.
    """
    shfmt_plugin = get_plugin("shfmt")
    original = Path(temp_shell_file_with_issues).read_text()

    result = shfmt_plugin.fix([temp_shell_file_with_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("shfmt")

    new_content = Path(temp_shell_file_with_issues).read_text()
    assert_that(new_content).is_not_equal_to(original)


def test_fix_complex_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_shell_file_complex_issues: str,
) -> None:
    """Verify shfmt fix handles complex formatting issues.

    Runs shfmt fix on a file with multiple formatting issues and verifies
    fixes are applied.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_shell_file_complex_issues: Path to file with complex issues.
    """
    shfmt_plugin = get_plugin("shfmt")
    original = Path(temp_shell_file_complex_issues).read_text()

    result = shfmt_plugin.fix([temp_shell_file_complex_issues], {})

    assert_that(result).is_not_none()

    new_content = Path(temp_shell_file_complex_issues).read_text()
    assert_that(new_content).is_not_equal_to(original)


def test_fix_clean_file_unchanged(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_shell_file_clean: str,
) -> None:
    """Verify shfmt fix doesn't change already formatted files.

    Runs shfmt fix on a clean file and verifies the content stays the same.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_shell_file_clean: Path to clean file.
    """
    shfmt_plugin = get_plugin("shfmt")
    original = Path(temp_shell_file_clean).read_text()

    result = shfmt_plugin.fix([temp_shell_file_clean], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()

    new_content = Path(temp_shell_file_clean).read_text()
    assert_that(new_content).is_equal_to(original)


# --- Integration tests for shfmt check with various options ---


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("indent", 4),
        ("binary_next_line", True),
        ("switch_case_indent", True),
        ("space_redirects", True),
        ("language_dialect", "bash"),
        ("simplify", True),
    ],
    ids=[
        "indent",
        "binary_next_line",
        "switch_case_indent",
        "space_redirects",
        "language_dialect",
        "simplify",
    ],
)
def test_check_with_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_shell_file_with_issues: str,
    option_name: str,
    option_value: object,
) -> None:
    """Verify shfmt check works with various configuration options.

    Runs shfmt with different options configured and verifies the
    check completes successfully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_shell_file_with_issues: Path to file with formatting issues.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
    """
    shfmt_plugin = get_plugin("shfmt")
    shfmt_plugin.set_options(**{option_name: option_value})
    result = shfmt_plugin.check([temp_shell_file_with_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("shfmt")


# --- Tests for ShfmtPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("indent", 4, 4),
        ("indent", 0, 0),
        ("binary_next_line", True, True),
        ("switch_case_indent", True, True),
        ("space_redirects", True, True),
        ("language_dialect", "bash", "bash"),
        ("language_dialect", "posix", "posix"),
        ("simplify", True, True),
    ],
    ids=[
        "indent_spaces",
        "indent_tabs",
        "binary_next_line",
        "switch_case_indent",
        "space_redirects",
        "dialect_bash",
        "dialect_posix",
        "simplify",
    ],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify ShfmtPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    shfmt_plugin = get_plugin("shfmt")
    shfmt_plugin.set_options(**{option_name: option_value})
    assert_that(shfmt_plugin.options.get(option_name)).is_equal_to(expected)


def test_invalid_language_dialect(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify ShfmtPlugin.set_options rejects invalid language_dialect values.

    Tests that invalid language_dialect values raise ValueError.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    shfmt_plugin = get_plugin("shfmt")
    with pytest.raises(ValueError, match="Invalid language_dialect"):
        shfmt_plugin.set_options(language_dialect="invalid")
