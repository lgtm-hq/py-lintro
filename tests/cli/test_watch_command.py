"""Tests for the ``lintro watch`` CLI flag/config overlay."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.config.lintro_config import LintroConfig
from lintro.config.watch_config import WatchConfig


def test_no_fix_overrides_config_auto_fix() -> None:
    """``--no-fix`` must force check-only when config sets ``auto_fix``."""
    captured: dict[str, Any] = {}
    config = LintroConfig(watch=WatchConfig(auto_fix=True, clear_screen=True))

    def _watch_paths(*_args: Any, **kwargs: Any) -> None:
        captured["include_venv"] = kwargs.get("include_venv")

    with (
        patch(
            "lintro.cli_utils.commands.watch.load_config",
            return_value=config,
        ),
        patch(
            "lintro.cli_utils.commands.watch.watch_paths",
            side_effect=_watch_paths,
        ) as watch_paths,
        patch(
            "lintro.cli_utils.commands.watch.WatchRunner",
        ) as runner_cls,
    ):
        runner_cls.return_value = MagicMock()
        runner_cls.return_value.last_exit_code = 0
        result = CliRunner().invoke(cli, ["watch", "--no-fix", "--no-clear", "."])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(runner_cls.call_args.kwargs["auto_fix"]).is_false()
    assert_that(runner_cls.call_args.kwargs["clear_screen"]).is_false()
    assert_that(watch_paths.called).is_true()
    assert_that(captured["include_venv"]).is_false()


def test_include_venv_is_forwarded_to_watcher() -> None:
    """``--include-venv`` must reach both the runner and the watcher."""
    with (
        patch(
            "lintro.cli_utils.commands.watch.load_config",
            return_value=LintroConfig(),
        ),
        patch("lintro.cli_utils.commands.watch.watch_paths") as watch_paths,
        patch(
            "lintro.cli_utils.commands.watch.WatchRunner",
        ) as runner_cls,
    ):
        runner_cls.return_value = MagicMock()
        runner_cls.return_value.last_exit_code = 0
        result = CliRunner().invoke(cli, ["watch", "--include-venv", "."])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(runner_cls.call_args.kwargs["include_venv"]).is_true()
    assert_that(watch_paths.call_args.kwargs["include_venv"]).is_true()
    assert_that(watch_paths.call_args.kwargs["on_event"]).is_equal_to(
        runner_cls.return_value.record_event,
    )


def test_config_values_apply_when_cli_flags_are_omitted() -> None:
    """Watch config should supply every option not set on the CLI."""
    config = LintroConfig(
        watch=WatchConfig(
            auto_fix=True,
            clear_screen=True,
            debounce_ms=750,
            tools=["ruff"],
            ignore=["**/generated/**"],
        ),
    )
    with (
        patch(
            "lintro.cli_utils.commands.watch.load_config",
            return_value=config,
        ),
        patch("lintro.cli_utils.commands.watch.watch_paths") as watch_paths,
        patch("lintro.cli_utils.commands.watch.WatchRunner") as runner_cls,
    ):
        runner_cls.return_value = MagicMock()
        runner_cls.return_value.last_exit_code = 0
        result = CliRunner().invoke(cli, ["watch", "."])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(runner_cls.call_args.kwargs["auto_fix"]).is_true()
    assert_that(runner_cls.call_args.kwargs["clear_screen"]).is_true()
    assert_that(runner_cls.call_args.kwargs["restrict_to"]).is_equal_to(["ruff"])
    assert_that(watch_paths.call_args.kwargs["debounce_ms"]).is_equal_to(750)
    assert_that(watch_paths.call_args.kwargs["ignore_patterns"]).is_equal_to(
        ["**/generated/**"],
    )


def test_tools_all_uses_smart_selection() -> None:
    """``--tools all`` should not be treated as a tool named ``all``."""
    with (
        patch(
            "lintro.cli_utils.commands.watch.load_config",
            return_value=LintroConfig(),
        ),
        patch("lintro.cli_utils.commands.watch.watch_paths"),
        patch("lintro.cli_utils.commands.watch.WatchRunner") as runner_cls,
    ):
        runner_cls.return_value = MagicMock()
        runner_cls.return_value.last_exit_code = 0
        result = CliRunner().invoke(cli, ["watch", "--tools", "all", "."])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(runner_cls.call_args.kwargs["restrict_to"]).is_none()


def test_watch_help_describes_config_fallbacks() -> None:
    """Watch help should explain config-backed tools and debounce defaults."""
    result = CliRunner().invoke(cli, ["watch", "--help"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("watch.tools")
    assert_that(result.output).contains("watch.debounce_ms")


def test_unknown_cli_tool_fails_before_watcher_starts() -> None:
    """An unknown ``--tools`` name should fail with the standard suggestion."""
    with patch("lintro.cli_utils.commands.watch.watch_paths") as watch_paths:
        result = CliRunner().invoke(cli, ["watch", "--tools", "ruft", "."])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Unknown tool 'ruft'")
    assert_that(result.output).contains("ruff")
    watch_paths.assert_not_called()


def test_negative_debounce_is_a_clean_click_error() -> None:
    """A negative debounce value should fail without a Python traceback."""
    result = CliRunner().invoke(cli, ["watch", "--debounce", "-1", "."])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Invalid value for '--debounce'")
    assert_that(result.output).does_not_contain("Traceback")


def test_machine_output_formats_are_rejected() -> None:
    """Watch mode should not claim a single-document JSON contract."""
    result = CliRunner().invoke(cli, ["watch", "--output-format", "json", "."])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Invalid value for '--output-format'")


def test_malformed_watch_config_is_a_clean_click_error() -> None:
    """Malformed watch config should fail without entering the observer loop."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path(".lintro-config.yaml").write_text("watch: 500\n", encoding="utf-8")
        result = runner.invoke(cli, ["watch", "."])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("watch config must be a mapping")
    assert_that(result.output).does_not_contain("Traceback")
