"""Integration tests for KtlintPlugin check command."""

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


def test_definition_advertises_fix_support(ktlint_plugin: KtlintPlugin) -> None:
    """The plugin advertises fix support and the expected file patterns.

    Args:
        ktlint_plugin: The plugin under test.
    """
    assert_that(ktlint_plugin.definition.name).is_equal_to("ktlint")
    assert_that(ktlint_plugin.definition.can_fix).is_true()
    assert_that(ktlint_plugin.definition.file_patterns).contains("*.kt", "*.kts")


def test_check_detects_violations(
    ktlint_plugin: KtlintPlugin,
    ktlint_violation_file: str,
) -> None:
    """Detect style violations in a Kotlin source file.

    Args:
        ktlint_plugin: The plugin under test.
        ktlint_violation_file: Copied Kotlin sample with violations.
    """
    result = ktlint_plugin.check([ktlint_violation_file], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
    rules = {
        issue.rule for issue in (result.issues or []) if isinstance(issue, KtlintIssue)
    }
    assert_that(rules).contains("standard:op-spacing")


def test_check_clean_file(
    ktlint_plugin: KtlintPlugin,
    ktlint_clean_file: str,
) -> None:
    """A clean Kotlin file passes the check.

    Args:
        ktlint_plugin: The plugin under test.
        ktlint_clean_file: Copied clean Kotlin sample.
    """
    result = ktlint_plugin.check([ktlint_clean_file], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_kotlin_script(
    ktlint_plugin: KtlintPlugin,
    ktlint_violation_script: str,
) -> None:
    """Detect violations in a Kotlin Script (``.kts``) file.

    Args:
        ktlint_plugin: The plugin under test.
        ktlint_violation_script: Copied Kotlin Script sample with violations.
    """
    result = ktlint_plugin.check([ktlint_violation_script], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
