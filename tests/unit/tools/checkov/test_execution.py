"""Unit tests for checkov plugin execution."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - only TimeoutExpired is constructed here
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_name import ToolName
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.checkov.checkov_issue import CheckovIssue
from lintro.parsers.checkov.checkov_parser import (
    CHECKOV_PARSE_ERROR_CODE,
    parse_checkov_output,
)
from lintro.plugins.base import ExecutionContext
from lintro.tools.checkov.definition import CheckovPlugin, extract_checkov_json

_FAILED_CHECK: dict[str, Any] = {
    "check_id": "CKV_AWS_260",
    "check_name": "Ensure no security groups allow ingress from 0.0.0.0:0 to port 80",
    "check_result": {"result": "FAILED"},
    "file_path": "/main.tf",
    "file_abs_path": "/repo/main.tf",
    "file_line_range": [10, 19],
    "resource": "aws_security_group.allow_all",
    "severity": None,
    "guideline": None,
}


def _report(failed: list[dict[str, Any]]) -> str:
    """Wrap failed-check records in a single-framework checkov report.

    Args:
        failed: Failed-check records to embed.

    Returns:
        JSON string mimicking ``checkov --output json``.
    """
    return json.dumps(
        {
            "check_type": "terraform",
            "results": {
                "passed_checks": [],
                "failed_checks": failed,
                "skipped_checks": [],
                "parsing_errors": [],
            },
            "summary": {"passed": 0, "failed": len(failed), "skipped": 0},
        },
    )


GUIDELINE_URL = "https://docs.paloaltonetworks.com/checkov/CKV_AWS_1"

ISSUE_JSON = _report([_FAILED_CHECK])
CLEAN_JSON = _report([])


def _ctx(tmp_path: Path, file_path: Path) -> ExecutionContext:
    """Build a prepared execution context.

    Args:
        tmp_path: Temporary directory path.
        file_path: Terraform file included in the context.

    Returns:
        An ExecutionContext naming the single Terraform file.
    """
    return ExecutionContext(
        files=[str(file_path)],
        rel_files=[file_path.name],
        cwd=str(tmp_path),
        timeout=120,
    )


def test_check_with_issues(checkov_plugin: CheckovPlugin, tmp_path: Path) -> None:
    """Check returns issues parsed from the JSON report.

    Args:
        checkov_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "main.tf"
    source.write_text('resource "aws_s3_bucket" "b" {}\n')

    with (
        patch.object(checkov_plugin, "prepare") as mock_prepare,
        patch.object(
            checkov_plugin,
            "_run_subprocess",
            return_value=(False, ISSUE_JSON),
        ),
    ):
        mock_prepare.return_value = _ctx(tmp_path, source)
        result = checkov_plugin.check([str(source)], {})

    assert_that(result.name).is_equal_to(ToolName.CHECKOV)
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    issues = [i for i in (result.issues or []) if isinstance(i, CheckovIssue)]
    assert_that(issues).is_not_empty()
    issue = issues[0]
    assert_that(issue.check_id).is_equal_to("CKV_AWS_260")
    assert_that(issue.resource).is_equal_to("aws_security_group.allow_all")
    assert_that(issue.line).is_equal_to(10)


