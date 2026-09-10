"""Tests for ShellcheckPlugin definition attributes.

These tests verify the plugin definition has correct metadata and configuration.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from tests.integration._tools import require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = require_tool("shellcheck")


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "shellcheck"),
        ("can_fix", False),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify ShellcheckPlugin definition has correct attribute values.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    shellcheck_plugin = get_plugin("shellcheck")
    assert_that(getattr(shellcheck_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify ShellcheckPlugin definition includes shell file patterns.

    ShellCheck officially supports bash, sh, dash, and ksh dialects.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    shellcheck_plugin = get_plugin("shellcheck")
    assert_that(shellcheck_plugin.definition.file_patterns).contains("*.sh")
    assert_that(shellcheck_plugin.definition.file_patterns).contains("*.bash")
    assert_that(shellcheck_plugin.definition.file_patterns).contains("*.ksh")


def test_definition_has_version_command(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify ShellcheckPlugin definition has a version command.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    shellcheck_plugin = get_plugin("shellcheck")
    assert_that(shellcheck_plugin.definition.version_command).is_not_none()
