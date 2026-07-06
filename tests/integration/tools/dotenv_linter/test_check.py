"""Integration tests for the dotenv-linter check flow.

These tests require the ``dotenv-linter`` binary and run it against real
``.env`` files.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = pytest.mark.skipif(
    shutil.which("dotenv-linter") is None,
    reason="dotenv-linter not installed",
)


def test_check_detects_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    dotenv_violation_file: str,
) -> None:
    """check reports issues for a malformed .env file.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        dotenv_violation_file: Path to a .env file with issues.
    """
    plugin = get_plugin("dotenv_linter")
    result = plugin.check([dotenv_violation_file], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("dotenv_linter")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_extracts_known_checks(
    get_plugin: Callable[[str], BaseToolPlugin],
    dotenv_violation_file: str,
) -> None:
    """check surfaces recognizable dotenv-linter check names.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        dotenv_violation_file: Path to a .env file with issues.
    """
    plugin = get_plugin("dotenv_linter")
    result = plugin.check([dotenv_violation_file], {})

    codes = {issue.code for issue in result.issues}
    assert_that(codes).contains("LowercaseKey")


def test_check_clean_file_passes(
    get_plugin: Callable[[str], BaseToolPlugin],
    dotenv_clean_file: str,
) -> None:
    """check passes on a well-formed .env file.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        dotenv_clean_file: Path to a clean .env file.
    """
    plugin = get_plugin("dotenv_linter")
    result = plugin.check([dotenv_clean_file], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_issue_carries_doc_url(
    get_plugin: Callable[[str], BaseToolPlugin],
    dotenv_violation_file: str,
) -> None:
    """Each parsed issue exposes a resolvable documentation URL.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        dotenv_violation_file: Path to a .env file with issues.
    """
    plugin = get_plugin("dotenv_linter")
    result = plugin.check([dotenv_violation_file], {})

    lowercase = next(i for i in result.issues if i.code == "LowercaseKey")
    doc_url = plugin.doc_url(lowercase.code)
    assert_that(doc_url).is_equal_to(
        "https://dotenv-linter.github.io/#/checks/lowercase_key",
    )
