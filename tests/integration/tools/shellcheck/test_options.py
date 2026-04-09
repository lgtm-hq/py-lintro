"""Tests for ShellcheckPlugin options and configuration.

These tests verify the set_options method and option validation.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = pytest.mark.skipif(
    shutil.which("shellcheck") is None,
    reason="shellcheck not installed",
)


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("severity", "error"),
        ("severity", "warning"),
        ("severity", "info"),
        ("severity", "style"),
        ("shell", "bash"),
        ("shell", "sh"),
        ("exclude", ["SC2086"]),
        ("exclude", ["SC2086", "SC2046"]),
    ],
    ids=[
        "severity_error",
        "severity_warning",
        "severity_info",
        "severity_style",
        "shell_bash",
        "shell_sh",
        "exclude_single",
        "exclude_multiple",
    ],
)
def test_check_with_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    shellcheck_violation_file: str,
    option_name: str,
    option_value: object,
) -> None:
    """Verify ShellCheck check works with various configuration options.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        shellcheck_violation_file: Path to file with lint issues.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
    """
    shellcheck_plugin = get_plugin("shellcheck")
    shellcheck_plugin.set_options(**{option_name: option_value})
    result = shellcheck_plugin.check([shellcheck_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("shellcheck")


@pytest.mark.parametrize(
    ("option_name", "option_value", "expected"),
    [
        ("severity", "error", "error"),
        ("severity", "warning", "warning"),
        ("severity", "info", "info"),
        ("severity", "style", "style"),
        ("shell", "bash", "bash"),
        ("shell", "sh", "sh"),
        ("exclude", ["SC2086"], ["SC2086"]),
        ("exclude", ["SC2086", "SC2046"], ["SC2086", "SC2046"]),
    ],
    ids=[
        "severity_error",
        "severity_warning",
        "severity_info",
        "severity_style",
        "shell_bash",
        "shell_sh",
        "exclude_single",
        "exclude_multiple",
    ],
)
def test_set_options(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify ShellcheckPlugin.set_options correctly sets various options.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        option_name: Name of the option to set.
        option_value: Value to set for the option.
        expected: Expected value when retrieving the option.
    """
    shellcheck_plugin = get_plugin("shellcheck")
    shellcheck_plugin.set_options(**{option_name: option_value})
    assert_that(shellcheck_plugin.options.get(option_name)).is_equal_to(expected)


def test_invalid_severity(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify ShellcheckPlugin.set_options rejects invalid severity values.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    shellcheck_plugin = get_plugin("shellcheck")
    with pytest.raises(ValueError, match="Invalid severity level"):
        shellcheck_plugin.set_options(severity="invalid")


def test_invalid_shell(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify ShellcheckPlugin.set_options rejects invalid shell values.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    shellcheck_plugin = get_plugin("shellcheck")
    with pytest.raises(ValueError, match="Invalid shell dialect"):
        # shell is dialect name, not subprocess shell=True
        shellcheck_plugin.set_options(shell="invalid")  # nosec B604


def test_fix_raises_not_implemented(
    get_plugin: Callable[[str], BaseToolPlugin],
    shellcheck_violation_file: str,
) -> None:
    """Verify ShellCheck fix raises NotImplementedError.

    ShellCheck cannot automatically fix issues, so calling fix should
    raise NotImplementedError.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        shellcheck_violation_file: Path to file with lint issues.
    """
    shellcheck_plugin = get_plugin("shellcheck")
    with pytest.raises(NotImplementedError, match="cannot automatically fix"):
        shellcheck_plugin.fix([shellcheck_violation_file], {})
