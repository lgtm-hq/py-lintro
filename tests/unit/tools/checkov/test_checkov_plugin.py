"""Unit tests for the Checkov plugin (definition, command, and check flow)."""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

from assertpy import assert_that

from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_type import ToolType
from lintro.tools.definitions.checkov import CheckovPlugin

_VERIFY_VERSION = "lintro.plugins.execution_preparation.verify_tool_version"
_SUBPROCESS_RUN = "lintro.tools.definitions.checkov.subprocess.run"


def _report(failed: list[dict[str, Any]]) -> str:
    """Build a minimal single-framework Checkov JSON report.

    Args:
        failed: Failed-check records to embed.

    Returns:
        JSON string for stdout.
    """
    return json.dumps(
        {
            "check_type": "terraform",
            "results": {"passed_checks": [], "failed_checks": failed},
        },
    )


_FAILED = {
    "check_id": "CKV_AWS_260",
    "check_name": "Ensure no security groups allow ingress from 0.0.0.0:0 to port 80",
    "file_path": "/main.tf",
    "file_line_range": [1, 3],
    "resource": "aws_security_group.allow_all",
}


def test_definition_metadata(checkov_plugin: CheckovPlugin) -> None:
    """The definition advertises the expected security/IaC metadata."""
    definition = checkov_plugin.definition

    assert_that(definition.name).is_equal_to("checkov")
    assert_that(definition.can_fix).is_false()
    assert_that(definition.file_patterns).is_equal_to(["*.tf", "*.tf.json"])
    assert_that(bool(definition.tool_type & ToolType.SECURITY)).is_true()
    assert_that(bool(definition.tool_type & ToolType.INFRASTRUCTURE)).is_true()


def test_file_patterns_exclude_dockerfiles(checkov_plugin: CheckovPlugin) -> None:
    """Checkov must not claim Dockerfiles (hadolint owns them)."""
    patterns = checkov_plugin.definition.file_patterns

    assert_that(patterns).does_not_contain("Dockerfile")
    assert_that(patterns).does_not_contain("Dockerfile*")


def test_build_command_is_hermetic(checkov_plugin: CheckovPlugin) -> None:
    """The check command runs offline and never passes an API key."""
    cmd = checkov_plugin._build_check_command(["a.tf", "b.tf"])

    assert_that(cmd[:3]).is_equal_to(["checkov", "--output", "json"])
    assert_that(cmd).contains("--skip-download")
    assert_that(cmd).contains("--download-external-modules", "False")
    assert_that(cmd).contains("--compact")
    assert_that(" ".join(cmd)).does_not_contain("--bc-api-key")
    # Each file gets its own -f flag: checkov's --file appends one path per
    # occurrence, so a shared flag would drop every file after the first.
    assert_that(cmd[-4:]).is_equal_to(["-f", "a.tf", "-f", "b.tf"])


def test_build_command_includes_skip_and_check_filters() -> None:
    """skip_checks / checks options are threaded into the command."""
    plugin = CheckovPlugin()
    plugin.set_options(skip_checks=["CKV_AWS_18"], checks=["CKV_AWS_260"])

    cmd = plugin._build_check_command(["a.tf"])
    assert_that(cmd).contains("--skip-check", "CKV_AWS_18")
    assert_that(cmd).contains("--check", "CKV_AWS_260")


def test_doc_url_returns_policy_index(checkov_plugin: CheckovPlugin) -> None:
    """doc_url falls back to the Checkov policy index for any code."""
    assert_that(checkov_plugin.doc_url("CKV_AWS_260")).is_equal_to(
        DocUrlTemplate.CHECKOV.value,
    )
    assert_that(checkov_plugin.doc_url("")).is_none()


def _run_check(plugin: CheckovPlugin, tf_file: Path, completed: CompletedProcess[str]):
    """Run plugin.check with version verification and subprocess mocked.

    Args:
        plugin: The plugin under test.
        tf_file: A real ``.tf`` path so file discovery keeps it.
        completed: The mocked subprocess result.

    Returns:
        The ToolResult from the mocked run.
    """
    with (
        patch(_VERIFY_VERSION, return_value=None),
        patch(_SUBPROCESS_RUN, return_value=completed),
    ):
        return plugin.check([str(tf_file)], {})


def test_check_reports_failed_checks(
    checkov_plugin: CheckovPlugin,
    tmp_path: Path,
) -> None:
    """Failed checks are surfaced as issues and mark the run unsuccessful."""
    tf_file = tmp_path / "main.tf"
    tf_file.write_text('resource "aws_s3_bucket" "b" {}\n')

    completed = CompletedProcess(
        args=["checkov"],
        returncode=1,
        stdout=_report([_FAILED]),
        stderr="",
    )

    result = _run_check(checkov_plugin, tf_file, completed)
    assert_that(result.name).is_equal_to("checkov")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues[0].check_id).is_equal_to("CKV_AWS_260")


def test_check_clean_run_succeeds(
    checkov_plugin: CheckovPlugin,
    tmp_path: Path,
) -> None:
    """A report with no failed checks is a successful run."""
    tf_file = tmp_path / "main.tf"
    tf_file.write_text('resource "aws_s3_bucket" "b" {}\n')

    completed = CompletedProcess(
        args=["checkov"],
        returncode=0,
        stdout=_report([]),
        stderr="",
    )

    result = _run_check(checkov_plugin, tf_file, completed)
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_empty_stdout_nonzero_exit_fails_closed(
    checkov_plugin: CheckovPlugin,
    tmp_path: Path,
) -> None:
    """No stdout with a non-zero exit is a failure, not a clean pass."""
    tf_file = tmp_path / "main.tf"
    tf_file.write_text('resource "aws_s3_bucket" "b" {}\n')

    completed = CompletedProcess(
        args=["checkov"],
        returncode=2,
        stdout="",
        stderr="boom",
    )

    result = _run_check(checkov_plugin, tf_file, completed)
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_unparseable_stdout_fails_closed(
    checkov_plugin: CheckovPlugin,
    tmp_path: Path,
) -> None:
    """Unparseable stdout must not report a clean pass (security tool)."""
    tf_file = tmp_path / "main.tf"
    tf_file.write_text('resource "aws_s3_bucket" "b" {}\n')

    completed = CompletedProcess(
        args=["checkov"],
        returncode=1,
        stdout="not json at all",
        stderr="",
    )

    result = _run_check(checkov_plugin, tf_file, completed)
    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_fix_raises_not_implemented(checkov_plugin: CheckovPlugin) -> None:
    """Checkov does not support autofix."""
    assert_that(checkov_plugin.fix).raises(NotImplementedError).when_called_with(
        ["a.tf"],
        {},
    )
