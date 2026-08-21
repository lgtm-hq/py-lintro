"""Unit tests for markdownlint plugin check execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.markdownlint import MarkdownlintPlugin

# Tests for MarkdownlintPlugin.check method


def test_check_clean_file(
    markdownlint_plugin: MarkdownlintPlugin,
    tmp_path: Path,
) -> None:
    """Check reports success when no issues found.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    md_file = tmp_path / "README.md"
    md_file.write_text("# Title\n\nBody text.\n")

    with patch.object(
        markdownlint_plugin,
        "_run_subprocess",
        return_value=(True, ""),
    ):
        result = markdownlint_plugin.check([str(md_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_issues(
    markdownlint_plugin: MarkdownlintPlugin,
    tmp_path: Path,
) -> None:
    """Check parses issues reported by markdownlint.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    md_file = tmp_path / "README.md"
    md_file.write_text("#  Title  #\n")

    output = (
        f"{md_file}:1:1 MD021/no-multiple-space-closed-atx Multiple spaces "
        'inside hashes on closed atx style heading [Context: "#  Title  #"]'
    )

    with patch.object(
        markdownlint_plugin,
        "_run_subprocess",
        return_value=(False, output),
    ):
        result = markdownlint_plugin.check([str(md_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)


def test_fix_raises_not_implemented(
    markdownlint_plugin: MarkdownlintPlugin,
) -> None:
    """Fix raises NotImplementedError.

    Args:
        markdownlint_plugin: The MarkdownlintPlugin instance to test.
    """
    with pytest.raises(NotImplementedError, match="Markdownlint cannot fix"):
        markdownlint_plugin.fix([], {})
