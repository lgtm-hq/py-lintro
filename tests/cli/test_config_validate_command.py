"""Tests for the `lintro config validate` and `config init` subcommands."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.config.config_loader import clear_config_cache
from lintro.enums.validation_code import ValidationCode


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a CLI runner for testing.

    Returns:
        CliRunner: A Click test runner instance.
    """
    return CliRunner()


@pytest.fixture(autouse=True)
def _reset_config_cache() -> Iterator[None]:
    """Clear the config cache around every test in this module.

    Yields:
        None: Control returns to the test body.
    """
    clear_config_cache()
    yield
    clear_config_cache()


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
        assert_that(result.output).contains("Configuration is VALID")
        assert_that(result.output).does_not_contain("INVALID")


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
        assert_that(result.output).contains("Configuration is INVALID")
        assert_that(result.output).does_not_contain("Configuration is VALID")


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
        assert_that(data["valid"]).is_true()
        assert_that(data["config_path"]).ends_with(".lintro-config.yaml")
        assert_that(data["errors"]).is_empty()
        assert_that(data["warnings"]).is_length(1)
        warning = data["warnings"][0]
        assert_that(warning["code"]).is_equal_to(ValidationCode.UNKNOWN_TOOL.value)
        assert_that(warning["location"]).is_equal_to("tools")
        assert_that(warning["suggestion"]).is_equal_to("ruff")


def test_validate_group_json_flag_is_forwarded(cli_runner: CliRunner) -> None:
    """``config --json validate`` should emit JSON, not Rich output.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        Path(".lintro-config.yaml").write_text(
            "tools:\n  ruff:\n    enabled: true\n",
            encoding="utf-8",
        )

        result = cli_runner.invoke(cli, ["config", "--json", "validate"])

        assert_that(result.exit_code).is_equal_to(0)
        data = json.loads(result.output)
        assert_that(data["valid"]).is_true()


def test_validate_subcommand_flag_wins_over_group(cli_runner: CliRunner) -> None:
    """An explicit subcommand flag should still take effect.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        Path("custom.yaml").write_text(
            "tools:\n  ruff:\n    enabled: true\n",
            encoding="utf-8",
        )

        result = cli_runner.invoke(
            cli,
            ["config", "--json", "validate", "--path", "custom.yaml"],
        )

        assert_that(result.exit_code).is_equal_to(0)
        data = json.loads(result.output)
        assert_that(data["config_path"]).is_equal_to("custom.yaml")


def test_validate_explicit_path(cli_runner: CliRunner, tmp_path: Path) -> None:
    """The --path option should validate the specified file.

    Args:
        cli_runner: Click test runner instance.
        tmp_path: Temporary directory path.
    """
    config = tmp_path / "custom.yaml"
    config.write_text("tools:\n  ruff:\n    enabled: true\n", encoding="utf-8")

    # Run from an isolated cwd with no config of its own: if --path were
    # ignored, auto-detect would have nothing to fall back on and the
    # assertions below could not pass by accident.
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(
            cli,
            ["config", "validate", "--path", str(config), "--json"],
        )

        assert_that(result.exit_code).is_equal_to(0)
        data = json.loads(result.output)
        assert_that(data["config_path"]).is_equal_to(str(config))
        assert_that(data["valid"]).is_true()
        assert_that(data["errors"]).is_empty()

        rich_result = cli_runner.invoke(
            cli,
            ["config", "validate", "--path", str(config)],
        )

        assert_that(rich_result.exit_code).is_equal_to(0)
        assert_that(rich_result.output).contains("custom.yaml")
        assert_that(rich_result.output).contains("Configuration is VALID")
        assert_that(rich_result.output).does_not_contain("INVALID")


def test_validate_invalid_json_reports_error_code(cli_runner: CliRunner) -> None:
    """Errors in JSON output should carry a stable machine-readable code.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        Path(".lintro-config.yaml").write_text(
            'execution:\n  max_fix_retries: "bad"\n',
            encoding="utf-8",
        )

        result = cli_runner.invoke(cli, ["config", "validate", "--json"])

        assert_that(result.exit_code).is_equal_to(1)
        data = json.loads(result.output)
        assert_that(data["valid"]).is_false()
        assert_that(data["errors"][0]["code"]).is_equal_to(
            ValidationCode.INVALID_TYPE.value,
        )


def test_validate_missing_explicit_path_is_not_found(
    cli_runner: CliRunner,
) -> None:
    """``validate --path`` on a missing file is NOT_FOUND, not auto-detect.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        Path(".lintro-config.yaml").write_text(
            "tools:\n  ruff:\n    enabled: true\n",
            encoding="utf-8",
        )

        result = cli_runner.invoke(
            cli,
            ["config", "validate", "--path", "nope.yaml", "--json"],
        )

        assert_that(result.exit_code).is_equal_to(1)
        data = json.loads(result.output)
        assert_that(data["valid"]).is_false()
        assert_that(data["errors"][0]["code"]).is_equal_to(
            ValidationCode.NOT_FOUND.value,
        )


