"""Integration tests for Yamllint tool definition.

These tests require yamllint to be installed and available in PATH.
They verify the YamllintPlugin definition, check command, and output preservation.
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

# Skip all tests if yamllint is not installed
pytestmark = pytest.mark.skipif(
    shutil.which("yamllint") is None,
    reason="yamllint not installed",
)


@pytest.fixture
def temp_yaml_file_valid(tmp_path: Path) -> str:
    """Create a temporary valid YAML file.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "valid.yaml"
    file_path.write_text(
        """\
---
name: test
version: 1.0.0
description: A valid YAML file
items:
  - one
  - two
  - three
""",
    )
    return str(file_path)


@pytest.fixture
def temp_yaml_file_with_issues(tmp_path: Path) -> str:
    """Create a temporary YAML file with linting issues.

    Creates a file with trailing spaces and indentation issues
    that yamllint should detect.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "issues.yaml"
    # Note: trailing spaces after "test" and inconsistent indentation
    file_path.write_text(
        "name: test   \n"  # trailing spaces
        "items:\n"
        "  - one\n"
        "   - two\n"  # wrong indentation (3 spaces instead of 2)
        "  - three\n",
    )
    return str(file_path)


@pytest.fixture
def temp_yaml_file_syntax_error(tmp_path: Path) -> str:
    """Create a temporary YAML file with syntax errors.

    Creates a file with invalid YAML syntax that yamllint will report
    but may not produce parseable issue lines.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "syntax_error.yaml"
    # Invalid YAML: duplicate keys and malformed structure
    file_path.write_text(
        """\
name: test
name: duplicate
items:
  - one
  - : invalid
""",
    )
    return str(file_path)


# --- Tests for YamllintPlugin definition ---


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "yamllint"),
        ("can_fix", False),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify YamllintPlugin definition has correct attribute values.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    yamllint_plugin = get_plugin("yamllint")
    assert_that(getattr(yamllint_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify YamllintPlugin definition includes YAML file patterns.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    yamllint_plugin = get_plugin("yamllint")
    assert_that(yamllint_plugin.definition.file_patterns).contains("*.yml")
    assert_that(yamllint_plugin.definition.file_patterns).contains("*.yaml")


# --- Integration tests for yamllint check command ---


def test_check_valid_yaml_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_yaml_file_valid: str,
) -> None:
    """Verify yamllint check passes on valid YAML files.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_yaml_file_valid: Path to valid YAML file.
    """
    yamllint_plugin = get_plugin("yamllint")
    result = yamllint_plugin.check([temp_yaml_file_valid], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("yamllint")
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_yaml_file_with_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_yaml_file_with_issues: str,
) -> None:
    """Verify yamllint check detects issues in problematic YAML files.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_yaml_file_with_issues: Path to file with YAML issues.
    """
    yamllint_plugin = get_plugin("yamllint")
    result = yamllint_plugin.check([temp_yaml_file_with_issues], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("yamllint")
    assert_that(result.issues_count).is_greater_than(0)
    # Verify raw output is preserved when there are issues
    assert_that(result.output).is_not_none()


def test_check_yaml_file_with_syntax_error(
    get_plugin: Callable[[str], BaseToolPlugin],
    temp_yaml_file_syntax_error: str,
) -> None:
    """Verify yamllint check handles syntax errors and preserves output.

    This tests the bug fix where raw output was being discarded when
    yamllint failed but parsing produced no issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        temp_yaml_file_syntax_error: Path to file with YAML syntax errors.
    """
    yamllint_plugin = get_plugin("yamllint")
    result = yamllint_plugin.check([temp_yaml_file_syntax_error], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("yamllint")
    # Yamllint must report failure or issues for syntax errors - never silently pass
    if result.success:
        assert_that(result.issues_count).is_greater_than(0)
    # The key assertion: output should always be preserved even if parsing fails
    # to extract structured issues
    assert_that(result.output).is_not_none()


def test_check_empty_directory(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify yamllint check handles empty directories gracefully.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    yamllint_plugin = get_plugin("yamllint")
    result = yamllint_plugin.check([str(tmp_path)], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()


def test_check_nonexistent_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify yamllint check raises FileNotFoundError for nonexistent files.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    nonexistent = tmp_path / "nonexistent.yaml"
    yamllint_plugin = get_plugin("yamllint")

    with pytest.raises(FileNotFoundError):
        yamllint_plugin.check([str(nonexistent)], {})


def test_check_preserves_output_on_failure(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Verify raw output is preserved when yamllint fails.

    This is a regression test for the bug where error messages were
    being discarded when the tool failed but parsing produced no
    structured issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    # Create a file that will cause yamllint to fail with unparseable output
    # by using completely invalid YAML
    invalid_file = tmp_path / "broken.yaml"
    invalid_file.write_text(
        """\
{{{invalid: yaml: content:::
  - this: [is: broken
""",
    )

    yamllint_plugin = get_plugin("yamllint")
    result = yamllint_plugin.check([str(invalid_file)], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("yamllint")
    # The critical assertion: when yamllint fails, output must be preserved
    # so users can see what went wrong
    if not result.success:
        assert_that(result.output).is_not_none()
        assert_that(result.output).is_not_empty()


# --- Tests for YamllintPlugin.set_options method ---


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("strict", True),
        ("relaxed", True),
        ("no_warnings", True),
    ],
    ids=["strict", "relaxed", "no_warnings"],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
) -> None:
    """Verify YamllintPlugin.set_options correctly sets various options.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
    """
    yamllint_plugin = get_plugin("yamllint")
    yamllint_plugin.set_options(**{option_name: option_value})
    assert_that(yamllint_plugin.options.get(option_name)).is_equal_to(option_value)


def test_set_options_format(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify format option is normalized correctly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    yamllint_plugin = get_plugin("yamllint")
    yamllint_plugin.set_options(format="standard")
    assert_that(yamllint_plugin.options.get("format")).is_equal_to("standard")
