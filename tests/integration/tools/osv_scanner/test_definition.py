"""Integration tests for OsvScannerPlugin definition."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if osv-scanner is not installed
pytestmark = pytest.mark.skipif(
    shutil.which("osv-scanner") is None,
    reason="osv-scanner not installed",
)


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


def test_definition_file_patterns(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify OsvScannerPlugin definition includes lockfile patterns."""
    plugin = get_plugin("osv_scanner")
    assert_that(plugin.definition.file_patterns).contains("requirements.txt")
    assert_that(plugin.definition.file_patterns).contains("package-lock.json")
    assert_that(plugin.definition.file_patterns).contains("Cargo.lock")