def test_check_attaches_doc_urls_and_prefers_a_native_guideline(
    checkov_plugin: CheckovPlugin,
    tmp_path: Path,
) -> None:
    """Findings carry a doc URL; a native ``guideline`` outranks the fallback.

    ``CheckovIssue.__post_init__`` only propagates a guideline into
    ``doc_url``; nothing else assigned the plugin's static policy-index URL, so
    every finding rendered without a documentation link. The fallback must be
    applied without clobbering the more specific guideline when one is present.

    Args:
        checkov_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "main.tf"
    source.write_text('resource "aws_s3_bucket" "b" {}\n')
    guided = {**_FAILED_CHECK, "check_id": "CKV_AWS_1", "guideline": GUIDELINE_URL}
    report = _report([_FAILED_CHECK, guided])

    with (
        patch.object(checkov_plugin, "prepare") as mock_prepare,
        patch.object(checkov_plugin, "_run_subprocess", return_value=(False, report)),
    ):
        mock_prepare.return_value = _ctx(tmp_path, source)
        result = checkov_plugin.check([str(source)], {})

    urls = {
        issue.check_id: issue.doc_url
        for issue in (result.issues or [])
        if isinstance(issue, CheckovIssue)
    }
    assert_that(urls["CKV_AWS_260"]).is_equal_to(DocUrlTemplate.CHECKOV)
    assert_that(urls["CKV_AWS_1"]).is_equal_to(GUIDELINE_URL)


def test_check_fails_on_a_zero_exit_that_still_reports_failed_checks(
    checkov_plugin: CheckovPlugin,
    tmp_path: Path,
) -> None:
    """Soft-fail output — exit 0 with findings — is reported, never a pass.

    ``soft-fail: true`` in a native ``.checkov.yaml`` suppresses the non-zero
    exit code but not the JSON report, and the plugin honours those configs.
    The verdict must therefore come from the report content, not the exit
    status: a clean exit alongside a populated ``failed_checks`` has to
    surface the findings and fail the tool result.

    Args:
        checkov_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "main.tf"
    source.write_text('resource "aws_s3_bucket" "b" {}\n')

    with (
        patch.object(checkov_plugin, "prepare") as mock_prepare,
        patch.object(
            checkov_plugin,
            "_run_subprocess",
            return_value=(True, ISSUE_JSON),
        ),
    ):
        mock_prepare.return_value = _ctx(tmp_path, source)
        result = checkov_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    # The finding survives, rather than the run being failed on the exit code
    # alone with an empty report.
    codes = {
        issue.check_id
        for issue in (result.issues or [])
        if isinstance(issue, CheckovIssue)
    }
    assert_that(codes).is_equal_to({"CKV_AWS_260"})
    # Not the no-report fail-closed branch: that one sets parse_failures_count.
    assert_that(result.parse_failures_count).is_none()


def test_check_clean(checkov_plugin: CheckovPlugin, tmp_path: Path) -> None:
    """Check returns success with no issues for a clean report.

    Args:
        checkov_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "main.tf"
    source.write_text('output "noop" {\n  value = "ok"\n}\n')

    with (
        patch.object(checkov_plugin, "prepare") as mock_prepare,
        patch.object(
            checkov_plugin,
            "_run_subprocess",
            return_value=(True, CLEAN_JSON),
        ),
    ):
        mock_prepare.return_value = _ctx(tmp_path, source)
        result = checkov_plugin.check([str(source)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_execution_failure_fails_closed(
    checkov_plugin: CheckovPlugin,
    tmp_path: Path,
) -> None:
    """A non-zero exit with no parseable report fails closed.

    A security scanner that reported a pass because it could not be run at all
    would be worse than useless, so the raw output is surfaced instead.

    Args:
        checkov_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "main.tf"
    source.write_text("resource {\n")

    with (
        patch.object(checkov_plugin, "prepare") as mock_prepare,
        patch.object(
            checkov_plugin,
            "_run_subprocess",
            return_value=(False, "checkov: error: unrecognized arguments"),
        ),
    ):
        mock_prepare.return_value = _ctx(tmp_path, source)
        result = checkov_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("unrecognized arguments")


