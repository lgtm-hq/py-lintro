"""Tests for the lintro config CLI command."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.config.lintro_config import (
    EnforceConfig,
    ExecutionConfig,
    LintroConfig,
    LintroToolConfig,
)


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a CLI runner for testing.

    Returns:
        CliRunner: A Click test runner instance.
    """
    return CliRunner()


@pytest.fixture
def mock_config() -> LintroConfig:
    """Create a mock LintroConfig for testing.

    Returns:
        LintroConfig: A configured LintroConfig instance.
    """
    return LintroConfig(
        execution=ExecutionConfig(
            enabled_tools=[],
            fail_fast=False,
        ),
        enforce=EnforceConfig(
            line_length=88,
            target_python="py313",
        ),
        tools={
            "ruff": LintroToolConfig(enabled=True),
            "black": LintroToolConfig(enabled=True),
        },
        config_path="/path/to/.lintro-config.yaml",
    )


# =============================================================================
# Tests for JSON output mode of config command
# =============================================================================


@patch("lintro.cli_utils.commands.config.get_config")
@patch("lintro.cli_utils.commands.config.validate_config_consistency")
@patch("lintro.cli_utils.commands.config.is_tool_injectable")
def test_json_output_is_valid_json(
    mock_injectable: MagicMock,
    mock_validate: MagicMock,
    mock_get_config: MagicMock,
    mock_config: LintroConfig,
    cli_runner: CliRunner,
) -> None:
    """JSON output is valid JSON.

    Args:
        mock_injectable: Mock for is_tool_injectable function.
        mock_validate: Mock for validate_config_consistency function.
        mock_get_config: Mock for get_config function.
        mock_config: Mock LintroConfig instance.
        cli_runner: Click test runner instance.
    """
    mock_get_config.return_value = mock_config
    mock_validate.return_value = []
    mock_injectable.return_value = True

    result = cli_runner.invoke(cli, ["config", "--json"])

    assert_that(result.exit_code).is_equal_to(0)
    # Should be valid JSON
    data = json.loads(result.output)
    assert_that(data).contains("global_settings")
    assert_that(data).contains("tool_configs")


@patch("lintro.cli_utils.commands.config.get_config")
@patch("lintro.cli_utils.commands.config.validate_config_consistency")
@patch("lintro.cli_utils.commands.config.is_tool_injectable")
def test_json_output_includes_line_length(
    mock_injectable: MagicMock,
    mock_validate: MagicMock,
    mock_get_config: MagicMock,
    mock_config: LintroConfig,
    cli_runner: CliRunner,
) -> None:
    """JSON output includes line length in global settings.

    Args:
        mock_injectable: Mock for is_tool_injectable function.
        mock_validate: Mock for validate_config_consistency function.
        mock_get_config: Mock for get_config function.
        mock_config: Mock LintroConfig instance.
        cli_runner: Click test runner instance.
    """
    mock_get_config.return_value = mock_config
    mock_validate.return_value = []
    mock_injectable.return_value = True

    result = cli_runner.invoke(cli, ["config", "--json"])

    data = json.loads(result.output)
    assert_that(data["global_settings"]["line_length"]).is_equal_to(88)


@patch("lintro.cli_utils.commands.config.get_config")
@patch("lintro.cli_utils.commands.config.validate_config_consistency")
@patch("lintro.cli_utils.commands.config.is_tool_injectable")
def test_json_output_includes_derived_tool_order(
    mock_injectable: MagicMock,
    mock_validate: MagicMock,
    mock_get_config: MagicMock,
    mock_config: LintroConfig,
    cli_runner: CliRunner,
) -> None:
    """JSON output includes the derived tool execution order.

    Args:
        mock_injectable: Mock for is_tool_injectable function.
        mock_validate: Mock for validate_config_consistency function.
        mock_get_config: Mock for get_config function.
        mock_config: Mock LintroConfig instance.
        cli_runner: Click test runner instance.
    """
    mock_get_config.return_value = mock_config
    mock_validate.return_value = []
    mock_injectable.return_value = True

    result = cli_runner.invoke(cli, ["config", "--json"])

    data = json.loads(result.output)
    assert_that(data).contains("tool_execution_order")
    tool_names = [t["tool"] for t in data["tool_execution_order"]]
    assert_that(tool_names).contains("black", "ruff")
    # The derived order runs ruff (FIX on *.py) before black (FORMAT).
    assert_that(tool_names.index("ruff")).is_less_than(tool_names.index("black"))
    assert_that(data["global_settings"]["tool_order"]).is_equal_to("derived")


