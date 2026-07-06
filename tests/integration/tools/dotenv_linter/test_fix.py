"""Integration tests for the dotenv-linter fix flow.

These tests require the ``dotenv-linter`` binary and exercise its in-place
auto-fixing against real ``.env`` files.
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

pytestmark = pytest.mark.skipif(
    shutil.which("dotenv-linter") is None,
    reason="dotenv-linter not installed",
)


def test_fix_resolves_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    dotenv_violation_file: str,
) -> None:
    """fix auto-corrects the issues and leaves the file clean.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        dotenv_violation_file: Path to a .env file with issues.
    """
    plugin = get_plugin("dotenv_linter")
    result = plugin.fix([dotenv_violation_file], {})

    assert_that(result.name).is_equal_to("dotenv_linter")
    assert_that(result.initial_issues_count).is_greater_than(0)
    assert_that(result.fixed_issues_count).is_greater_than(0)
    # Invariant: initial == fixed + remaining.
    assert_that(
        result.fixed_issues_count + result.remaining_issues_count,
    ).is_equal_to(result.initial_issues_count)


def test_fix_rewrites_file_in_place(
    get_plugin: Callable[[str], BaseToolPlugin],
    dotenv_violation_file: str,
) -> None:
    """fix modifies the file so a subsequent check finds no issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        dotenv_violation_file: Path to a .env file with issues.
    """
    plugin = get_plugin("dotenv_linter")
    before = Path(dotenv_violation_file).read_text()
    plugin.fix([dotenv_violation_file], {})
    after = Path(dotenv_violation_file).read_text()

    assert_that(after).is_not_equal_to(before)

    recheck = get_plugin("dotenv_linter").check([dotenv_violation_file], {})
    assert_that(recheck.success).is_true()
    assert_that(recheck.issues_count).is_equal_to(0)


def test_fix_does_not_create_backup_file(
    get_plugin: Callable[[str], BaseToolPlugin],
    dotenv_violation_file: str,
) -> None:
    """fix runs with --no-backup so no .env.bak file is left behind.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        dotenv_violation_file: Path to a .env file with issues.
    """
    plugin = get_plugin("dotenv_linter")
    plugin.fix([dotenv_violation_file], {})

    backup = Path(f"{dotenv_violation_file}.bak")
    assert_that(backup.exists()).is_false()


def test_fix_clean_file_is_noop(
    get_plugin: Callable[[str], BaseToolPlugin],
    dotenv_clean_file: str,
) -> None:
    """fix on a clean file reports nothing to fix.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        dotenv_clean_file: Path to a clean .env file.
    """
    plugin = get_plugin("dotenv_linter")
    result = plugin.fix([dotenv_clean_file], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(0)
    assert_that(result.fixed_issues_count).is_equal_to(0)
