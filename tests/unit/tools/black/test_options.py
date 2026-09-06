"""Unit tests for black plugin options."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.black import BLACK_DEFAULT_TIMEOUT, BlackPlugin


def _record_black_argv(
    commands: list[list[str]],
) -> Callable[..., tuple[bool, str]]:
    """Build a plain stand-in for black's subprocess runner.

    Args:
        commands: List the executed black argv is appended to.

    Returns:
        A callable recording each invocation and reporting a clean run.
    """

    def fake_run(*, cmd: list[str], **kwargs: object) -> tuple[bool, str]:
        """Record one black invocation and report success.

        Taking ``cmd`` as a typed keyword parameter narrows it for real, where
        ``typing.cast`` was a no-op that would have silently split a stray
        string into characters (#2315).

        Args:
            cmd: Command the plugin passed to the runner.
            **kwargs: Remaining runner arguments (timeout, cwd).

        Returns:
            A successful run with empty output.
        """
        commands.append(list(cmd))
        return (True, "")

    return fake_run


def test_default_options(black_plugin: BlackPlugin) -> None:
    """Default options include expected keys and values.

    Args:
        black_plugin: The BlackPlugin instance to test.
    """
    defaults = black_plugin.definition.default_options
    assert_that(defaults["timeout"]).is_equal_to(BLACK_DEFAULT_TIMEOUT)
    assert_that(defaults["line_length"]).is_none()
    assert_that(defaults["fast"]).is_false()
    assert_that(defaults["preview"]).is_false()


def test_set_options_line_length(black_plugin: BlackPlugin) -> None:
    """Set line_length option.

    Args:
        black_plugin: The BlackPlugin instance to test.
    """
    black_plugin.set_options(line_length=100)
    assert_that(black_plugin.options.get("line_length")).is_equal_to(100)


def test_set_options_invalid_line_length_type(black_plugin: BlackPlugin) -> None:
    """Raise ValueError for invalid line_length type.

    Args:
        black_plugin: The BlackPlugin instance to test.
    """
    with pytest.raises(ValueError, match="line_length must be"):
        black_plugin.set_options(line_length="wide")  # type: ignore[arg-type]


def test_set_options_fast(black_plugin: BlackPlugin) -> None:
    """Set fast option.

    Args:
        black_plugin: The BlackPlugin instance to test.
    """
    black_plugin.set_options(fast=True)
    assert_that(black_plugin.options.get("fast")).is_true()


def test_check_passes_line_length_to_black(
    black_plugin: BlackPlugin,
    tmp_path: Path,
) -> None:
    """check() argv includes --line-length when Lintro config injection is off.

    Args:
        black_plugin: The BlackPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text('"""Module."""\n')
    black_plugin.set_options(line_length=100)

    commands: list[list[str]] = []

    with (
        patch.object(black_plugin, "_build_config_args", return_value=[]),
        patch.object(black_plugin, "_check_line_length_violations", return_value=[]),
        patch.object(
            black_plugin,
            "_run_subprocess",
            side_effect=_record_black_argv(commands),
        ),
    ):
        result = black_plugin.check([str(py_file)], {})

    assert_that(commands).is_length(1)
    assert_that(commands[0]).contains("--line-length", "100")
    assert_that(commands[0]).contains("--check")
    assert_that(result.success).is_true()


def test_check_passes_fast_to_black(
    black_plugin: BlackPlugin,
    tmp_path: Path,
) -> None:
    """check() argv includes --fast when enabled.

    Args:
        black_plugin: The BlackPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text('"""Module."""\n')
    black_plugin.set_options(fast=True)

    commands: list[list[str]] = []

    with (
        patch.object(black_plugin, "_check_line_length_violations", return_value=[]),
        patch.object(
            black_plugin,
            "_run_subprocess",
            side_effect=_record_black_argv(commands),
        ),
    ):
        result = black_plugin.check([str(py_file)], {})

    assert_that(commands).is_length(1)
    assert_that(commands[0]).contains("--fast")
    assert_that(commands[0]).contains("--check")
    assert_that(result.success).is_true()


def test_line_length_check_accepts_float_timeout(black_plugin: BlackPlugin) -> None:
    """A float ``timeout`` (as ``set_options`` stores it) must not raise.

    ``BaseToolPlugin.set_options`` normalises ``timeout`` to ``float``, so
    ``--tool-options black:timeout=120`` reaches the line-length checker as
    ``120.0``. Coercing it via ``int(str(...))`` raised ``ValueError``.

    Args:
        black_plugin: The BlackPlugin instance to test.
    """
    black_plugin.set_options(timeout=120)
    assert_that(black_plugin.options.get("timeout")).is_equal_to(120.0)

    with patch(
        "lintro.tools.core.line_length_checker.check_line_length_violations",
        return_value=[],
    ) as mock_check:
        issues = black_plugin._check_line_length_violations(
            files=["example.py"],
            cwd=".",
        )

    assert_that(issues).is_empty()
    assert_that(mock_check.call_args.kwargs["timeout"]).is_equal_to(120)