def test_unmatched_group_export_on_validate_errors(
    cli_runner: CliRunner,
) -> None:
    """``config --export`` must not be silently dropped on validate.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        Path(".lintro-config.yaml").write_text(
            "tools:\n  ruff:\n    enabled: true\n",
            encoding="utf-8",
        )

        result = cli_runner.invoke(
            cli,
            ["config", "--export", "out.yaml", "validate"],
        )

        assert_that(result.exit_code).is_not_equal_to(0)
        assert_that(result.output).contains("--export")
        assert_that(Path("out.yaml").exists()).is_false()


def test_format_null_tool_entry_exits_one(cli_runner: CliRunner) -> None:
    """``format`` must exit 1 on a null ``tools.<name>:`` entry.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        Path(".lintro-config.yaml").write_text(
            "tools:\n  ruff:\n",
            encoding="utf-8",
        )
        Path("sample.py").write_text("x = 1\n", encoding="utf-8")

        result = cli_runner.invoke(cli, ["format", "sample.py"])

        assert_that(result.exit_code).is_equal_to(1)
        assert_that(result.output).contains(
            "tools.ruff must be a mapping or boolean",
        )
        assert_that(result.output).does_not_contain("Traceback")


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
    """`config init` should scaffold the same minimal YAML as top-level init.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(cli, ["config", "init", "--minimal", "--static"])

        assert_that(result.exit_code).is_equal_to(0)
        config_path = Path(".lintro-config.yaml")
        assert_that(config_path.exists()).is_true()
        content = config_path.read_text(encoding="utf-8")
        assert_that(content).contains("# Lintro Configuration (Minimal)")
        assert_that(content).contains("enforce:")
        assert_that(content).contains("line_length: 88")
        assert_that(content).contains("tools:")
        assert_that(content).contains("ruff:")
        assert_that(content).contains("black:")
        assert_that(content).contains("mypy:")

        top_level = cli_runner.invoke(cli, ["init", "--minimal", "--static", "--force"])
        assert_that(top_level.exit_code).is_equal_to(0)
        assert_that(config_path.read_text(encoding="utf-8")).is_equal_to(content)


def test_config_show_subcommand(cli_runner: CliRunner) -> None:
    """`config show --json` should emit the effective config report.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        Path(".lintro-config.yaml").write_text(
            "enforce:\n  line_length: 100\nexecution:\n  tool_order: alphabetical\n",
            encoding="utf-8",
        )

        result = cli_runner.invoke(cli, ["config", "show", "--json"])

        assert_that(result.exit_code).is_equal_to(0)
        data = json.loads(result.output)
        assert_that(data).contains(
            "config_source",
            "global_settings",
            "execution",
            "tool_execution_order",
            "tool_configs",
            "warnings",
        )
        assert_that(data["config_source"]).ends_with(".lintro-config.yaml")
        settings = data["global_settings"]
        assert_that(settings).contains(
            "line_length",
            "target_python",
            "tool_order",
            "custom_order",
        )
        assert_that(settings["line_length"]).is_equal_to(100)
        assert_that(settings["tool_order"]).is_equal_to("alphabetical")
        assert_that(data["tool_execution_order"]).is_not_empty()
        assert_that(data["tool_execution_order"][0]).contains("tool", "priority")


def test_config_show_null_tool_entry_exits_one(cli_runner: CliRunner) -> None:
    """``config show`` must exit 1 on a null ``tools.<name>:`` entry.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        Path(".lintro-config.yaml").write_text(
            "tools:\n  ruff:\n",
            encoding="utf-8",
        )

        result = cli_runner.invoke(cli, ["config", "show"])

        assert_that(result.exit_code).is_equal_to(1)
        assert_that(result.output).contains(
            "tools.ruff must be a mapping or boolean",
        )
        assert_that(result.output).does_not_contain("Traceback")


def test_check_null_tool_entry_exits_one(cli_runner: CliRunner) -> None:
    """``check`` must exit 1 on a null ``tools.<name>:`` entry.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        Path(".lintro-config.yaml").write_text(
            "tools:\n  ruff:\n",
            encoding="utf-8",
        )
        Path("sample.py").write_text("x = 1\n", encoding="utf-8")

        result = cli_runner.invoke(cli, ["check", "sample.py"])

        assert_that(result.exit_code).is_equal_to(1)
        assert_that(result.output).contains(
            "tools.ruff must be a mapping or boolean",
        )
        assert_that(result.output).does_not_contain("Traceback")


def test_config_group_json_flag_forwards_to_show(cli_runner: CliRunner) -> None:
    """``config --json show`` should emit the same JSON report as ``show --json``.

    Args:
        cli_runner: Click test runner instance.
    """
    with cli_runner.isolated_filesystem():
        result = cli_runner.invoke(cli, ["config", "--json", "show"])

        assert_that(result.exit_code).is_equal_to(0)
        data = json.loads(result.output)
        assert_that(data).contains("global_settings")
