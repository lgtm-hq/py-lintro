"""Tests for PytestPlugin check method."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from assertpy import assert_that

from lintro.enums.pytest_enums import PytestSpecialMode
from lintro.parsers.pytest.pytest_issue import PytestIssue

if TYPE_CHECKING:
    from lintro.tools.definitions.pytest import PytestPlugin


# =============================================================================
# Tests for PytestPlugin check method with mocked subprocess
# =============================================================================


def test_check_success_with_mocked_subprocess(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """Check succeeds with mocked subprocess returning success.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    with (
        patch.object(
            sample_pytest_plugin,
            "_verify_tool_version",
            return_value=None,
        ),
        patch.object(
            sample_pytest_plugin,
            "_run_subprocess",
            return_value=(True, "10 passed in 0.12s"),
        ),
        patch.object(sample_pytest_plugin, "_parse_output", return_value=[]),
        patch.object(
            sample_pytest_plugin.executor,
            "prepare_test_execution",
            return_value=10,
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "execute_tests",
        ) as mock_execute,
    ):
        mock_execute.return_value = (True, "10 passed in 0.12s", 0)

        result = sample_pytest_plugin.check(["tests"], {})

        assert_that(result.success).is_true()
        assert_that(result.name).is_equal_to("pytest")


def test_check_failure_with_mocked_subprocess(
    sample_pytest_plugin: PytestPlugin,
    sample_pytest_issues: list[PytestIssue],
) -> None:
    """Check fails with mocked subprocess returning failure.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
        sample_pytest_issues: List of sample PytestIssue objects.
    """
    failed_issues = [
        i for i in sample_pytest_issues if i.test_status in ("FAILED", "ERROR")
    ]

    with (
        patch.object(
            sample_pytest_plugin,
            "_verify_tool_version",
            return_value=None,
        ),
        patch.object(
            sample_pytest_plugin,
            "_run_subprocess",
            return_value=(False, "2 failed, 8 passed in 0.15s"),
        ),
        patch.object(
            sample_pytest_plugin,
            "_parse_output",
            return_value=failed_issues,
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "prepare_test_execution",
            return_value=10,
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "execute_tests",
        ) as mock_execute,
    ):
        mock_execute.return_value = (False, "2 failed, 8 passed in 0.15s", 1)

        result = sample_pytest_plugin.check(["tests"], {})

        assert_that(result.success).is_false()
        assert_that(result.issues_count).is_greater_than(0)


def test_check_handles_executor_not_initialized(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """Check handles case when executor is None.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    sample_pytest_plugin.executor = None

    with patch.object(
        sample_pytest_plugin,
        "_verify_tool_version",
        return_value=None,
    ):
        result = sample_pytest_plugin.check(["tests"], {})

        assert_that(result.success).is_false()
        assert_that(result.output).contains("not initialized")


def test_check_handles_result_processor_not_initialized(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """Check handles case when result_processor is None.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    sample_pytest_plugin.result_processor = None

    with (
        patch.object(
            sample_pytest_plugin,
            "_verify_tool_version",
            return_value=None,
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "prepare_test_execution",
            return_value=10,
        ),
        patch.object(
            sample_pytest_plugin.executor,
            "execute_tests",
            return_value=(True, "10 passed", 0),
        ),
        patch.object(sample_pytest_plugin, "_parse_output", return_value=[]),
    ):
        result = sample_pytest_plugin.check(["tests"], {})

        assert_that(result.success).is_false()
        assert_that(result.output).contains("not initialized")


# =============================================================================
# Tests for pytest test collection mode
# =============================================================================


def test_collect_only_mode_enabled(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """Collect only mode is enabled correctly.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    sample_pytest_plugin.set_options(collect_only=True)
    assert_that(sample_pytest_plugin.pytest_config.collect_only).is_true()
    assert_that(sample_pytest_plugin.pytest_config.is_special_mode()).is_true()


def test_collect_only_returns_special_mode(
    sample_pytest_plugin: PytestPlugin,
) -> None:
    """Collect only returns correct special mode name.

    Args:
        sample_pytest_plugin: The PytestPlugin instance to test.
    """
    sample_pytest_plugin.set_options(collect_only=True)
    mode = sample_pytest_plugin.pytest_config.get_special_mode()
    assert_that(mode).is_equal_to(PytestSpecialMode.COLLECT_ONLY.value)
