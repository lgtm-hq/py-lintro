"""Integration tests for TyposPlugin check command."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

# Skip all tests if typos is not installed on PATH.
pytestmark = pytest.mark.skipif(
    shutil.which("typos") is None,
    reason="typos not installed",
)


def test_check_reports_real_typos(
    get_plugin: Callable[[str], BaseToolPlugin],
    typos_violation_file: str,
) -> None:
    """Verify typos detects misspellings in the violation sample.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        typos_violation_file: Path to the copied violation sample.
    """
    plugin = get_plugin("typos")

    result = plugin.check([typos_violation_file], {})

    assert_that(result.name).is_equal_to("typos")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than_or_equal_to(2)
    typos_found = {getattr(issue, "typo", None) for issue in result.issues or []}
    assert_that(typos_found).contains("teh")


def test_check_clean_file_passes(
    get_plugin: Callable[[str], BaseToolPlugin],
    typos_clean_file: str,
) -> None:
    """Verify typos reports no issues for a correctly spelled file.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        typos_clean_file: Path to the copied clean sample.
    """
    plugin = get_plugin("typos")

    result = plugin.check([typos_clean_file], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_definition_can_fix(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify the typos definition advertises fix support.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("typos")

    assert_that(plugin.definition.can_fix).is_true()


def test_project_extend_exclude_is_honored(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """A project's ``.typos.toml`` excludes apply to Lintro's explicit paths.

    typos skips its ignore rules for paths named on the command line unless
    ``--force-exclude`` is passed, so this is a regression guard for that flag.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest temporary directory fixture.
    """
    (tmp_path / ".typos.toml").write_text(
        '[files]\nextend-exclude = ["ignored.txt"]\n',
        encoding="utf-8",
    )
    excluded = tmp_path / "ignored.txt"
    excluded.write_text("teh cat\n", encoding="utf-8")
    checked = tmp_path / "checked.txt"
    checked.write_text("teh dog\n", encoding="utf-8")

    plugin = get_plugin("typos")
    result = plugin.check([str(excluded), str(checked)], {})

    reported = {Path(issue.file).name for issue in result.issues or []}
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(reported).is_equal_to({"checked.txt"})


def test_binary_files_are_not_reported(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """A real binary file produces no findings even when named explicitly.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest temporary directory fixture.
    """
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x00teh\x00seperate\x00cheker\x00")

    plugin = get_plugin("typos")
    result = plugin.check([str(binary)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
