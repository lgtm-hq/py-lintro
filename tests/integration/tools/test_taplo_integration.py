"""Integration tests for Taplo tool definition.

These tests require taplo to be installed and available in PATH.
They verify the TaploPlugin definition, check command, fix command,
and set_options method.
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

pytestmark = require_tool("taplo")


@pytest.fixture
def temp_toml_file_with_issues(tmp_path: Path) -> str:
    """Create a temporary TOML file with formatting issues.

    Creates a file containing TOML with deliberate formatting issues
    that taplo should detect, including:
    - Inconsistent spacing around equals
    - Unaligned entries

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "config.toml"
    file_path.write_text(
        """\
# TOML file with formatting issues
[package]
name="example"
version = "0.1.0"
description    =    "Inconsistent spacing"

[dependencies]
zlib = "1.0"
alib = "2.0"
""",
    )
    return str(file_path)


@pytest.fixture
def temp_toml_file_clean(tmp_path: Path) -> str:
    """Create a temporary TOML file with no issues.

    Creates a file containing properly formatted TOML that should pass
    taplo checking without issues.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "clean.toml"
    file_path.write_text(
        """\
# Clean TOML file
[package]
name = "example"
version = "0.1.0"
description = "A properly formatted TOML file"

[dependencies]
alib = "2.0"
zlib = "1.0"
""",
    )
    return str(file_path)


# --- Tests for TaploPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "taplo"),
        ("can_fix", True),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify TaploPlugin definition has correct attribute values.

    Tests that the plugin definition exposes the expected values for
    name and can_fix attributes.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    taplo_plugin = get_plugin("taplo")
    assert_that(getattr(taplo_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify TaploPlugin definition includes expected file patterns.

    Tests that the plugin is configured to handle TOML files.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    taplo_plugin = get_plugin("taplo")
    assert_that(taplo_plugin.definition.file_patterns).contains("*.toml")


def test_definition_tool_type(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify TaploPlugin is both a linter and formatter.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    from lintro.enums.tool_type import ToolType

    taplo_plugin = get_plugin("taplo")
    expected_type = ToolType.LINTER | ToolType.FORMATTER
    assert_that(taplo_plugin.definition.tool_type).is_equal_to(expected_type)


# --- Integration tests for taplo check command ---


def test_check_file_with_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_toml_file_with_issues: str,
) -> None:
    """Verify taplo check detects formatting issues in TOML files.

    Runs taplo on a file containing deliberate formatting issues
    and verifies that issues are found.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_toml_file_with_issues: Path to file with formatting issues.
    """
    taplo_plugin = get_plugin("taplo")
    result = taplo_plugin.check([temp_toml_file_with_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("taplo")
    # Taplo should detect formatting issues - check success=False (exit code)
    # as primary indicator since output parsing may vary by environment
    assert_that(result.success).is_false()


def test_check_clean_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_toml_file_clean: str,
) -> None:
    """Verify taplo check passes on properly formatted files.

    Runs taplo on a properly formatted file and verifies no issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_toml_file_clean: Path to file with no issues.
    """
    taplo_plugin = get_plugin("taplo")
    result = taplo_plugin.check([temp_toml_file_clean], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("taplo")
    assert_that(result.success).is_true()


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify taplo check handles empty directories gracefully.

    Runs taplo on an empty directory and verifies a result is returned
    without errors.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    taplo_plugin = get_plugin("taplo")
    result = taplo_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()


# --- Integration tests for taplo fix command ---


def test_fix_formats_toml_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_toml_file_with_issues: str,
) -> None:
    """Verify taplo fix formats TOML files.

    Runs taplo fix on a file with formatting issues and verifies
    that fixes are applied by checking both the result and file content.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_toml_file_with_issues: Path to file with formatting issues.
    """
    taplo_plugin = get_plugin("taplo")
    result = taplo_plugin.fix([temp_toml_file_with_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("taplo")
    # After fix, the file should be formatted
    assert_that(result.fixed_issues_count).is_greater_than_or_equal_to(0)

    # Verify the file was actually reformatted
    fixed_content = Path(temp_toml_file_with_issues).read_text()
    # Taplo normalizes spacing around '=' to single space
    assert_that(fixed_content).contains('name = "example"')
    assert_that(fixed_content).contains('description = "Inconsistent spacing"')


# --- Tests for TaploPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("schema", "/path/to/schema.json"),
        ("aligned_arrays", True),
        ("aligned_entries", True),
        ("array_trailing_comma", True),
        ("indent_string", "    "),
        ("reorder_keys", True),
    ],
    ids=[
        "schema_path",
        "aligned_arrays",
        "aligned_entries",
        "trailing_comma",
        "indent_string",
        "reorder_keys",
    ],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
) -> None:
    """Verify TaploPlugin.set_options correctly sets various options.

    Tests that plugin options can be set and retrieved correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
    """
    taplo_plugin = get_plugin("taplo")
    taplo_plugin.set_options(**{option_name: option_value})
    assert_that(taplo_plugin.options.get(option_name)).is_equal_to(option_value)
