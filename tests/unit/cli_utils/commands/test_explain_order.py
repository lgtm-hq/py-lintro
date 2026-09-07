"""Tests for ``--explain-order`` on check and format (#1741).

The flag is reporting only: it prints the shadow diff and returns without
handing anything to the execution pipeline.
"""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.check import check_command
from lintro.cli_utils.commands.format import format_command


def test_check_explain_order_prints_diff_and_runs_nothing() -> None:
    """``lintro check --explain-order`` reports and exits without running."""
    runner = CliRunner()

    args = ["--tools", "ruff,black", "--explain-order"]

    with patch("lintro.cli_utils.commands.check.run_lint_with_ai") as mock_run:
        result = runner.invoke(check_command, args)

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.called).is_false()
    assert_that(result.output).contains("Execution order (shadow mode)")
    assert_that(result.output).contains("Current (scalar priority):")
    assert_that(result.output).contains("Derived (claims):")
    assert_that(result.output).contains("ruff should run before black")


def test_format_explain_order_prints_diff_and_runs_nothing() -> None:
    """``lintro fmt --explain-order`` reports and exits without formatting."""
    runner = CliRunner()

    with patch("lintro.cli_utils.commands.format.run_lint_with_ai") as mock_run:
        result = runner.invoke(
            format_command,
            ["--tools", "ruff,black", "--explain-order"],
        )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.called).is_false()
    assert_that(result.output).contains("Execution order (shadow mode)")
    assert_that(result.output).contains("*.py: ruff(fix) -> black(format)")


def test_check_explain_order_rejects_an_unknown_tool() -> None:
    """An unknown ``--tools`` value exits 1 instead of printing a diff."""
    runner = CliRunner()

    with patch("lintro.cli_utils.commands.check.run_lint_with_ai") as mock_run:
        result = runner.invoke(
            check_command,
            ["--tools", "definitely-not-a-tool", "--explain-order"],
        )

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(mock_run.called).is_false()


def test_check_without_explain_order_still_runs() -> None:
    """Omitting the flag leaves the normal pipeline untouched."""
    runner = CliRunner()

    with patch(
        "lintro.cli_utils.commands.check.run_lint_with_ai",
        return_value=0,
    ) as mock_run:
        result = runner.invoke(check_command, ["--tools", "ruff"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.called).is_true()
    assert_that(result.output).does_not_contain("Execution order (shadow mode)")


def test_check_explain_order_still_validates_the_diff_base() -> None:
    """``--diff`` validation runs before the explain-order early exit."""
    runner = CliRunner()
    args = ["--diff", ".", "--tools", "ruff", "--explain-order"]

    with patch("lintro.cli_utils.commands.check.run_lint_with_ai") as mock_run:
        result = runner.invoke(check_command, args)

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(mock_run.called).is_false()
    assert_that(result.output).does_not_contain("Execution order (shadow mode)")


def test_check_no_cache_still_clears_before_explain_order() -> None:
    """``--no-cache`` clears the caches even when the run only explains."""
    runner = CliRunner()
    args = ["--tools", "ruff", "--no-cache", "--explain-order"]

    with (
        patch("lintro.cli_utils.commands.check.run_lint_with_ai") as mock_run,
        patch("lintro.utils.file_cache.clear_all_caches") as mock_clear,
    ):
        result = runner.invoke(check_command, args)

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.called).is_false()
    assert_that(mock_clear.called).is_true()
    assert_that(result.output).contains("Execution order (shadow mode)")


def test_check_explain_order_uses_the_default_selection() -> None:
    """Without ``--tools`` the diff covers the detected default toolset."""
    runner = CliRunner()

    with patch("lintro.cli_utils.commands.check.run_lint_with_ai") as mock_run:
        result = runner.invoke(check_command, ["--explain-order"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.called).is_false()
    assert_that(result.output).contains("Execution order (shadow mode)")
    assert_that(result.output).contains("ruff")
