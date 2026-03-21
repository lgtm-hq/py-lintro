"""Unit tests for lintro configuration loaders from pyproject.toml."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.config.config_loader import _load_pyproject_fallback
from lintro.utils.config import (
    clear_pyproject_cache,
    load_lintro_tool_config,
)


def test_load_lintro_tool_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load tool-specific config sections from pyproject.

    Args:
        tmp_path: Temporary directory for pyproject creation.
        monkeypatch: Pytest monkeypatch to chdir into temp dir.
    """
    # Clear both LRU caches to ensure we load from the test directory
    clear_pyproject_cache()

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        (
            "[tool.lintro]\n"
            "[tool.lintro.ruff]\n"
            'select = ["E", "F"]\n'
            "line_length = 88\n"
            "[tool.lintro.prettier]\n"
            "single_quote = true\n"
        ),
    )
    monkeypatch.chdir(tmp_path)
    ruff_cfg = load_lintro_tool_config("ruff")
    assert_that(ruff_cfg.get("line_length")).is_equal_to(88)
    assert_that(ruff_cfg.get("select")).is_equal_to(["E", "F"])
    prettier_cfg = load_lintro_tool_config("prettier")
    assert_that(prettier_cfg.get("single_quote") is True).is_true()
    missing_cfg = load_lintro_tool_config("yamllint")
    assert_that(missing_cfg).is_equal_to({})


def test_config_loader_handles_missing_and_malformed_pyproject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate loaders handle missing and malformed pyproject files.

    Args:
        tmp_path: Temporary directory used to simulate project roots.
        monkeypatch: Pytest monkeypatch fixture for chdir and environment.
    """
    # Clear both LRU caches to ensure we load from the test directory
    clear_pyproject_cache()

    from lintro.utils import config as cfg

    # 1) Missing pyproject.toml
    monkeypatch.chdir(tmp_path)
    assert_that(cfg.load_lintro_tool_config("ruff")).is_equal_to({})
    assert_that(cfg.load_post_checks_config()).is_equal_to({})

    # 2) Malformed pyproject.toml should be handled gracefully
    (tmp_path / "pyproject.toml").write_text("not: [valid\n")
    assert_that(cfg.load_lintro_tool_config("ruff")).is_equal_to({})
    assert_that(cfg.load_post_checks_config()).is_equal_to({})


# =============================================================================
# TOML Parse Error Logging Tests
# =============================================================================


def test_load_pyproject_toml_parse_error_logs_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify TOML parse error logs warning with file path and error details.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture for chdir.
    """
    clear_pyproject_cache()

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("invalid: [toml content")
    monkeypatch.chdir(tmp_path)

    with patch("lintro.config.config_loader.logger") as mock_logger:
        result, path = _load_pyproject_fallback()

        assert_that(result).is_equal_to({})
        assert_that(path).is_none()
        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert_that(warning_msg).contains("Failed to parse pyproject.toml")
        assert_that(warning_msg).contains(str(pyproject))


def test_load_pyproject_os_error_logs_debug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify OS error reading pyproject.toml logs debug message.

    Args:
        tmp_path: Pytest temporary directory fixture.
        monkeypatch: Pytest monkeypatch fixture for chdir.
    """
    clear_pyproject_cache()

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.lintro]")
    monkeypatch.chdir(tmp_path)

    with (
        patch("lintro.config.config_loader.logger") as mock_logger,
        patch("pathlib.Path.open", side_effect=OSError("Permission denied")),
    ):
        result, path = _load_pyproject_fallback()

        assert_that(result).is_equal_to({})
        assert_that(path).is_none()
        mock_logger.debug.assert_called_once()
        debug_msg = mock_logger.debug.call_args[0][0]
        assert_that(debug_msg).contains("Could not read pyproject.toml")