def test_check_timeout_returns_failure(
    checkov_plugin: CheckovPlugin,
    tmp_path: Path,
) -> None:
    """A subprocess timeout produces a failed ToolResult.

    Args:
        checkov_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "main.tf"
    source.write_text('output "noop" {\n  value = "ok"\n}\n')

    with (
        patch.object(checkov_plugin, "prepare") as mock_prepare,
        patch.object(
            checkov_plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd="checkov", timeout=120),
        ),
    ):
        mock_prepare.return_value = _ctx(tmp_path, source)
        result = checkov_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    # The prose alone is not the contract: a regression that kept the message
    # but stopped setting the machine-readable flag would still be a timeout
    # reported as an ordinary failure.
    assert_that(result.timed_out).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("timed out")


def test_check_skips_when_no_files(checkov_plugin: CheckovPlugin) -> None:
    """Check returns the early result when preparation says to skip.

    Args:
        checkov_plugin: The plugin under test.
    """
    early = ToolResult(
        name="checkov",
        success=True,
        output="No files to check.",
        issues_count=0,
    )
    with patch.object(checkov_plugin, "prepare", return_value=early):
        result = checkov_plugin.check(["."], {})

    assert_that(result).is_same_as(early)


def test_fix_raises_not_implemented(checkov_plugin: CheckovPlugin) -> None:
    """Checkov does not support fixing.

    Args:
        checkov_plugin: The plugin under test.
    """
    assert_that(checkov_plugin.fix).raises(NotImplementedError).when_called_with(
        ["main.tf"],
        {},
    )


def test_check_passes_configured_options_into_the_subprocess_argv(
    checkov_plugin: CheckovPlugin,
    tmp_path: Path,
) -> None:
    """Options set through ``check()`` reach the real checkov argv.

    The other execution tests assert command construction through the private
    ``_build_command`` helper, which cannot catch a ``check()`` that builds a
    command and then fails to pass it on. This spies the argv the subprocess
    layer actually receives, with only the version probe stubbed so the test is
    hermetic on a runner with no checkov installed.

    Args:
        checkov_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "main.tf"
    source.write_text('output "noop" {\n  value = "ok"\n}\n')
    seen: list[list[str]] = []

    def _spy(cmd: list[str], **kwargs: object) -> tuple[bool, str]:
        seen.append(cmd)
        return True, CLEAN_JSON

    checkov_plugin.set_options(skip_checks=["CKV_AWS_18"])
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(checkov_plugin, "_run_subprocess", side_effect=_spy),
    ):
        result = checkov_plugin.check([str(source)], {})

    assert_that(result.success).is_true()
    assert_that(seen).is_length(1)
    assert_that(seen[0]).contains("--output", "json", "--skip-download")
    assert_that(seen[0]).contains("--skip-results-upload")
    joined = " ".join(seen[0])
    assert_that(joined).contains("--framework terraform,terraform_json")
    assert_that(joined).contains("--skip-check CKV_AWS_18")
    assert_that(seen[0]).does_not_contain("--bc-api-key")
    assert_that(seen[0][-1]).ends_with("main.tf")


@pytest.mark.parametrize(
    "template",
    [
        "WARNING unable to resolve var\n{payload}\ndone\n",
        "[WARNING] unresolved ${{var}} in main.tf\n{payload}\n",
        "{payload}\n[WARNING] 1 module skipped [terraform]\n",
        "[INFO] start\n{payload}\n[INFO] done [ok]\n",
    ],
    ids=["plain-prose", "bracketed-before", "bracketed-after", "bracketed-both"],
)
def test_extract_checkov_json_strips_surrounding_log_lines(template: str) -> None:
    """The JSON report survives stderr warnings merged into the output.

    The batch runner hands the parser stdout and stderr combined, and checkov
    logs to stderr — unresolved variables print as ``${var}`` and log lines are
    bracketed. Slicing from the first opener to the last closer would swallow a
    trailing ``[tag]`` and yield nothing parseable, which with a zero exit reads
    as a clean security pass.

    Args:
        template: Output shape wrapping the payload.
    """
    noisy = template.format(payload=CLEAN_JSON)

    assert_that(extract_checkov_json(noisy)).is_equal_to(CLEAN_JSON)


