"""Unit tests for gitleaks plugin check method execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.gitleaks import GitleaksPlugin
from tests.test_samples_helpers import copy_sample


def _get_report_path(cmd: list[str]) -> str | None:
    """Extract the report path from a gitleaks command.

    Args:
        cmd: The command list.

    Returns:
        The report path if found, None otherwise.
    """
    try:
        idx = cmd.index("--report-path")
        return cmd[idx + 1]
    except (ValueError, IndexError):
        return None


def _mock_subprocess_factory(output: str) -> Any:
    """Create a mock subprocess function that writes output to the report file.

    Args:
        output: The JSON output to write to the report file.

    Returns:
        A callable that can be used as a side_effect for _run_subprocess.
    """

    def mock_run(cmd: list[str], **kwargs: Any) -> tuple[bool, str]:
        report_path = _get_report_path(cmd)
        if report_path:
            Path(report_path).write_text(output)
        return (True, "")

    return mock_run


def test_check_with_mocked_subprocess_success(
    gitleaks_plugin: GitleaksPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no secrets found.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "security",
        "gitleaks",
        "gitleaks_test_module.py",
        dest_name="test_module.py",
    )

    with patch.object(
        gitleaks_plugin,
        "_run_subprocess",
        side_effect=_mock_subprocess_factory("[]"),
    ):
        result = gitleaks_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_secrets_found(
    gitleaks_plugin: GitleaksPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when gitleaks finds secrets.

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "security",
        "gitleaks",
        "gitleaks_aws_key.py",
        dest_name="test_module.py",
    )

    gitleaks_output = """[
        {
            "File": "test_module.py",
            "StartLine": 1,
            "StartColumn": 11,
            "EndLine": 1,
            "EndColumn": 34,
            "RuleID": "aws-access-key-id",
            "Description": "AWS Access Key ID",
            "Secret": "REDACTED",
            "Match": "AKIAIOSFODNN7EXAMPLE",
            "Fingerprint": "test_module.py:aws-access-key-id:1",
            "Entropy": 3.5
        }
    ]"""

    with patch.object(
        gitleaks_plugin,
        "_run_subprocess",
        side_effect=_mock_subprocess_factory(gitleaks_output),
    ):
        result = gitleaks_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_not_none()
    assert_that(result.issues).is_length(1)


def test_check_empty_report_with_zero_exit_is_not_clean(
    gitleaks_plugin: GitleaksPlugin,
    tmp_path: Path,
) -> None:
    """An empty report with a zero exit must not report a clean scan (#1044).

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "security",
        "gitleaks",
        "gitleaks_module.py",
        dest_name="test_module.py",
    )

    with patch.object(
        gitleaks_plugin,
        "_run_subprocess",
        side_effect=_mock_subprocess_factory(""),
    ):
        result = gitleaks_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_check_garbage_report_with_zero_exit_is_not_clean(
    gitleaks_plugin: GitleaksPlugin,
    tmp_path: Path,
) -> None:
    """An unparseable report with a zero exit must not report a clean scan (#1044).

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "security",
        "gitleaks",
        "gitleaks_module.py",
        dest_name="test_module.py",
    )

    garbage = "}{ not valid json report"

    with patch.object(
        gitleaks_plugin,
        "_run_subprocess",
        side_effect=_mock_subprocess_factory(garbage),
    ):
        result = gitleaks_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_equal_to(1)


def test_check_non_array_report_with_zero_exit_is_not_clean(
    gitleaks_plugin: GitleaksPlugin,
    tmp_path: Path,
) -> None:
    """Valid JSON that is not an array must not report a clean scan (#1044).

    Args:
        gitleaks_plugin: The GitleaksPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "security",
        "gitleaks",
        "gitleaks_module.py",
        dest_name="test_module.py",
    )

    with patch.object(
        gitleaks_plugin,
        "_run_subprocess",
        side_effect=_mock_subprocess_factory('{"unexpected": "object"}'),
    ):
        result = gitleaks_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.parse_failures_count).is_equal_to(1)