@patch("lintro.cli_utils.commands.config.get_config")
@patch("lintro.cli_utils.commands.config.validate_config_consistency")
@patch("lintro.cli_utils.commands.config.is_tool_injectable")
def test_json_output_includes_warnings(
    mock_injectable: MagicMock,
    mock_validate: MagicMock,
    mock_get_config: MagicMock,
    mock_config: LintroConfig,
    cli_runner: CliRunner,
) -> None:
    """JSON output includes configuration warnings.

    Args:
        mock_injectable: Mock for is_tool_injectable function.
        mock_validate: Mock for validate_config_consistency function.
        mock_get_config: Mock for get_config function.
        mock_config: Mock LintroConfig instance.
        cli_runner: Click test runner instance.
    """
    mock_get_config.return_value = mock_config
    mock_validate.return_value = ["black: Native config differs"]
    mock_injectable.return_value = True

    result = cli_runner.invoke(cli, ["config", "--json"])

    data = json.loads(result.output)
    assert_that(data).contains("warnings")
    assert_that(len(data["warnings"])).is_greater_than(0)
    assert_that(data["warnings"][0]).contains("black")


@patch("lintro.cli_utils.commands.config.get_config")
@patch("lintro.cli_utils.commands.config.validate_config_consistency")
@patch("lintro.cli_utils.commands.config.is_tool_injectable")
def test_json_output_includes_config_source(
    mock_injectable: MagicMock,
    mock_validate: MagicMock,
    mock_get_config: MagicMock,
    mock_config: LintroConfig,
    cli_runner: CliRunner,
) -> None:
    """JSON output includes config_source field.

    Args:
        mock_injectable: Mock for is_tool_injectable function.
        mock_validate: Mock for validate_config_consistency function.
        mock_get_config: Mock for get_config function.
        mock_config: Mock LintroConfig instance.
        cli_runner: Click test runner instance.
    """
    mock_get_config.return_value = mock_config
    mock_validate.return_value = []
    mock_injectable.return_value = True

    result = cli_runner.invoke(cli, ["config", "--json"])

    data = json.loads(result.output)
    assert_that(data).contains("config_source")
    assert_that(data["config_source"]).contains(".lintro-config.yaml")


# =============================================================================
# Tests for verbose mode of config command
# =============================================================================


@patch("lintro.cli_utils.commands.config.get_config")
@patch("lintro.cli_utils.commands.config.validate_config_consistency")
@patch("lintro.cli_utils.commands.config.is_tool_injectable")
@patch("lintro.cli_utils.commands.config._load_native_tool_config")
def test_verbose_shows_native_config(
    mock_native_config: MagicMock,
    mock_injectable: MagicMock,
    mock_validate: MagicMock,
    mock_get_config: MagicMock,
    mock_config: LintroConfig,
    cli_runner: CliRunner,
) -> None:
    """Verbose mode shows native config column.

    Args:
        mock_native_config: Mock for _load_native_tool_config function.
        mock_injectable: Mock for is_tool_injectable function.
        mock_validate: Mock for validate_config_consistency function.
        mock_get_config: Mock for get_config function.
        mock_config: Mock LintroConfig instance.
        cli_runner: Click test runner instance.
    """
    mock_get_config.return_value = mock_config
    mock_validate.return_value = []
    mock_injectable.return_value = False
    mock_native_config.return_value = {"printWidth": 100}

    result = cli_runner.invoke(cli, ["config", "--verbose"])

    assert_that(result.exit_code).is_equal_to(0)
    # Verbose should show Native Config column
    assert_that(
        "Native" in result.output or "native" in result.output.lower(),
    ).is_true()
