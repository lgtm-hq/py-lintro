"""Unit tests for console logger output and summaries."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.utils.console import ThreadSafeConsoleLogger, create_logger


def test_create_logger_and_basic_methods(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise basic logging methods and ensure files are created.

    Args:
        tmp_path: Temporary directory for artifacts.
        capsys: Pytest capture fixture for stdout.
    """
    logger = create_logger(run_dir=tmp_path, verbose=True, raw_output=False)
    assert_that(isinstance(logger, ThreadSafeConsoleLogger)).is_true()
    logger.info("info message")
    logger.debug("debug message")
    logger.warning("warn message")
    logger.error("error message")
    logger.print_lintro_header()
    logger.print_tool_header(tool_name="ruff", action="check")
    logger.print_tool_result(tool_name="ruff", output="", issues_count=0)
    raw = (
        "\n    [*] 2 fixable\n"
        "    Formatting issues:\n"
        "    Would reformat: file1.py\n"
        "    Would reformat: file2.py\n"
        "    Found 3 issue(s) that cannot be auto-fixed\n"
        "    "
    ).strip()
    logger.print_tool_result(
        tool_name="ruff",
        output="some formatted table",
        issues_count=3,
        raw_output_for_meta=raw,
        action=Action.CHECK,
    )

    logger.print_execution_summary(
        action=Action.CHECK,
        tool_results=[ToolResult(name="ruff", issues_count=1, success=False)],
    )

    logger.print_execution_summary(
        action=Action.FIX,
        tool_results=[
            ToolResult(
                name="ruff",
                fixed_issues_count=2,
                remaining_issues_count=0,
                success=True,
            ),
        ],
    )
    logger.save_console_log()
    assert_that((tmp_path / "console.log").exists()).is_true()
    out = capsys.readouterr().out
    assert_that(out).contains("LINTRO")
    assert_that(out).contains("Running ruff (check)")
    # Output should contain either issue-related text or pass/fail status
    assert_that(
        "issues" in out.lower() or "PASS" in out or "FAIL" in out,
    ).is_true()


def test_summary_marks_fail_on_tool_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ensure summary marks FAIL when any tool result indicates failure.

    Args:
        tmp_path: Temporary directory for artifacts.
        capsys: Pytest capture fixture for stdout.
    """
    logger = create_logger(run_dir=tmp_path, verbose=False, raw_output=False)

    logger.print_execution_summary(
        action=Action.CHECK,
        tool_results=[
            ToolResult(name="bandit", success=False, output="Failed to parse"),
        ],
    )
    out = capsys.readouterr().out
    assert_that(out).contains("FAIL")
    assert_that(out.split("FAIL")[0]).does_not_contain("PASS")
