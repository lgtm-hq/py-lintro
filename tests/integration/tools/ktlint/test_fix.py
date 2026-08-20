"""Integration tests for KtlintPlugin fix command.

Both tests assert the fix invariant ``initial = fixed + remaining``.
"""

from __future__ import annotations

import shutil

import pytest
from assertpy import assert_that

from lintro.parsers.ktlint.ktlint_issue import KtlintIssue
from lintro.tools.definitions.ktlint import KtlintPlugin

pytestmark = pytest.mark.skipif(
    shutil.which("ktlint") is None,
    reason="ktlint not installed",
)


def test_fix_full_auto_correct(
    ktlint_plugin: KtlintPlugin,
    ktlint_violation_file: str,
) -> None:
    """All formatting issues are fixed and the invariant holds.

    Args:
        ktlint_plugin: The plugin under test.
        ktlint_violation_file: Copied Kotlin sample with violations.
    """
    initial = ktlint_plugin.check([ktlint_violation_file], {}).issues_count
    result = ktlint_plugin.fix([ktlint_violation_file], {})

    assert_that(initial).is_greater_than(0)
    assert_that(result.success).is_true()
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(result.initial_issues_count).is_equal_to(initial)
    assert_that(result.initial_issues_count).is_equal_to(
        (result.fixed_issues_count or 0) + (result.remaining_issues_count or 0),
    )
    # Re-checking the fixed file reports no issues.
    assert_that(
        ktlint_plugin.check([ktlint_violation_file], {}).issues_count,
    ).is_equal_to(
        0,
    )


def test_fix_partial_leaves_non_correctable(
    ktlint_plugin: KtlintPlugin,
    ktlint_misnamed_file: str,
) -> None:
    """A non-auto-correctable rule remains and the invariant still holds.

    The file is deliberately named so it does not match its class name,
    triggering the non-auto-correctable ``standard:filename`` rule.

    Args:
        ktlint_plugin: The plugin under test.
        ktlint_misnamed_file: Copied Kotlin sample under a violating name.
    """
    initial = ktlint_plugin.check([ktlint_misnamed_file], {}).issues_count
    result = ktlint_plugin.fix([ktlint_misnamed_file], {})

    assert_that(initial).is_greater_than(result.fixed_issues_count)
    assert_that(result.success).is_false()
    assert_that(result.remaining_issues_count).is_greater_than(0)
    assert_that(result.initial_issues_count).is_equal_to(initial)
    assert_that(result.initial_issues_count).is_equal_to(
        (result.fixed_issues_count or 0) + (result.remaining_issues_count or 0),
    )
    rules = {
        issue.rule for issue in (result.issues or []) if isinstance(issue, KtlintIssue)
    }
    assert_that(rules).contains("standard:filename")