def test_extract_checkov_json_handles_multi_framework_list_reports() -> None:
    """A top-level array survives the slice, with or without stderr noise.

    Checkov emits a list when several frameworks run against the same paths, so
    an extractor that only looked for ``{`` would drop the whole report.
    """
    payload = json.dumps([json.loads(ISSUE_JSON), json.loads(CLEAN_JSON)])

    assert_that(extract_checkov_json(payload)).is_equal_to(payload)
    assert_that(
        extract_checkov_json(f"WARNING unable to resolve var\n{payload}\ndone\n"),
    ).is_equal_to(payload)
    assert_that(parse_checkov_output(extract_checkov_json(payload))).is_length(1)


def test_extract_checkov_json_returns_none_without_a_payload() -> None:
    """Output carrying no JSON at all yields None rather than raising."""
    assert_that(extract_checkov_json("")).is_none()
    assert_that(extract_checkov_json("checkov: command failed")).is_none()
    assert_that(extract_checkov_json("{not json at all")).is_none()
    assert_that(extract_checkov_json("[WARNING] skipped ${var} [tf]")).is_none()


def test_check_fails_on_a_zero_exit_that_only_reports_parsing_errors(
    checkov_plugin: CheckovPlugin,
    tmp_path: Path,
) -> None:
    """An unparseable Terraform file fails the run rather than passing it.

    Checkov writes a perfectly valid JSON report in this case and exits 0, so
    the no-report fail-closed branch never fires; the only signal is
    ``results.parsing_errors``. Without it lintro would report a clean security
    scan for a file no policy was evaluated against.

    Args:
        checkov_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "main.tf"
    source.write_text("resource {\n")
    report = json.dumps(
        {
            "check_type": "terraform",
            "results": {"failed_checks": [], "parsing_errors": [str(source)]},
        },
    )

    with (
        patch.object(checkov_plugin, "prepare") as mock_prepare,
        patch.object(checkov_plugin, "_run_subprocess", return_value=(True, report)),
    ):
        mock_prepare.return_value = _ctx(tmp_path, source)
        result = checkov_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    codes = {str(getattr(issue, "check_id", "")) for issue in (result.issues or [])}
    assert_that(codes).is_equal_to({CHECKOV_PARSE_ERROR_CODE})


def test_check_fails_closed_when_a_zero_exit_carries_no_report(
    checkov_plugin: CheckovPlugin,
    tmp_path: Path,
) -> None:
    """Exit zero with no JSON report is a failure, not a clean scan.

    ``--output json`` always writes a report for a run that reached the policy
    engine, so its absence means checkov never scanned the files. A soft-fail
    config, or checkov dying before it wrote the report, would otherwise be
    indistinguishable from a genuinely clean result and reported as a pass.

    Args:
        checkov_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "main.tf"
    source.write_text('output "noop" {\n  value = "ok"\n}\n')

    with (
        patch.object(checkov_plugin, "prepare") as mock_prepare,
        patch.object(
            checkov_plugin,
            "_run_subprocess",
            return_value=(True, "[WARNING] could not load ${var} [terraform]"),
        ),
    ):
        mock_prepare.return_value = _ctx(tmp_path, source)
        result = checkov_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.output).contains("WARNING")


def test_check_reports_findings_hidden_behind_trailing_log_lines(
    checkov_plugin: CheckovPlugin,
    tmp_path: Path,
) -> None:
    """Findings survive a bracketed log line printed after the report.

    Args:
        checkov_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    source = tmp_path / "main.tf"
    source.write_text('resource "aws_s3_bucket" "b" {}\n')
    noisy = f"{ISSUE_JSON}\n[WARNING] 1 module skipped [terraform]\n"

    with (
        patch.object(checkov_plugin, "prepare") as mock_prepare,
        patch.object(checkov_plugin, "_run_subprocess", return_value=(False, noisy)),
    ):
        mock_prepare.return_value = _ctx(tmp_path, source)
        result = checkov_plugin.check([str(source)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
