"""Integration tests for RubocopPlugin fix command."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if rubocop is not installed on PATH.
pytestmark = pytest.mark.skipif(
    shutil.which("rubocop") is None,
    reason="rubocop not installed",
)


def test_fix_preserves_issue_invariant(
    get_plugin: Callable[[str], BaseToolPlugin],
    rubocop_violation_file: str,
) -> None:
    """Autocorrect reduces offenses and preserves the fix invariant.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        rubocop_violation_file: Path to a writable copy of the bad fixture.
    """
    plugin = get_plugin("rubocop")
    result = plugin.fix([rubocop_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("rubocop")
    assert_that(result.initial_issues_count).is_greater_than(0)
    assert_that(result.fixed_issues_count).is_greater_than(0)
    assert_that(result.initial_issues_count).is_equal_to(
        (result.fixed_issues_count or 0) + (result.remaining_issues_count or 0),
    )


def test_fix_clean_file_is_noop(
    get_plugin: Callable[[str], BaseToolPlugin],
    rubocop_clean_file: str,
) -> None:
    """Autocorrect leaves an already clean file untouched.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        rubocop_clean_file: Path to a writable copy of the clean fixture.
    """
    original = Path(rubocop_clean_file).read_text(encoding="utf-8")

    plugin = get_plugin("rubocop")
    result = plugin.fix([rubocop_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(Path(rubocop_clean_file).read_text(encoding="utf-8")).is_equal_to(
        original,
    )
