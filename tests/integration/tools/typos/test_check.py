"""Integration tests for TyposPlugin check command."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if typos is not installed on PATH.
pytestmark = pytest.mark.skipif(
    shutil.which("typos") is None,
    reason="typos not installed",
)


def test_check_reports_real_typos(
    get_plugin: Callable[[str], BaseToolPlugin],
    typos_violation_file: str,
) -> None:
    """Verify typos detects misspellings in the violation sample.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        typos_violation_file: Path to the copied violation sample.
    """
    plugin = get_plugin("typos")

    result = plugin.check([typos_violation_file], {})

    assert_that(result.name).is_equal_to("typos")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than_or_equal_to(2)
    typos_found = {getattr(issue, "typo", None) for issue in result.issues or []}
    assert_that(typos_found).contains("teh")


def test_check_clean_file_passes(
    get_plugin: Callable[[str], BaseToolPlugin],
    typos_clean_file: str,
) -> None:
    """Verify typos reports no issues for a correctly spelled file.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        typos_clean_file: Path to the copied clean sample.
    """
    plugin = get_plugin("typos")

    result = plugin.check([typos_clean_file], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_definition_can_fix(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify the typos definition advertises fix support.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("typos")

    assert_that(plugin.definition.can_fix).is_true()
