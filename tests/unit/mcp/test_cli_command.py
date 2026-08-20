"""Unit tests for the ``lintro mcp`` CLI command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import click
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.cli_utils.commands.mcp import mcp_command


def test_mcp_command_is_registered_on_the_cli() -> None:
    """``mcp`` is exposed as a top-level lintro command.

    Subcommands load lazily, so ``cli.commands`` is empty until one is
    resolved; go through the public listing/lookup API instead.
    """
    ctx = click.Context(cli)

    assert_that(cli.list_commands(ctx)).contains("mcp")
    assert_that(cli.get_command(ctx, "mcp")).is_same_as(mcp_command)


def test_mcp_command_defaults_workspace_to_cwd(tmp_path: Path) -> None:
    """Without ``--workspace`` the server is rooted at the process cwd."""
    runner = CliRunner()
    with (
        patch("lintro.mcp.server.run_stdio_server") as mock_run,
        runner.isolated_filesystem(temp_dir=tmp_path) as workdir,
    ):
        result = runner.invoke(mcp_command, [])

    assert_that(result.exit_code).is_equal_to(0)
    mock_run.assert_called_once_with(workspace=Path(workdir).resolve())


def test_mcp_command_uses_explicit_workspace(tmp_path: Path) -> None:
    """``--workspace`` overrides the cwd and is resolved to an absolute path."""
    runner = CliRunner()
    with patch("lintro.mcp.server.run_stdio_server") as mock_run:
        result = runner.invoke(mcp_command, ["--workspace", str(tmp_path)])

    assert_that(result.exit_code).is_equal_to(0)
    mock_run.assert_called_once_with(workspace=tmp_path.resolve())


def test_mcp_command_rejects_missing_workspace(tmp_path: Path) -> None:
    """A non-existent workspace is rejected by Click before startup."""
    runner = CliRunner()
    result = runner.invoke(mcp_command, ["--workspace", str(tmp_path / "nope")])

    assert_that(result.exit_code).is_not_equal_to(0)


def test_mcp_command_reports_missing_extra() -> None:
    """Without the optional SDK the command fails with install guidance."""
    runner = CliRunner()
    with patch("lintro.mcp.is_mcp_available", return_value=False):
        result = runner.invoke(mcp_command, [])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("lintro[mcp]")
