"""html-validate runs without a complete verdict are execution failures (#3352).

html-validate 11.21 prints a JSON array to stdout on every completed run:
``[]`` when clean (exit 0) and one record per file with findings (exit 1).
A configuration it cannot load prints nothing to stdout and exits 1.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.html_validate.definition import HtmlValidatePlugin

FINDING = (
    '{"filePath":"index.html","messages":[{"ruleId":"wcag/h37","severity":2,'
    '"message":"<img> is missing required \\"alt\\" attribute",'
    '"line":5,"column":2}]}'
)


def _check(
    plugin: HtmlValidatePlugin,
    tmp_path: Path,
    *,
    returncode: int,
    stdout: str,
    stderr: str = "",
) -> ToolResult:
    """Run ``check`` on one HTML file with a canned html-validate run.

    Args:
        plugin: The plugin under test.
        tmp_path: Working directory for the context.
        returncode: html-validate's exit status.
        stdout: The JSON report stream.
        stderr: The diagnostics stream.

    Returns:
        The check result.
    """
    ctx = MagicMock(should_skip=False, early_result=None, timeout=30)
    ctx.cwd = str(tmp_path)
    ctx.files = ctx.rel_files = ["index.html"]
    run = SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output=stdout + stderr,
    )
    with (
        patch.object(plugin, "_prepare_execution", return_value=ctx),
        patch.object(
            plugin,
            "_get_executable_command",
            return_value=["/pinned/html-validate"],
        ),
        patch.object(plugin, "_run_subprocess_result", return_value=run),
    ):
        return plugin.check([str(tmp_path)], {})


def test_malformed_exit_zero_report_does_not_claim_inspection(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """Exit 0 with a malformed record is incomplete, not an inspected pass."""
    result = _check(
        html_validate_plugin,
        tmp_path,
        returncode=0,
        stdout='[{"filePath":"index.html","messages":"oops"}]',
    )

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.output).contains("could not be fully parsed").does_not_contain(
        "oops",
    )


def test_empty_exit_zero_stdout_is_no_report(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """A clean run prints ``[]``, so empty stdout at exit 0 is no report."""
    result = _check(html_validate_plugin, tmp_path, returncode=0, stdout="")

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.output).is_equal_to("html-validate printed no report.")


def test_partly_malformed_report_keeps_real_findings(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """Findings survive a malformed sibling record; coverage stays unknown."""
    result = _check(
        html_validate_plugin,
        tmp_path,
        returncode=1,
        stdout=f'[{FINDING}, "not a record"]',
    )

    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.issues).extracting("code").is_equal_to(["wcag/h37"])


def test_config_load_failure_names_the_tool_message(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """A config error quotes stderr's first lines, not a parse-failure count."""
    result = _check(
        html_validate_plugin,
        tmp_path,
        returncode=1,
        stdout="",
        stderr='UserError: Failed to load configuration from "nope:none"\n',
    )

    assert_that(result.execution_failed).is_true()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.output).is_equal_to(
        "html-validate exited with an error and printed no report.\n"
        'UserError: Failed to load configuration from "nope:none"',
    )


def test_clean_empty_array_is_an_inspected_pass(
    html_validate_plugin: HtmlValidatePlugin,
    tmp_path: Path,
) -> None:
    """The ``[]`` html-validate prints when clean still proves inspection."""
    result = _check(html_validate_plugin, tmp_path, returncode=0, stdout="[]")

    assert_that(result.success).is_true()
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.parse_failures_count).is_zero()
