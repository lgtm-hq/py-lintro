"""Integration tests for the Vale tool definition.

These tests require Vale to be installed and available in PATH. They exercise
the ValePlugin end-to-end against a self-contained ``.vale.ini`` fixture that
uses only Vale's built-in ``Vale`` style (no ``vale sync`` required).
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

# Skip all tests if vale is not installed.
pytestmark = pytest.mark.skipif(
    shutil.which("vale") is None,
    reason="vale not installed",
)

_VALE_INI = """\
MinAlertLevel = suggestion

[*.md]
BasedOnStyles = Vale
"""


def _write_configured_project(tmp_path: Path, markdown: str) -> str:
    """Write a self-contained Vale project and return the Markdown path.

    Args:
        tmp_path: Temporary directory path.
        markdown: Markdown content for the sample document.

    Returns:
        Path to the created Markdown file as a string.
    """
    (tmp_path / ".vale.ini").write_text(_VALE_INI)
    md = tmp_path / "doc.md"
    md.write_text(markdown)
    return str(md)


def test_definition_attributes(
    get_plugin: Callable[[str], BaseToolPlugin],
) -> None:
    """Verify ValePlugin definition exposes expected values.

    Args:
        get_plugin: Fixture factory to get plugin instances.
    """
    plugin = get_plugin("vale")

    assert_that(plugin.definition.name).is_equal_to("vale")
    assert_that(plugin.definition.can_fix).is_false()


def test_check_detects_issues(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Vale should detect prose issues in a configured project.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Temporary directory path.
    """
    md = _write_configured_project(
        tmp_path,
        "# Title\n\nThe the quick brown fox jumped.\n",
    )

    plugin = get_plugin("vale")
    result = plugin.check([md], {})

    assert_that(result).is_not_none()
    assert_that(result.name).is_equal_to("vale")
    assert_that(result.issues_count).is_greater_than(0)
    assert_that(result.success).is_false()


def test_check_clean_document(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Vale should pass on a clean document in a configured project.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Temporary directory path.
    """
    md = _write_configured_project(
        tmp_path,
        "# Title\n\nA short and clear sentence.\n",
    )

    plugin = get_plugin("vale")
    result = plugin.check([md], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_skips_without_config(
    get_plugin: Callable[[str], BaseToolPlugin],
    tmp_path: Path,
) -> None:
    """Vale should skip (non-error) when no configuration is present.

    Args:
        get_plugin: Fixture factory to get plugin instances.
        tmp_path: Temporary directory path.
    """
    md = tmp_path / "doc.md"
    md.write_text("# Title\n\nThe the repeated words here.\n")

    plugin = get_plugin("vale")
    result = plugin.check([str(md)], {})

    assert_that(result).is_not_none()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("Skipping vale")
