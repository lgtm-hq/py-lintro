"""CLI wiring tests for the ``--diff`` option on check and format."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - subprocess drives git in controlled test fixtures
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.utils.git_diff import DIFF_DEFAULT_SENTINEL


@pytest.mark.parametrize("command", ["chk", "fmt"])
def test_diff_option_in_help(command: str) -> None:
    """The ``--diff`` option is documented in check and format help.

    Args:
        command: CLI subcommand alias under test.
    """
    runner = CliRunner()
    result = runner.invoke(cli, [command, "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("--diff")


def test_diff_flag_without_value_passes_sentinel() -> None:
    """``chk --diff`` forwards the default sentinel to the runner."""
    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.check.run_lint_tools_simple",
        return_value=0,
    ) as mock_run:
        runner.invoke(cli, ["chk", "--diff", "--tools", "ruff"])

    assert_that(mock_run.call_args.kwargs["diff_base"]).is_equal_to(
        DIFF_DEFAULT_SENTINEL,
    )


def test_diff_flag_with_explicit_base_passes_ref() -> None:
    """``chk --diff main`` forwards the explicit base ref."""
    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.check.run_lint_tools_simple",
        return_value=0,
    ) as mock_run:
        runner.invoke(cli, ["chk", "--diff", "main", "--tools", "ruff"])

    assert_that(mock_run.call_args.kwargs["diff_base"]).is_equal_to("main")


def test_no_diff_flag_passes_none() -> None:
    """Omitting ``--diff`` forwards ``None`` (full scan)."""
    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.check.run_lint_tools_simple",
        return_value=0,
    ) as mock_run:
        runner.invoke(cli, ["chk", "--tools", "ruff"])

    assert_that(mock_run.call_args.kwargs["diff_base"]).is_none()


def test_format_diff_flag_passes_sentinel() -> None:
    """``fmt --diff`` forwards the default sentinel to the runner."""
    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.format.run_lint_tools_simple",
        return_value=0,
    ) as mock_run:
        runner.invoke(cli, ["fmt", "--diff", "--tools", "ruff"])

    assert_that(mock_run.call_args.kwargs["diff_base"]).is_equal_to(
        DIFF_DEFAULT_SENTINEL,
    )


def test_diff_equals_syntax_passes_ref() -> None:
    """``chk --diff=main`` forwards the explicit base ref."""
    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.check.run_lint_tools_simple",
        return_value=0,
    ) as mock_run:
        result = runner.invoke(cli, ["chk", "--diff=main", "--tools", "ruff"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.call_args.kwargs["diff_base"]).is_equal_to("main")


def test_diff_rejects_existing_path_as_base(tmp_path: Path) -> None:
    """``chk --diff <path>`` errors instead of consuming the scan path."""
    scan_dir = tmp_path / "src"
    scan_dir.mkdir()
    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.check.run_lint_tools_simple",
        return_value=0,
    ) as mock_run:
        result = runner.invoke(
            cli,
            ["chk", "--diff", str(scan_dir), "--tools", "ruff"],
        )

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("looks like a filesystem path")
    assert_that(result.output).contains("--diff=<ref>")
    assert_that(mock_run.called).is_false()


def test_diff_with_separator_treats_path_as_scan_target(tmp_path: Path) -> None:
    """``chk --diff -- <path>`` keeps the path as a scan target."""
    scan_dir = tmp_path / "src"
    scan_dir.mkdir()
    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.check.run_lint_tools_simple",
        return_value=0,
    ) as mock_run:
        result = runner.invoke(
            cli,
            ["chk", "--diff", "--tools", "ruff", "--", str(scan_dir)],
        )

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.call_args.kwargs["diff_base"]).is_equal_to(
        DIFF_DEFAULT_SENTINEL,
    )
    assert_that(mock_run.call_args.kwargs["paths"]).contains(str(scan_dir))


def test_diff_equals_syntax_allows_ref_when_path_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--diff=main`` works when a ``main/`` directory also exists."""
    subprocess.run(  # nosec B603 B607 - fixed git argv in test repo setup; shell=False
        ["git", "init", "-q"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(  # nosec B603 B607 - fixed git argv in test repo setup; shell=False
        ["git", "commit", "--allow-empty", "-qm", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )
    subprocess.run(  # nosec B603 B607 - fixed git argv in test repo setup; shell=False
        ["git", "branch", "-M", "main"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / "main").mkdir()
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    with patch(
        "lintro.cli_utils.commands.check.run_lint_tools_simple",
        return_value=0,
    ) as mock_run:
        result = runner.invoke(cli, ["chk", "--diff=main", "--tools", "ruff"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(mock_run.call_args.kwargs["diff_base"]).is_equal_to("main")
