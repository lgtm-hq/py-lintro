"""Integration tests for TrivyPlugin definition."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "trivy"),
        ("can_fix", False),
        ("tool_type", ToolType.SECURITY),
    ],
    ids=["name", "can_fix", "tool_type"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify TrivyPlugin definition has correct attribute values.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        attr: Definition attribute under test.
        expected: Expected attribute value.
    """
    plugin = get_plugin("trivy")
    assert_that(getattr(plugin.definition, attr)).is_equal_to(expected)


def test_definition_targets_dependency_manifests(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify TrivyPlugin is scoped to lockfiles and manifests.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("trivy")
    assert_that(plugin.definition.file_patterns).contains("requirements.txt")
    assert_that(plugin.definition.file_patterns).contains("uv.lock")


def test_definition_defaults_are_hermetic(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify trivy never downloads a DB or calls advisory APIs by default.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    defaults = get_plugin("trivy").definition.default_options
    assert_that(defaults).contains_entry({"skip_db_update": True})
    assert_that(defaults).contains_entry({"offline_scan": True})
