"""Integration tests running the real ``lint-imports`` binary."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from assertpy import assert_that

from lintro.parsers.import_linter.import_linter_issue import ImportLinterIssue

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin

pytestmark = pytest.mark.skipif(
    shutil.which("lint-imports") is None,
    reason="import-linter (lint-imports) not installed",
)


def test_check_reports_the_broken_chain(
    get_plugin: Callable[[str], BaseToolPlugin],
    broken_contract_project: str,
) -> None:
    """A deliberately broken layers contract yields one issue with its chain.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        broken_contract_project: Staged project with a broken contract.
    """
    plugin = get_plugin("import-linter")
    result = plugin.check([broken_contract_project], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("import-linter")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert result.issues is not None  # narrow type for mypy
    issue = result.issues[0]
    assert isinstance(issue, ImportLinterIssue)  # nosec B101 - narrow type for mypy
    assert_that(issue.code).is_equal_to("Layered architecture")
    assert_that(issue.file).is_equal_to("layered.storage")
    assert_that(issue.line).is_equal_to(0)
    assert_that(issue.message).is_equal_to(
        "layered.storage -> layered.helpers -> layered.compat -> layered.api",
    )


def test_check_passes_when_contracts_are_kept(
    get_plugin: Callable[[str], BaseToolPlugin],
    kept_contract_project: str,
) -> None:
    """A project honouring its layers contract passes with no issues.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        kept_contract_project: Staged project with a kept contract.
    """
    plugin = get_plugin("import-linter")
    result = plugin.check([kept_contract_project], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_without_contracts_is_clean(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """A Python project with no import-linter config reports a clean result.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Pytest fixture providing a temporary directory.
    """
    (tmp_path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")

    plugin = get_plugin("import-linter")
    result = plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
