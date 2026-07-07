"""Tests for the `lintro config validate` and `config init` subcommands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a CLI runner for testing.

    Returns:
        CliRunner: A Click test runner instance.
    """
    return CliRunner()


def test_validate_valid_config_exits_zero(cli_runner: CliRunner) -> None:
    """A valid config should validate and exit 0.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        Path(".lintro-config.yaml").write_text(
            "tools:\n  ruff:\n    enabled: true\n",
            encoding="utf-8",
        )

        result = cli_runner.invoke(cli, ["config", "validate"])

        assert_that(result.exit_code).is_equal_to(0)
        assert_that(result.output).contains("VALID")


def test_validate_invalid_config_exits_nonzero(cli_runner: CliRunner) -> None:
    """An invalid config should exit non-zero with an error report.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        Path(".lintro-config.yaml").write_text(
            'execution:\n  max_fix_retries: "bad"\n',
            encoding="utf-8",
        )

        result = cli_runner.invoke(cli, ["config", "validate"])

        assert_that(result.exit_code).is_equal_to(1)
        assert_that(result.output).contains("INVALID")


def test_validate_json_output(cli_runner: CliRunner) -> None:
    """JSON output should be machine-readable with expected fields.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        Path(".lintro-config.yaml").write_text(
            "tools:\n  ruft:\n    enabled: true\n",
            encoding="utf-8",
        )

        result = cli_runner.invoke(cli, ["config", "validate", "--json"])

        assert_that(result.exit_code).is_equal_to(0)
        data = json.loads(result.output)
        assert_that(data).contains("valid")
        assert_that(data).contains("warnings")
        assert_that(data["warnings"][0]["suggestion"]).is_equal_to("ruff")


def test_validate_explicit_path(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The --path option should validate the specified file.

    Args:
        cli_runner: Click test runner instance.
        tmp_path: Temporary directory path.
    """
    config = tmp_path / "custom.yaml"
    config.write_text("tools:\n  ruff:\n    enabled: true\n", encoding="utf-8")

    result = cli_runner.invoke(cli, ["config", "validate", "--path", str(config)])

    assert_that(result.exit_code).is_equal_to(0)


def test_validate_missing_config_errors(cli_runner: CliRunner) -> None:
    """Validation with no config present should error and exit non-zero.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(cli, ["config", "validate"])

        assert_that(result.exit_code).is_equal_to(1)
        assert_that(result.output).contains("lintro init")


def test_config_init_subcommand_scaffolds(cli_runner: CliRunner) -> None:
    """`config init` should scaffold a config like the top-level init.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(cli, ["config", "init", "--minimal", "--static"])

        assert_that(result.exit_code).is_equal_to(0)
        assert_that(Path(".lintro-config.yaml").exists()).is_true()


def test_config_show_subcommand(cli_runner: CliRunner) -> None:
    """`config show --json` should emit the effective config report.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(cli, ["config", "show", "--json"])

        assert_that(result.exit_code).is_equal_to(0)
        data = json.loads(result.output)
        assert_that(data).contains("global_settings")
