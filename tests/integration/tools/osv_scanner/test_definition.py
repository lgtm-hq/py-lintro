"""Integration tests for OsvScannerPlugin definition."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("name", "osv_scanner"),
        ("can_fix", False),
    ],
    ids=["name", "can_fix"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify OsvScannerPlugin definition has correct attribute values."""
    plugin = get_plugin("osv_scanner")
    assert_that(getattr(plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns_empty(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify OsvScannerPlugin has empty file_patterns (uses --recursive)."""
    plugin = get_plugin("osv_scanner")
    assert_that(plugin.definition.file_patterns).is_empty()
