"""Unit tests for black plugin options."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.black import BLACK_DEFAULT_TIMEOUT, BlackPlugin


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
        black_plugin.set_options(line_length="wide")


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

    with (
        patch.object(black_plugin, "_build_config_args", return_value=[]),
        patch.object(black_plugin, "_check_line_length_violations", return_value=[]),
        patch.object(
            black_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ) as mock_run,
    ):
        black_plugin.check([str(py_file)], {})

    cmd = mock_run.call_args.kwargs["cmd"]
    assert_that(cmd).contains("--line-length", "100")
    assert_that(cmd).contains("--check")


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

    with (
        patch.object(black_plugin, "_check_line_length_violations", return_value=[]),
        patch.object(
            black_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ) as mock_run,
    ):
        black_plugin.check([str(py_file)], {})

    cmd = mock_run.call_args.kwargs["cmd"]
    assert_that(cmd).contains("--fast")
    assert_that(cmd).contains("--check")
