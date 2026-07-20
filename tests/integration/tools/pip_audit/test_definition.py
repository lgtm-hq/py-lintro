"""Integration tests for PipAuditPlugin definition."""

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
        ("name", "pip_audit"),
        ("can_fix", False),
        ("priority", 90),
    ],
    ids=["name", "can_fix", "priority"],
)
def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
    attr: str,
    expected: object,
) -> None:
    """Verify PipAuditPlugin definition has correct attribute values."""
    plugin = get_plugin("pip_audit")
    assert_that(getattr(plugin.definition, attr)).is_equal_to(expected)


def test_definition_file_patterns_include_requirements(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify PipAuditPlugin discovers requirements and project manifests."""
    plugin = get_plugin("pip_audit")
    assert_that(plugin.definition.file_patterns).contains(
        "requirements*.txt",
        "pyproject.toml",
        "setup.py",
    )
