"""Tests for the ``check`` command health-score CLI wiring."""

from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.check import check_command


@pytest.fixture
def runner() -> CliRunner:
    """Provide a Click CLI runner.

    Returns:
        CliRunner: A fresh runner instance.
    """
    return CliRunner()


def test_help_lists_score_options(runner: CliRunner) -> None:
    """The check command exposes --score and --fail-under."""
    result = runner.invoke(check_command, ["--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("--score")
    assert_that(result.output).contains("--fail-under")


def test_score_flag_forwarded(runner: CliRunner) -> None:
    """--score is forwarded to the executor and drives the exit code."""
    with patch(
        "lintro.cli_utils.commands.check.run_lint_tools_simple",
        return_value=0,
    ) as mock_run:
        result = runner.invoke(check_command, [".", "--score"])

    assert_that(result.exit_code).is_equal_to(0)
    _, kwargs = mock_run.call_args
    assert_that(kwargs["score"]).is_true()
    assert_that(kwargs["fail_under"]).is_none()


def test_fail_under_forwarded(runner: CliRunner) -> None:
    """--fail-under is parsed as a float and forwarded to the executor."""
    with patch(
        "lintro.cli_utils.commands.check.run_lint_tools_simple",
        return_value=1,
    ) as mock_run:
        result = runner.invoke(check_command, [".", "--fail-under", "75"])

    assert_that(result.exit_code).is_equal_to(1)
    _, kwargs = mock_run.call_args
    assert_that(kwargs["fail_under"]).is_equal_to(75.0)


def test_fail_under_rejects_out_of_range(runner: CliRunner) -> None:
    """--fail-under enforces the 0-100 range."""
    result = runner.invoke(check_command, [".", "--fail-under", "150"])

    assert_that(result.exit_code).is_not_equal_to(0)
