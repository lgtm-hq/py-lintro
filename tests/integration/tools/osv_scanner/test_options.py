"""Integration tests for OsvScannerPlugin.set_options method."""

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
    ("option_name", "option_value", "expected"),
    [
        ("timeout", 30, 30),
        ("timeout", 60, 60),
        ("timeout", 300, 300),
    ],
    ids=["timeout_30", "timeout_60", "timeout_300"],
)
def test_set_options_timeout(
    get_plugin: Callable[[str], BaseToolPlugin],
    option_name: str,
    option_value: object,
    expected: object,
) -> None:
    """Verify OsvScannerPlugin.set_options correctly sets timeout."""
    plugin = get_plugin("osv_scanner")
    plugin.set_options(**{option_name: option_value})
    assert_that(plugin.options.get(option_name)).is_equal_to(expected)


def test_invalid_timeout(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify OsvScannerPlugin.set_options rejects invalid timeout values."""
    plugin = get_plugin("osv_scanner")
    with pytest.raises(ValueError, match="must be positive"):
        plugin.set_options(timeout=-1)
