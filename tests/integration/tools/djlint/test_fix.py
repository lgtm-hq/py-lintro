"""Integration tests for DjlintPlugin fix command."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if djlint is not installed on PATH.
pytestmark = pytest.mark.skipif(
    shutil.which("djlint") is None,
    reason="djlint not installed",
)


def test_fix_reformats_template(
    get_plugin: Callable[[str], BaseToolPlugin],
    djlint_violation_file: str,
) -> None:
    """Verify djLint fix reformats a messy template to a clean state.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        djlint_violation_file: Path to a template with formatting issues.
    """
    plugin = get_plugin("djlint")
    result = plugin.fix([djlint_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("djlint")
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.initial_issues_count).is_greater_than(0)
    assert_that(result.fixed_issues_count).is_greater_than(0)
    assert_that(Path(djlint_violation_file).read_text()).is_not_empty()


def test_fix_clean_file_is_a_no_op(
    get_plugin: Callable[[str], BaseToolPlugin],
    djlint_clean_file: str,
) -> None:
    """Verify djLint fix leaves an already-clean template untouched.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        djlint_clean_file: Path to a well-formatted template.
    """
    before = Path(djlint_clean_file).read_text()
    plugin = get_plugin("djlint")
    result = plugin.fix([djlint_clean_file], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.initial_issues_count).is_equal_to(0)
    assert_that(result.fixed_issues_count).is_equal_to(0)
    assert_that(Path(djlint_clean_file).read_text()).is_equal_to(before)
