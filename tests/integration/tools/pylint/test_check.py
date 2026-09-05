"""Integration tests running the real ``pylint`` binary."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.parsers.pylint.pylint_issue import PylintIssue

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = pytest.mark.skipif(
    shutil.which("pylint") is None,
    reason="pylint not installed",
)


def test_check_reports_exactly_one_duplicate_code_issue(
    get_plugin: Callable[[str], BaseToolPlugin],
    duplicate_code_project: str,
) -> None:
    """Two modules sharing a 15-line block yield exactly one R0801 issue.

    One clone set means one message, not one per participating file — that is
    pylint's own behaviour and the count this plugin must pass through
    unchanged.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        duplicate_code_project: Staged sample with a duplicated block.
    """
    plugin = get_plugin("pylint")
    result = plugin.check([duplicate_code_project], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("pylint")
    assert_that(result.success).is_false()
    assert_that(result.skipped).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert result.issues is not None  # narrow type for mypy
    issue = result.issues[0]
    assert isinstance(issue, PylintIssue)  # nosec B101 - narrow type for mypy
    assert_that(issue.code).is_equal_to("R0801")
    assert_that(issue.symbol).is_equal_to("duplicate-code")
    assert_that(issue.message_type).is_equal_to("refactor")
    # The body names both participating modules and quotes the shared block.
    assert_that(issue.message).starts_with("Similar lines in 2 files")
    assert_that(issue.message).contains("==first:", "==second:")
    assert_that(issue.message).contains("for index, entry in enumerate(records):")


def test_check_passes_on_a_project_without_clones(
    get_plugin: Callable[[str], BaseToolPlugin],
    clean_project: str,
) -> None:
    """Modules that share nothing produce a clean, non-skipped result.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        clean_project: Staged sample with no duplicated block.
    """
    plugin = get_plugin("pylint")
    result = plugin.check([clean_project], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.skipped).is_false()


def test_tool_options_reach_the_command_line(
    get_plugin: Callable[[str], BaseToolPlugin],
    duplicate_code_project: str,
) -> None:
    """``pylint:disable=`` reaches ``--disable`` and silences the finding.

    The staged sample reports exactly one R0801 with its own config; turning
    the check off through a tool option must take that to zero. Asserting the
    change in behaviour — rather than the argv — proves the option really
    reaches pylint.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        duplicate_code_project: Staged sample with a duplicated block.
    """
    plugin = get_plugin("pylint")
    plugin.set_options(disable="duplicate-code")
    result = plugin.check([duplicate_code_project], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.skipped).is_false()


def test_check_skips_a_path_with_no_python_files(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """A path holding no Python files reports zero issues without running.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    (tmp_path / "notes.md").write_text("# nothing to lint\n", encoding="utf-8")

    plugin = get_plugin("pylint")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result.issues_count).is_equal_to(0)
