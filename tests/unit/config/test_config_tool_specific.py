"""Unit tests for tool-specific config loaders.

This module contains function-based pytest tests for tool-specific config
loaders including ruff, mypy, bandit, and black.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.utils.config import (
    load_bandit_config,
    load_black_config,
    load_mypy_config,
    load_ruff_config,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_load_tool_config() -> Any:
    """Factory fixture for mocking load_tool_config_from_pyproject.

    Returns:
        Function that creates a patch context manager with the given return value.
    """

    def _create_mock(return_value: dict[str, Any]) -> Any:
        return patch(
            "lintro.utils.config.load_tool_config_from_pyproject",
            return_value=return_value,
        )

    return _create_mock


# =============================================================================
# Tests for load_ruff_config
# =============================================================================


def test_load_ruff_config_flattens_lint_section(mock_load_tool_config: Any) -> None:
    """Verify load_ruff_config flattens the lint section to top level.

    Ruff's lint.select, lint.ignore, lint.extend-select, and lint.extend-ignore
    should be flattened to top-level keys with underscores.

    Args:
        mock_load_tool_config: Factory fixture for mocking
            load_tool_config_from_pyproject.
    """
    mock_config = {
        "line-length": 100,
        "lint": {
            "select": ["E", "F"],
            "ignore": ["E501"],
            "extend-select": ["W"],
            "extend-ignore": ["E402"],
        },
    }
    with mock_load_tool_config(mock_config):
        result = load_ruff_config()

    assert_that(result["select"]).is_equal_to(["E", "F"])
    assert_that(result["select"]).is_length(2)
    assert_that(result["ignore"]).is_equal_to(["E501"])
    assert_that(result["extend_select"]).is_equal_to(["W"])
    assert_that(result["extend_ignore"]).is_equal_to(["E402"])


def test_load_ruff_config_handles_non_dict_lint_section(
    mock_load_tool_config: Any,
) -> None:
    """Verify load_ruff_config handles non-dict lint section gracefully.

    When lint is not a dict, select and other lint keys should not be present.

    Args:
        mock_load_tool_config: Factory fixture for mocking
            load_tool_config_from_pyproject.
    """
    with mock_load_tool_config({"lint": "invalid", "line-length": 88}):
        result = load_ruff_config()

    assert_that(result).does_not_contain_key("select")
    assert_that(result).does_not_contain_key("ignore")


def test_load_ruff_config_handles_empty_config(mock_load_tool_config: Any) -> None:
    """Verify load_ruff_config handles empty configuration.

    Args:
        mock_load_tool_config: Factory fixture for mocking
            load_tool_config_from_pyproject.
    """
    with mock_load_tool_config({}):
        result = load_ruff_config()

    assert_that(result).is_instance_of(dict)


# =============================================================================
# Tests for tool-specific config loaders (bandit, black)
# =============================================================================


@pytest.mark.parametrize(
    ("loader_func", "tool_name", "config_data", "expected"),
    [
        pytest.param(
            load_bandit_config,
            "bandit",
            {"exclude_dirs": ["tests"]},
            {"exclude_dirs": ["tests"]},
            id="bandit-config",
        ),
        pytest.param(
            load_black_config,
            "black",
            {"line-length": 88},
            {"line-length": 88},
            id="black-config",
        ),
    ],
)
def test_tool_config_loaders_return_correct_config(
    loader_func: Any,
    tool_name: str,
    config_data: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    """Test that tool-specific config loaders correctly return tool configuration.

    Args:
        loader_func: The config loader function to test.
        tool_name: Name of the tool (for documentation).
        config_data: Mock config data to return.
        expected: Expected result from the loader.
    """
    with patch(
        "lintro.utils.config.load_tool_config_from_pyproject",
        return_value=config_data,
    ):
        result = loader_func()

    assert_that(result).is_equal_to(expected)
    assert_that(result).is_instance_of(dict)


# =============================================================================
# Tests for load_mypy_config
# =============================================================================


def test_load_mypy_config_from_pyproject(tmp_path: Path) -> None:
    """Verify mypy config loads correctly from pyproject.toml.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.mypy]\nstrict = true\nwarn_return_any = true\n")

    config, path = load_mypy_config(base_dir=tmp_path)

    assert_that(config).is_equal_to({"strict": True, "warn_return_any": True})
    assert_that(path).is_equal_to(pyproject)
    assert_that(config).is_instance_of(dict)


def test_load_mypy_config_from_mypy_ini(tmp_path: Path) -> None:
    """Verify mypy config loads correctly from mypy.ini.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    mypy_ini = tmp_path / "mypy.ini"
    mypy_ini.write_text("[mypy]\nstrict = true\n")

    config, path = load_mypy_config(base_dir=tmp_path)

    assert_that(config).contains_key("strict")
    assert_that(path).is_equal_to(mypy_ini)


def test_load_mypy_config_from_dot_mypy_ini(tmp_path: Path) -> None:
    """Verify mypy config loads correctly from .mypy.ini.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    dot_mypy_ini = tmp_path / ".mypy.ini"
    dot_mypy_ini.write_text("[mypy]\nwarn_unused_ignores = True\n")

    config, path = load_mypy_config(base_dir=tmp_path)

    assert_that(config).contains_key("warn_unused_ignores")
    assert_that(path).is_equal_to(dot_mypy_ini)


def test_load_mypy_config_returns_empty_when_no_config_file(tmp_path: Path) -> None:
    """Verify load_mypy_config returns empty config when no files found.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    config, path = load_mypy_config(base_dir=tmp_path)

    assert_that(config).is_empty()
    assert_that(config).is_instance_of(dict)
    assert_that(path).is_none()


def test_load_mypy_config_defaults_to_cwd_when_no_base_dir() -> None:
    """Verify load_mypy_config defaults to current working directory."""
    with patch("lintro.utils.config.Path") as mock_path:
        mock_cwd = MagicMock()
        mock_path.cwd.return_value = mock_cwd
        mock_cwd.__truediv__ = MagicMock(
            return_value=MagicMock(exists=MagicMock(return_value=False)),
        )

        load_mypy_config(base_dir=None)

        mock_path.cwd.assert_called_once()
