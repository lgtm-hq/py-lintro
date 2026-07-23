"""Unit tests for the html-validate plugin execution methods."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.parsers.html_validate.html_validate_issue import HtmlValidateIssue
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.html_validate import HtmlValidatePlugin

_ISSUE_JSON = (
    '[{"filePath":"index.html","messages":['
    '{"ruleId":"wcag/h37","severity":2,'
    '"message":"<img> is missing required \\"alt\\" attribute",'
    '"line":5,"column":2,"selector":"html > body > img",'
    '"ruleUrl":"https://html-validate.org/rules/wcag/h37.html"}],'
    '"errorCount":1,"warningCount":0}]'
)


def _mock_ctx(tmp_path: Path, files: list[str]) -> MagicMock:
    """Build a mock ExecutionContext.

    Args:
        tmp_path: Working directory for the mock context.
        files: File list to expose on the context.

    Returns:
        A configured MagicMock standing in for an ExecutionContext.
    """
    ctx = MagicMock()
    ctx.should_skip = False
    ctx.early_result = None
    ctx.timeout = 30
    ctx.cwd = str(tmp_path)
    ctx.files = files
    ctx.rel_files = files
    return ctx


def test_check_with_issues(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """Check returns structured issues parsed from JSON stdout.

    Args:
        html_validate_plugin: The plugin under test.
        tmp_path: Temporary directory for the fixture file.
    """
    html_file = tmp_path / "index.html"
    html_file.write_text("<img src='a.png'>\n")

    mock_result = SubprocessResult(
        returncode=1,
        stdout=_ISSUE_JSON,
        stderr="",
        output=_ISSUE_JSON,
    )

    with (
        patch.object(html_validate_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            html_validate_plugin,
            "_run_subprocess_result",
            return_value=mock_result,
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path, [str(html_file)])
        result = html_validate_plugin.check([str(html_file)], {})

    assert_that(result.name).is_equal_to("html_validate")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    issue = cast(HtmlValidateIssue, result.issues[0])  # type: ignore[index]
    assert_that(issue.code).is_equal_to("wcag/h37")
    assert_that(issue.severity).is_equal_to("error")
    assert_that(issue.line).is_equal_to(5)


def test_check_clean_suppresses_output(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """A clean run reports success and suppresses output.

    Args:
        html_validate_plugin: The plugin under test.
        tmp_path: Temporary directory for the fixture file.
    """
    html_file = tmp_path / "clean.html"
    html_file.write_text("<p>ok</p>\n")

    mock_result = SubprocessResult(
        returncode=0,
        stdout="[]",
        stderr="",
        output="[]",
    )

    with (
        patch.object(html_validate_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            html_validate_plugin,
            "_run_subprocess_result",
            return_value=mock_result,
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path, [str(html_file)])
        result = html_validate_plugin.check([str(html_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_none()


def test_check_returns_early_when_skipped(
    html_validate_plugin: HtmlValidatePlugin,
) -> None:
    """Check returns the early result when preparation signals a skip.

    Args:
        html_validate_plugin: The plugin under test.
    """
    early = MagicMock()
    ctx = MagicMock()
    ctx.should_skip = True
    ctx.early_result = early

    with patch.object(
        html_validate_plugin,
        "_prepare_execution",
        return_value=ctx,
    ):
        result = html_validate_plugin.check(["x.html"], {})

    assert_that(result).is_same_as(early)


def test_fix_raises_not_implemented(
    html_validate_plugin: HtmlValidatePlugin,
) -> None:
    """html-validate is check-only; fix() must raise NotImplementedError.

    Args:
        html_validate_plugin: The plugin under test.
    """
    with pytest.raises(NotImplementedError):
        html_validate_plugin.fix(["x.html"], {})
