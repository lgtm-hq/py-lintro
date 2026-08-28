"""Tests for the ``lintro watch`` CLI flag/config overlay."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - subprocess runs a fixed interpreter probe; shell=False
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.config.lintro_config import LintroConfig
from lintro.config.watch_config import WatchConfig
from lintro.utils.execution.tool_configuration import ToolsToRunResult


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
            "lintro.watch.watcher.watch_paths",
            side_effect=_watch_paths,
        ) as watch_paths,
        patch(
            "lintro.watch.runner.WatchRunner",
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
        patch("lintro.watch.watcher.watch_paths") as watch_paths,
        patch(
            "lintro.watch.runner.WatchRunner",
        ) as runner_cls,
    ):
        runner_cls.return_value = MagicMock()
        runner_cls.return_value.last_exit_code = 0
        result = CliRunner().invoke(cli, ["watch", "--include-venv", "."])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(runner_cls.call_args.kwargs["auto_fix"]).is_false()
    assert_that(runner_cls.call_args.kwargs["include_venv"]).is_true()
    assert_that(watch_paths.call_args.kwargs["include_venv"]).is_true()
    assert_that(watch_paths.call_args.kwargs["debounce_ms"]).is_equal_to(300)
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
        patch("lintro.watch.watcher.watch_paths") as watch_paths,
        patch("lintro.watch.runner.WatchRunner") as runner_cls,
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
        patch("lintro.watch.watcher.watch_paths"),
        patch("lintro.watch.runner.WatchRunner") as runner_cls,
    ):
        runner_cls.return_value = MagicMock()
        runner_cls.return_value.last_exit_code = 0
        result = CliRunner().invoke(cli, ["watch", "--tools", "all", "."])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(runner_cls.call_args.kwargs["restrict_to"]).is_none()


def test_config_tools_all_uses_smart_selection() -> None:
    """The ``all`` sentinel should work when sourced from watch.tools."""
    config = LintroConfig(watch=WatchConfig(tools=["all"]))
    with (
        patch(
            "lintro.cli_utils.commands.watch.load_config",
            return_value=config,
        ),
        patch("lintro.watch.watcher.watch_paths"),
        patch("lintro.watch.runner.WatchRunner") as runner_cls,
    ):
        runner_cls.return_value.last_exit_code = 0
        result = CliRunner().invoke(cli, ["watch", "."])

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
    with patch("lintro.watch.watcher.watch_paths") as watch_paths:
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


@pytest.mark.parametrize("output_format", ["json", "csv", "markdown"])
def test_machine_output_formats_are_rejected(output_format: str) -> None:
    """Watch mode should not claim a single-document JSON contract."""
    result = CliRunner().invoke(
        cli,
        ["watch", "--output-format", output_format, "."],
    )

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Invalid value for '--output-format'")


def test_config_tool_error_names_watch_tools_source() -> None:
    """Config-sourced invalid tools should not be blamed on a missing CLI flag."""
    config = LintroConfig(watch=WatchConfig(tools=["ruft"]))
    with (
        patch(
            "lintro.cli_utils.commands.watch.load_config",
            return_value=config,
        ),
        patch(
            "lintro.cli_utils.commands.watch.get_tools_to_run",
            side_effect=ValueError("Unknown tool 'ruft'"),
        ),
    ):
        result = CliRunner().invoke(cli, ["watch", "."])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("Invalid value for watch.tools")


def test_malformed_watch_config_is_a_clean_click_error() -> None:
    """Malformed watch config should fail without entering the observer loop."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path(".lintro-config.yaml").write_text("watch: 500\n", encoding="utf-8")
        result = runner.invoke(cli, ["watch", "."])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("watch config must be a mapping")
    assert_that(result.output).does_not_contain("Traceback")


