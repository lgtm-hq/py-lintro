"""Integration tests for GitleaksPlugin definition attributes."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from tests.integration._tools import require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = require_tool("gitleaks", version_args=("version",))


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "gitleaks"),
        ("can_fix", False),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify GitleaksPlugin definition has correct attribute values.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: The attribute name to check on the definition.
        expected: The expected value of the attribute.
    """
    gitleaks_plugin = get_plugin("gitleaks")
    assert_that(getattr(gitleaks_plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify GitleaksPlugin definition scans all files.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    gitleaks_plugin = get_plugin("gitleaks")
    assert_that(gitleaks_plugin.definition.file_patterns).contains("*")


def test_definition_tool_type(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify GitleaksPlugin is a security tool type.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    from lintro.enums.tool_type import ToolType

    gitleaks_plugin = get_plugin("gitleaks")
    assert_that(gitleaks_plugin.definition.tool_type).is_equal_to(ToolType.SECURITY)
