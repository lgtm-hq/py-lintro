"""Integration tests for TyposPlugin fix command."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from assertpy import assert_that

from tests.integration._tools import require_tool

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = require_tool("typos")


def test_fix_corrects_real_typos(
    get_plugin: Callable[[str], BaseToolPlugin],
    typos_violation_file: str,
) -> None:
    """Verify typos auto-corrects misspellings and reports fixed counts.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        typos_violation_file: Path to the copied violation sample.
    """
    plugin = get_plugin("typos")

    result = plugin.fix([typos_violation_file], {})

    assert_that(result.name).is_equal_to("typos")
    assert_that(result.fixed_issues_count).is_greater_than_or_equal_to(2)
    # Invariant: initial == fixed + remaining.
    assert_that(
        (result.fixed_issues_count or 0) + (result.remaining_issues_count or 0),
    ).is_equal_to(result.initial_issues_count)
    # The fixture is fully correctable, so the fix must complete cleanly.
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(result.success).is_true()

    fixed_content = Path(typos_violation_file).read_text(encoding="utf-8")
    assert_that(fixed_content).contains("sample")
    assert_that(fixed_content).contains("separate")
    assert_that(fixed_content).contains("the words")
    assert_that(fixed_content).contains("spell checker")
    assert_that(fixed_content).does_not_contain("teh")


def test_fix_clean_file_is_noop(
    get_plugin: Callable[[str], BaseToolPlugin],
    typos_clean_file: str,
) -> None:
    """Verify fixing an already-clean file changes nothing and succeeds.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        typos_clean_file: Path to the copied clean sample.
    """
    plugin = get_plugin("typos")
    before = Path(typos_clean_file).read_text(encoding="utf-8")

    result = plugin.fix([typos_clean_file], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(0)
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(Path(typos_clean_file).read_text(encoding="utf-8")).is_equal_to(before)
