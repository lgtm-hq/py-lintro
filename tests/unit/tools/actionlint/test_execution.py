"""Unit tests for actionlint plugin check execution."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.actionlint import ActionlintPlugin

# Tests for ActionlintPlugin.check method


def test_check_restricts_to_workflow_files(
    actionlint_plugin: ActionlintPlugin,
    tmp_path: Path,
) -> None:
    """Only files under .github/workflows are checked.

    Args:
        actionlint_plugin: The ActionlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    other_file = tmp_path / "notes.yml"
    other_file.write_text("key: value\n")

    result = actionlint_plugin.check([str(other_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No GitHub workflow files")


def test_check_with_clean_workflow(
    actionlint_plugin: ActionlintPlugin,
    tmp_path: Path,
) -> None:
    """Check reports success when actionlint finds no issues.

    Args:
        actionlint_plugin: The ActionlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    workflow_file = workflows_dir / "ci.yml"
    workflow_file.write_text("name: CI\non: push\njobs: {}\n")

    with patch.object(
        actionlint_plugin,
        "_run_subprocess",
        return_value=(True, ""),
    ):
        result = actionlint_plugin.check([str(workflow_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_issues(
    actionlint_plugin: ActionlintPlugin,
    tmp_path: Path,
) -> None:
    """Check parses issues reported by actionlint.

    Args:
        actionlint_plugin: The ActionlintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    workflow_file = workflows_dir / "ci.yml"
    workflow_file.write_text("name: CI\non: push\njobs: {}\n")

    rel_path = os.path.relpath(workflow_file, tmp_path)
    output = f'{rel_path}:3:1: unexpected key "jobs" [syntax-check]'

    with patch.object(
        actionlint_plugin,
        "_run_subprocess",
        return_value=(False, output),
    ):
        result = actionlint_plugin.check([str(workflow_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)


def test_fix_raises_not_implemented(actionlint_plugin: ActionlintPlugin) -> None:
    """Fix raises NotImplementedError.

    Args:
        actionlint_plugin: The ActionlintPlugin instance to test.
    """
    with pytest.raises(NotImplementedError, match="Actionlint cannot automatically"):
        actionlint_plugin.fix([], {})


def test_doc_url_returns_none_for_empty_code(
    actionlint_plugin: ActionlintPlugin,
) -> None:
    """doc_url returns None when no code is given.

    Args:
        actionlint_plugin: The ActionlintPlugin instance to test.
    """
    assert_that(actionlint_plugin.doc_url("")).is_none()