def test_default_path_is_current_directory() -> None:
    """Omitting PATHS should watch the current directory."""
    with (
        patch("lintro.watch.watcher.watch_paths") as watch_paths,
        patch("lintro.watch.runner.WatchRunner") as runner_cls,
    ):
        runner_cls.return_value.last_exit_code = 0
        result = CliRunner().invoke(cli, ["watch"])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(watch_paths.call_args.args[0]).is_equal_to(["."])


def test_empty_validated_tool_selection_is_usage_error() -> None:
    """A fully disabled explicit allowlist should fail before watching."""
    with (
        patch(
            "lintro.cli_utils.commands.watch.get_tools_to_run",
            return_value=ToolsToRunResult(),
        ),
        patch("lintro.watch.watcher.watch_paths") as watch_paths,
    ):
        result = CliRunner().invoke(cli, ["watch", "--tools", "ruff", "."])

    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains("No enabled watch tools remain")
    watch_paths.assert_not_called()


def test_watcher_os_error_is_clean_click_error() -> None:
    """Observer startup failures should produce a concise nonzero exit."""
    with patch(
        "lintro.watch.watcher.watch_paths",
        side_effect=OSError("permission denied"),
    ):
        result = CliRunner().invoke(cli, ["watch", "."])

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("Watch failed: permission denied")
    assert_that(result.output).does_not_contain("Traceback")


def test_last_batch_exit_code_is_process_exit_code() -> None:
    """Clean Ctrl-C shutdown should return the latest lint result."""
    with (
        patch("lintro.watch.watcher.watch_paths"),
        patch("lintro.watch.runner.WatchRunner") as runner_cls,
    ):
        runner_cls.return_value.last_exit_code = 1
        result = CliRunner().invoke(cli, ["watch", "."])

    assert_that(result.exit_code).is_equal_to(1)


def _run_isolated_cli_probe(script: str) -> subprocess.CompletedProcess[str]:
    """Run a CLI import probe in a fresh interpreter.

    Reloading ``lintro.cli`` in-process leaves later tests patching a
    different module object than ``main`` was imported from.

    Args:
        script: Python source to execute with ``python -c``.

    Returns:
        The completed subprocess result.
    """
    return subprocess.run(  # nosec B603 - fixed interpreter and inline script
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        cwd=Path(__file__).resolve().parents[2],
        env=os.environ.copy(),
        text=True,
    )


def test_importing_cli_does_not_load_watchdog() -> None:
    """Importing the CLI must not import watchdog.

    Scheduled report jobs mount current source into a fallback GHCR image
    that may predate the watchdog dependency. ``lintro check`` has to start
    in that environment.
    """
    script = """
import sys

import lintro.cli
import lintro.cli_utils.commands.watch as command_mod

leftover = [
    name
    for name in sys.modules
    if name == "watchdog" or name.startswith("watchdog.")
]
if leftover:
    raise SystemExit(f"watchdog imported: {leftover}")
if lintro.cli.watch_command.name != "watch":
    raise SystemExit("cli watch command not registered")
if command_mod.watch_command.name != "watch":
    raise SystemExit("watch command module not loaded")
"""
    result = _run_isolated_cli_probe(script=script)
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr).is_empty()


def test_importing_cli_survives_missing_watchdog() -> None:
    """CLI import must succeed when watchdog is not installed."""
    script = """
import builtins
import sys

from click.testing import CliRunner

real_import = builtins.__import__


def _blocked_import(
    name,
    globals=None,
    locals=None,
    fromlist=(),
    level=0,
):
    if name == "watchdog" or name.startswith("watchdog."):
        raise ModuleNotFoundError("No module named 'watchdog'")
    return real_import(name, globals, locals, fromlist, level)


builtins.__import__ = _blocked_import
for name in list(sys.modules):
    if name == "watchdog" or name.startswith("watchdog."):
        del sys.modules[name]

import lintro.cli

result = CliRunner().invoke(lintro.cli.cli, ["check", "--help"])
if result.exit_code != 0 or "Usage:" not in result.output:
    raise SystemExit(result.output or result.exception)
"""
    result = _run_isolated_cli_probe(script=script)
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr).is_empty()
