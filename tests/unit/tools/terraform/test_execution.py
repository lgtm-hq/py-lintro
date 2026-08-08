"""Tests for TerraformPlugin check and fix execution (mocked subprocess)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

from assertpy import assert_that

from lintro.parsers.terraform.terraform_issue import TerraformIssue
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.terraform import TerraformPlugin

_VALIDATE_JSON = json.dumps(
    {
        "valid": False,
        "error_count": 1,
        "diagnostics": [
            {
                "severity": "error",
                "summary": "Reference to undeclared local value",
                "detail": 'A local value with the name "x" has not been declared.',
                "range": {
                    "filename": "main.tf",
                    "start": {"line": 7, "column": 11},
                    "end": {"line": 7, "column": 31},
                },
            },
        ],
    },
)


def _write_tf(tmp_path: Path, name: str = "main.tf") -> Path:
    """Write a minimal Terraform file into a temp directory.

    Args:
        tmp_path: Temporary directory to write into.
        name: File name to create.

    Returns:
        Path to the created file.
    """
    tf_file = tmp_path / name
    tf_file.write_text('output "x" {\n  value = 1\n}\n')
    return tf_file


def test_check_clean_fmt_only(
    terraform_plugin: TerraformPlugin,
    tmp_path: Path,
) -> None:
    """Check succeeds when fmt reports no issues and validate is disabled.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    tf_file = _write_tf(tmp_path)
    terraform_plugin.set_options(validate=False)

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(terraform_plugin, "_run_subprocess", return_value=(True, "")),
    ):
        result = terraform_plugin.check([str(tf_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_reports_fmt_violation(
    terraform_plugin: TerraformPlugin,
    tmp_path: Path,
) -> None:
    """Check reports a formatting issue from fmt stdout.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    tf_file = _write_tf(tmp_path)
    terraform_plugin.set_options(validate=False)

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            terraform_plugin,
            "_run_subprocess",
            return_value=(False, "main.tf\n"),
        ),
    ):
        result = terraform_plugin.check([str(tf_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    fmt_issue = cast(TerraformIssue, result.issues[0])  # type: ignore[index]
    assert_that(fmt_issue.code).is_equal_to("fmt")


def test_check_reports_validate_diagnostics(
    terraform_plugin: TerraformPlugin,
    tmp_path: Path,
) -> None:
    """Check surfaces validate diagnostics after a clean fmt run.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    tf_file = _write_tf(tmp_path)

    validate_result = SubprocessResult(
        returncode=1,
        stdout=_VALIDATE_JSON,
        stderr="",
        output=_VALIDATE_JSON,
    )

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            terraform_plugin,
            "_run_subprocess",
            side_effect=[(True, ""), (True, "")],  # fmt check, then init
        ),
        patch.object(
            terraform_plugin,
            "_run_subprocess_result",
            return_value=validate_result,
        ),
    ):
        result = terraform_plugin.check([str(tf_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    validate_issue = cast(TerraformIssue, result.issues[0])  # type: ignore[index]
    assert_that(validate_issue.code).is_equal_to("validate")
    assert_that(validate_issue.line).is_equal_to(7)
    assert_that(validate_issue.message).contains("undeclared local value")


def test_check_init_failure_reported(
    terraform_plugin: TerraformPlugin,
    tmp_path: Path,
) -> None:
    """A failing terraform init is reported as an init issue.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    tf_file = _write_tf(tmp_path)

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            terraform_plugin,
            "_run_subprocess",
            side_effect=[(True, ""), (False, "Error: provider download failed")],
        ),
    ):
        result = terraform_plugin.check([str(tf_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    init_issue = cast(TerraformIssue, result.issues[0])  # type: ignore[index]
    assert_that(init_issue.code).is_equal_to("init")


def test_check_no_terraform_files(
    terraform_plugin: TerraformPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no Terraform files are found.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    other = tmp_path / "notes.txt"
    other.write_text("not terraform")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = terraform_plugin.check([str(other)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_fix_formats_files(
    terraform_plugin: TerraformPlugin,
    tmp_path: Path,
) -> None:
    """Fix formats files and reports the fixed count.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    tf_file = _write_tf(tmp_path)
    terraform_plugin.set_options(validate=False)

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            terraform_plugin,
            "_run_subprocess",
            side_effect=[
                (False, "main.tf\n"),  # initial fmt check
                (True, ""),  # fmt apply
                (True, ""),  # final fmt check
            ],
        ),
    ):
        result = terraform_plugin.fix([str(tf_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)


def test_fix_validate_issue_persists(
    terraform_plugin: TerraformPlugin,
    tmp_path: Path,
) -> None:
    """Validate diagnostics persist across fix and are not counted as fixed.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    tf_file = _write_tf(tmp_path)

    validate_result = SubprocessResult(
        returncode=1,
        stdout=_VALIDATE_JSON,
        stderr="",
        output=_VALIDATE_JSON,
    )

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            terraform_plugin,
            "_run_subprocess",
            side_effect=[
                (False, "main.tf\n"),  # initial fmt check
                (True, ""),  # validate init
                (True, ""),  # fmt apply
                (True, ""),  # final fmt check
            ],
        ),
        patch.object(
            terraform_plugin,
            "_run_subprocess_result",
            return_value=validate_result,
        ),
    ):
        result = terraform_plugin.fix([str(tf_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(1)
    remaining_issue = cast(TerraformIssue, result.issues[0])  # type: ignore[index]
    assert_that(remaining_issue.code).is_equal_to("validate")


def test_fix_no_terraform_files(
    terraform_plugin: TerraformPlugin,
    tmp_path: Path,
) -> None:
    """Fix returns success when no Terraform files are found.

    Args:
        terraform_plugin: The TerraformPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    other = tmp_path / "notes.txt"
    other.write_text("not terraform")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = terraform_plugin.fix([str(other)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No")
