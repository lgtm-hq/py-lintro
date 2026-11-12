"""Vale runs without a complete verdict are execution failures (#3352).

The payloads below are what Vale 3.26 prints with ``--output=JSON``: a mapping
of alerts (exit 1), ``{}`` when clean (exit 0), and a single runtime-error
object (exit 2) when a configured style is missing.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.tools.vale.definition import ValePlugin
from tests.unit.tools.vale.conftest import make_ctx

ALERT: dict[str, object] = {
    "Span": [6, 10],
    "Check": "Vale.Repetition",
    "Message": "'is' is repeated!",
    "Severity": "error",
    "Match": "is is",
    "Line": 3,
}
MISSING_STYLE = {
    "Line": 3,
    "Path": "/repo/.vale.ini",
    "Text": "Style 'Nope' isn't on the StylesPath (/repo/styles).",
    "Code": "E201",
    "Span": 17,
}


def _check(
    plugin: ValePlugin,
    tmp_path: Path,
    *,
    run_success: bool,
    output: str,
) -> ToolResult:
    """Run ``check`` against one Markdown file with a canned Vale run.

    Args:
        plugin: The plugin under test.
        tmp_path: Working directory for the context.
        run_success: Whether Vale exited 0.
        output: Combined Vale output.

    Returns:
        The check result.
    """
    with (
        patch.object(plugin, "_prepare_execution") as prepare,
        patch.object(plugin, "_run_subprocess", return_value=(run_success, output)),
    ):
        prepare.return_value = make_ctx(str(tmp_path), ["a.md"])
        return plugin.check(["a.md"], {})


def test_runtime_error_object_is_one_named_execution_failure(
    vale_plugin: ValePlugin,
    tmp_path: Path,
) -> None:
    """A missing style is one failure, named, with no raw report dumped."""
    result = _check(
        vale_plugin,
        tmp_path,
        run_success=False,
        output=json.dumps(MISSING_STYLE, indent=2),
    )

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.issues_count).is_zero()
    assert_that(result.output).is_equal_to(
        "Vale could not run: E201: Style 'Nope' isn't on the StylesPath "
        "(/repo/styles).",
    )


def test_empty_exit_zero_output_is_not_a_clean_inspected_pass(
    vale_plugin: ValePlugin,
    tmp_path: Path,
) -> None:
    """Vale prints ``{}`` when clean, so no output at all is no report."""
    result = _check(vale_plugin, tmp_path, run_success=True, output="")

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.output).is_equal_to("Vale printed no report.")


def test_malformed_report_keeps_real_alerts_without_claiming_inspection(
    vale_plugin: ValePlugin,
    tmp_path: Path,
) -> None:
    """A malformed file entry fails the run but keeps the alerts Vale gave."""
    output = json.dumps({"a.md": [ALERT], "b.md": "not a list"})

    result = _check(vale_plugin, tmp_path, run_success=False, output=output)

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.inspected_files).is_none()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.issues).extracting("file", "line", "check").is_equal_to(
        [("a.md", 3, "Vale.Repetition")],
    )
    assert_that(result.output).contains("could not be fully parsed").does_not_contain(
        "not a list",
    )


def test_text_crash_is_an_execution_error_not_a_parse_failure(
    vale_plugin: ValePlugin,
    tmp_path: Path,
) -> None:
    """Non-JSON text behind a failed exit is not counted as a bad report."""
    result = _check(
        vale_plugin,
        tmp_path,
        run_success=False,
        output="flag provided but not defined: -bogus\nUsage of vale:",
    )

    assert_that(result.execution_failed).is_true()
    assert_that(result.parse_failures_count).is_none()
    assert_that(result.output).is_equal_to(
        "Vale exited with an error and reported no findings: "
        "flag provided but not defined: -bogus | Usage of vale:",
    )


def test_complete_reports_still_claim_inspection(
    vale_plugin: ValePlugin,
    tmp_path: Path,
) -> None:
    """A clean ``{}`` and a report of alerts are complete verdicts."""
    clean = _check(vale_plugin, tmp_path, run_success=True, output="{}")
    findings = _check(
        vale_plugin,
        tmp_path,
        run_success=False,
        output=json.dumps({"a.md": [ALERT]}),
    )

    assert_that(clean.success).is_true()
    assert_that(clean.inspected_files).is_true()
    assert_that(findings.success).is_false()
    assert_that(findings.execution_failed).is_false()
    assert_that(findings.inspected_files).is_true()
    assert_that(findings.issues_count).is_equal_to(1)
