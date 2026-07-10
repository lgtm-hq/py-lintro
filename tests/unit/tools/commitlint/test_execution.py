"""Unit tests for the commitlint plugin definition and check execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_type import ToolType
from lintro.parsers.commitlint.commitlint_issue import CommitlintIssue
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.commitlint import CommitlintPlugin

_ERROR_REPORT = (
    "⧗   --- input ---\n"
    "bad commit message\n"
    "✖   subject may not be empty [subject-empty]\n"
    "✖   type may not be empty [type-empty]\n"
    "\n"
    "✖   found 2 problems, 0 warnings\n"
)

_WARNING_REPORT = (
    "⧗   --- input ---\n"
    "feat: ok subject\n"
    "\n"
    "this body line is definitely longer than twenty chars\n"
    "⚠   body's lines must not be longer than 20 characters "
    "[body-max-line-length]\n"
    "\n"
    "⚠   found 0 problems, 1 warnings\n"
)

_CONFIG_MISSING = (
    "⧗   --- input ---\n"
    "bad commit message\n"
    "✖   Please add rules to your `commitlint.config.js`\n"
)


def _result(returncode: int, stdout: str) -> SubprocessResult:
    """Build a SubprocessResult with the given return code and stdout.

    Args:
        returncode: Simulated process exit code.
        stdout: Simulated standard output.

    Returns:
        A SubprocessResult with empty stderr and combined output.
    """
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr="",
        output=stdout,
    )


def _paths(tmp_path: Path) -> list[str]:
    """Create a dummy file so shared execution finds a path to work in.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        A single-element list with the temp directory path.
    """
    (tmp_path / "placeholder.txt").write_text("x\n", encoding="utf-8")
    return [str(tmp_path)]


def test_definition_metadata() -> None:
    """The tool definition exposes the expected commitlint metadata."""
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        plugin = CommitlintPlugin()
    definition = plugin.definition
    assert_that(definition.name).is_equal_to("commitlint")
    assert_that(definition.can_fix).is_false()
    assert_that(bool(definition.tool_type & ToolType.LINTER)).is_true()
    assert_that(definition.file_patterns).is_equal_to(["*"])
    assert_that(definition.version_command).is_equal_to(["commitlint", "--version"])
    assert_that(definition.native_configs).contains("commitlint.config.js")


def test_check_errors_found(
    commitlint_plugin: CommitlintPlugin,
    tmp_path: Path,
) -> None:
    """A failing commit message produces error issues and marks failure."""
    with patch.object(
        commitlint_plugin,
        "_run_subprocess_result",
        return_value=_result(1, _ERROR_REPORT),
    ):
        result = commitlint_plugin.check(_paths(tmp_path), {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(2)
    issues = [i for i in (result.issues or []) if isinstance(i, CommitlintIssue)]
    assert_that([i.rule for i in issues]).is_equal_to(
        ["subject-empty", "type-empty"],
    )


def test_check_clean_commit(
    commitlint_plugin: CommitlintPlugin,
    tmp_path: Path,
) -> None:
    """A conventional commit yields success with no issues and no output."""
    with patch.object(
        commitlint_plugin,
        "_run_subprocess_result",
        return_value=_result(0, ""),
    ):
        result = commitlint_plugin.check(_paths(tmp_path), {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_none()


def test_check_warning_reported(
    commitlint_plugin: CommitlintPlugin,
    tmp_path: Path,
) -> None:
    """A warning-only report surfaces the warning as an issue."""
    with patch.object(
        commitlint_plugin,
        "_run_subprocess_result",
        return_value=_result(0, _WARNING_REPORT),
    ):
        result = commitlint_plugin.check(_paths(tmp_path), {})

    assert_that(result.issues_count).is_equal_to(1)
    issues = [i for i in (result.issues or []) if isinstance(i, CommitlintIssue)]
    assert_that(issues).is_length(1)
    assert_that(issues[0].rule).is_equal_to("body-max-line-length")
    assert_that(issues[0].level).is_equal_to("warning")


def test_check_config_missing_skips(
    commitlint_plugin: CommitlintPlugin,
    tmp_path: Path,
) -> None:
    """Missing commitlint config skips the tool as a non-error."""
    with patch.object(
        commitlint_plugin,
        "_run_subprocess_result",
        return_value=_result(9, _CONFIG_MISSING),
    ):
        result = commitlint_plugin.check(_paths(tmp_path), {})

    assert_that(result.success).is_true()
    assert_that(result.skipped).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.skip_reason).contains("no commitlint config")


def test_check_config_missing_via_message_text(
    commitlint_plugin: CommitlintPlugin,
    tmp_path: Path,
) -> None:
    """The 'Please add rules' message triggers a skip even without exit 9."""
    with patch.object(
        commitlint_plugin,
        "_run_subprocess_result",
        return_value=_result(1, _CONFIG_MISSING),
    ):
        result = commitlint_plugin.check(_paths(tmp_path), {})

    assert_that(result.skipped).is_true()


def test_doc_url() -> None:
    """doc_url returns the rules page for a code and None when empty."""
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        plugin = CommitlintPlugin()
    assert_that(plugin.doc_url("type-empty")).is_equal_to(
        str(DocUrlTemplate.COMMITLINT),
    )
    assert_that(plugin.doc_url("")).is_none()


def test_fix_raises_not_implemented(commitlint_plugin: CommitlintPlugin) -> None:
    """Commitlint cannot fix commit messages."""
    with pytest.raises(NotImplementedError):
        commitlint_plugin.fix(["."], {})


def test_check_phrase_in_commit_message_does_not_mask_violations(
    commitlint_plugin: CommitlintPlugin,
    tmp_path: Path,
) -> None:
    """A commit message containing 'Please add rules' is not a config skip."""
    report = (
        "⧗   --- input ---\n"
        "docs: Please add rules to the style guide\n"
        "✖   subject must not be sentence-case [subject-case]\n"
        "\n"
        "✖   found 1 problems, 0 warnings\n"
    )
    with patch.object(
        commitlint_plugin,
        "_run_subprocess_result",
        return_value=_result(1, report),
    ):
        result = commitlint_plugin.check(_paths(tmp_path), {})

    assert_that(result.skipped).is_false()
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
