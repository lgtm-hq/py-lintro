"""Integration tests for the typos tool definition.

These tests require the ``typos`` binary to be installed and available in PATH.
They exercise the real check and fix paths against temporary files.
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

# Skip all tests if typos is not installed.
pytestmark = pytest.mark.skipif(
    shutil.which("typos") is None,
    reason="typos not installed",
)


@pytest.fixture
def file_with_typos(tmp_path: Path) -> str:
    """Create a temporary file containing deliberate misspellings.

    Args:
        tmp_path: Pytest temporary directory fixture.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "notes.txt"
    file_path.write_text("We shoud fix teh spelling here.\n")
    return str(file_path)


@pytest.fixture
def clean_file(tmp_path: Path) -> str:
    """Create a temporary file with no spelling issues.

    Args:
        tmp_path: Pytest temporary directory fixture.

    Returns:
        Path to the created file as a string.
    """
    file_path = tmp_path / "clean.txt"
    file_path.write_text("This file has no spelling problems at all.\n")
    return str(file_path)


def test_check_reports_real_typos(
    get_plugin: Callable[[str], BaseToolPlugin],
    file_with_typos: str,
) -> None:
    """Typos detects misspellings in a real file."""
    plugin = get_plugin("typos")

    result = plugin.check([file_with_typos], {})

    assert_that(result.name).is_equal_to("typos")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than_or_equal_to(2)
    typos_found = {issue.typo for issue in result.issues}
    assert_that(typos_found).contains("teh")


def test_check_clean_file_passes(
    get_plugin: Callable[[str], BaseToolPlugin],
    clean_file: str,
) -> None:
    """Typos reports no issues for a correctly spelled file."""
    plugin = get_plugin("typos")

    result = plugin.check([clean_file], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_fix_corrects_real_typos(
    get_plugin: Callable[[str], BaseToolPlugin],
    file_with_typos: str,
) -> None:
    """Typos auto-corrects misspellings and reports fixed counts."""
    plugin = get_plugin("typos")

    result = plugin.fix([file_with_typos], {})

    assert_that(result.name).is_equal_to("typos")
    assert_that(result.fixed_issues_count).is_greater_than_or_equal_to(2)
    # Invariant: initial == fixed + remaining.
    assert_that(
        result.fixed_issues_count + result.remaining_issues_count,
    ).is_equal_to(result.initial_issues_count)

    fixed_content = Path(file_with_typos).read_text()
    assert_that(fixed_content).contains("should")
    assert_that(fixed_content).contains("the spelling")
    assert_that(fixed_content).does_not_contain("teh")


def test_definition_can_fix(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """The typos definition advertises fix support."""
    plugin = get_plugin("typos")

    assert_that(plugin.definition.can_fix).is_true()
