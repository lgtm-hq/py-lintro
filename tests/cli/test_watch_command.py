"""Tests for the ``lintro watch`` CLI flag/config overlay."""

from __future__ import annotations

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
        result = CliRunner().invoke(cli, ["watch", "--include-venv", "."])

    assert_that(result.exit_code).is_equal_to(0)
    assert_that(runner_cls.call_args.kwargs["include_venv"]).is_true()
    assert_that(watch_paths.call_args.kwargs["include_venv"]).is_true()
