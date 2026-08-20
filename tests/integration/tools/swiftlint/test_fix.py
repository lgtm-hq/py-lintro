"""Integration tests for SwiftlintPlugin fix command.

These tests require SwiftLint to be installed and available on PATH. They
exercise the real ``swiftlint --fix`` path, including the
``initial == fixed + remaining`` invariant.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if swiftlint is not installed on PATH.
pytestmark = pytest.mark.skipif(
    shutil.which("swiftlint") is None,
    reason="swiftlint not installed",
)


def test_fix_invariant_holds(
    get_plugin: Callable[[str], BaseToolPlugin],
    swiftlint_violation_file: str,
) -> None:
    """Fix satisfies ``initial == fixed + remaining`` on a real run.

    The fixture contains at least one auto-correctable violation
    (``trailing_semicolon``) and some that are not, so the fix run should
    report both a non-zero fixed count and a non-zero remaining count.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        swiftlint_violation_file: Path to a file with violations.
    """
    plugin = get_plugin("swiftlint")

    # Capture the initial issue count via a check first.
    initial = plugin.check([swiftlint_violation_file], {}).issues_count
    assert_that(initial).is_greater_than(0)

    result = plugin.fix([swiftlint_violation_file], {})

    assert_that(result.initial_issues_count).is_equal_to(
        (result.fixed_issues_count or 0) + (result.remaining_issues_count or 0),
    )
    assert_that(result.initial_issues_count).is_equal_to(initial)
    assert_that(result.fixed_issues_count).is_greater_than(0)


def test_fix_clean_file_unchanged(
    get_plugin: Callable[[str], BaseToolPlugin],
    swiftlint_clean_file: str,
) -> None:
    """Fix leaves an already-clean Swift file untouched.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        swiftlint_clean_file: Path to a clean file.
    """
    plugin = get_plugin("swiftlint")
    original = Path(swiftlint_clean_file).read_text()

    result = plugin.fix([swiftlint_clean_file], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(0)
    assert_that(Path(swiftlint_clean_file).read_text()).is_equal_to(original)
