"""Integration tests for PipAuditPlugin.set_options method."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin


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
    """Verify PipAuditPlugin.set_options correctly sets timeout."""
    plugin = get_plugin("pip_audit")
    plugin.set_options(**{option_name: option_value})
    assert_that(plugin.options.get(option_name)).is_equal_to(expected)


def test_invalid_timeout(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify PipAuditPlugin.set_options rejects negative timeout values."""
    plugin = get_plugin("pip_audit")
    with pytest.raises(ValueError, match="must be non-negative"):
        plugin.set_options(timeout=-1)


def test_invalid_timeout_type(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify PipAuditPlugin.set_options rejects non-numeric timeout values."""
    plugin = get_plugin("pip_audit")
    with pytest.raises(ValueError, match="must be a number"):
        plugin.set_options(timeout="fast")
