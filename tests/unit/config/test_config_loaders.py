"""Unit tests for core config loading functions.

This module contains function-based pytest tests for core config utilities
including pyproject loading, lintro section parsing, and tool config extraction.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.utils.config import (
    _get_lintro_section,
    clear_pyproject_cache,
    get_tool_order_config,
    load_lintro_global_config,
    load_lintro_tool_config,
    load_post_checks_config,
    load_pyproject_config,
    load_tool_config_from_pyproject,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def clear_cache() -> None:
    """Clear pyproject-related caches before each test."""
    clear_pyproject_cache()


@pytest.fixture
def mock_empty_pyproject() -> Any:
    """Provide a mock that returns None for pyproject finding.

    Returns:
        Context manager for patching _find_pyproject to return None.
    """
    return patch("lintro.utils.config._find_pyproject", return_value=None)


@pytest.fixture
def mock_lintro_section() -> Any:
    """Factory fixture for mocking _get_lintro_section with custom return values.

    Returns:
        Function that creates a patch context manager with the given return value.
    """

    def _create_mock(return_value: dict[str, Any]) -> Any:
        return patch(
            "lintro.utils.config._get_lintro_section",
            return_value=return_value,
        )

    return _create_mock


# =============================================================================
# Tests for load_pyproject error handling
# =============================================================================


def test_load_pyproject_config_is_alias_for_load_pyproject(
    clear_cache: None,
    mock_empty_pyproject: Any,
) -> None:
    """Verify load_pyproject_config is an alias for load_pyproject.

    The load_pyproject_config function should return the same result as
    load_pyproject when no pyproject.toml file is found.

    Args:
        clear_cache: Fixture to clear caches.
        mock_empty_pyproject: Mock for patching _find_pyproject to return None.
    """
    with mock_empty_pyproject:
        result = load_pyproject_config()

    assert_that(result).is_empty()
    assert_that(result).is_instance_of(dict)


# =============================================================================
# Tests for _get_lintro_section
# =============================================================================


@pytest.mark.parametrize(
    ("pyproject_data", "expected_result", "description"),
    [
        pytest.param(
            {"tool": "invalid"},
            {},
            "non-dict tool section",
            id="invalid-tool-section",
        ),
        pytest.param(
            {"tool": {"lintro": "invalid"}},
            {},
            "non-dict lintro section",
            id="invalid-lintro-section",
        ),
        pytest.param(
            {"tool": {"lintro": {"key": "value"}}},
            {"key": "value"},
            "valid lintro section",
            id="valid-lintro-section",
        ),
        pytest.param(
            {"tool": {}},
            {},
            "empty tool section",
            id="empty-tool-section",
        ),
        pytest.param(
            {},
            {},
            "empty pyproject",
            id="empty-pyproject",
        ),
    ],
)
def test_get_lintro_section_handles_various_inputs(
    clear_cache: None,
    pyproject_data: dict[str, Any],
    expected_result: dict[str, Any],
    description: str,
) -> None:
    """Test _get_lintro_section handles various pyproject.toml structures.

    Args:
        clear_cache: Fixture to clear caches.
        pyproject_data: The mock pyproject.toml data to test.
        expected_result: The expected return value.
        description: Human-readable description of the test case.
    """
    with patch("lintro.utils.config.load_pyproject", return_value=pyproject_data):
        result = _get_lintro_section()

    assert_that(result).is_equal_to(expected_result)
    assert_that(result).is_instance_of(dict)


# =============================================================================
# Tests for load_lintro_global_config
# =============================================================================


def test_load_lintro_global_config_filters_tool_sections(
    mock_lintro_section: Any,
) -> None:
    """Verify load_lintro_global_config filters out tool-specific sections.

    Tool-specific sections like 'ruff' and 'black' should be excluded from
    the global config, leaving only non-tool settings.

    Args:
        mock_lintro_section: Factory fixture for mocking _get_lintro_section.
    """
    mock_data = {
        "global_setting": "value",
        "ruff": {"line_length": 88},
        "black": {"line_length": 88},
        "another_global": 42,
    }
    with mock_lintro_section(mock_data):
        result = load_lintro_global_config()

    assert_that(result).is_equal_to({"global_setting": "value", "another_global": 42})
    assert_that(result).does_not_contain_key("ruff")
    assert_that(result).does_not_contain_key("black")


def test_load_lintro_global_config_returns_empty_when_no_globals() -> None:
    """Verify load_lintro_global_config returns empty dict when only tools present."""
    with patch(
        "lintro.utils.config._get_lintro_section",
        return_value={"ruff": {}, "black": {}, "mypy": {}},
    ):
        result = load_lintro_global_config()

    assert_that(result).is_empty()


# =============================================================================
# Tests for load_lintro_tool_config
# =============================================================================


@pytest.mark.parametrize(
    ("section_data", "tool_name", "expected"),
    [
        pytest.param(
            {"ruff": "invalid"},
            "ruff",
            {},
            id="non-dict-tool-config",
        ),
        pytest.param(
            {"ruff": {"line_length": 100}},
            "ruff",
            {"line_length": 100},
            id="valid-tool-config",
        ),
        pytest.param(
            {"black": {"line_length": 88}},
            "ruff",
            {},
            id="tool-not-present",
        ),
        pytest.param(
            {},
            "ruff",
            {},
            id="empty-section",
        ),
    ],
)
def test_load_lintro_tool_config_handles_various_inputs(
    section_data: dict[str, Any],
    tool_name: str,
    expected: dict[str, Any],
) -> None:
    """Test load_lintro_tool_config with various section configurations.

    Args:
        section_data: Mock data for _get_lintro_section.
        tool_name: Name of the tool to load config for.
        expected: Expected return value.
    """
    with patch("lintro.utils.config._get_lintro_section", return_value=section_data):
        result = load_lintro_tool_config(tool_name)

    assert_that(result).is_equal_to(expected)
    assert_that(result).is_instance_of(dict)


# =============================================================================
# Tests for get_tool_order_config
# =============================================================================


def test_get_tool_order_config_returns_defaults_when_not_configured() -> None:
    """Verify get_tool_order_config returns default values when not configured.

    The default strategy should be 'priority' with empty custom_order and
    priority_overrides.
    """
    with patch("lintro.utils.config.load_lintro_global_config", return_value={}):
        result = get_tool_order_config()

    assert_that(result["strategy"]).is_equal_to("priority")
    assert_that(result["custom_order"]).is_empty()
    assert_that(result["custom_order"]).is_instance_of(list)
    assert_that(result["priority_overrides"]).is_empty()
    assert_that(result["priority_overrides"]).is_instance_of(dict)


def test_get_tool_order_config_returns_custom_values_when_configured() -> None:
    """Verify get_tool_order_config returns custom values when configured.

    When tool_order, tool_order_custom, and tool_priorities are set in the
    config, they should be returned in the result dictionary.
    """
    mock_config = {
        "tool_order": "custom",
        "tool_order_custom": ["ruff", "black", "mypy"],
        "tool_priorities": {"ruff": 100, "black": 50},
    }
    with patch(
        "lintro.utils.config.load_lintro_global_config",
        return_value=mock_config,
    ):
        result = get_tool_order_config()

    assert_that(result["strategy"]).is_equal_to("custom")
    assert_that(result["custom_order"]).is_equal_to(["ruff", "black", "mypy"])
    assert_that(result["custom_order"]).is_length(3)
    assert_that(result["priority_overrides"]).is_equal_to({"ruff": 100, "black": 50})
    assert_that(result["priority_overrides"]).contains_key("ruff")


# =============================================================================
# Tests for load_post_checks_config
# =============================================================================


@pytest.mark.parametrize(
    ("section_data", "expected"),
    [
        pytest.param(
            {"post_checks": {"enabled": True, "tools": ["black"]}},
            {"enabled": True, "tools": ["black"]},
            id="valid-post-checks-section",
        ),
        pytest.param(
            {"post_checks": "invalid"},
            {},
            id="non-dict-post-checks",
        ),
        pytest.param(
            {},
            {},
            id="missing-post-checks",
        ),
        pytest.param(
            {"post_checks": {}},
            {},
            id="empty-post-checks",
        ),
    ],
)
def test_load_post_checks_config_handles_various_inputs(
    section_data: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    """Test load_post_checks_config with various section configurations.

    Args:
        section_data: Mock data for _get_lintro_section.
        expected: Expected return value.
    """
    with patch("lintro.utils.config._get_lintro_section", return_value=section_data):
        result = load_post_checks_config()

    assert_that(result).is_equal_to(expected)
    assert_that(result).is_instance_of(dict)


# =============================================================================
# Tests for load_tool_config_from_pyproject
# =============================================================================


@pytest.mark.parametrize(
    ("pyproject_data", "tool_name", "expected"),
    [
        pytest.param(
            {"tool": {"ruff": "invalid"}},
            "ruff",
            {},
            id="non-dict-tool-config",
        ),
        pytest.param(
            {"tool": {"ruff": {"line-length": 100}}},
            "ruff",
            {"line-length": 100},
            id="valid-tool-config",
        ),
        pytest.param(
            {"tool": {}},
            "ruff",
            {},
            id="tool-not-in-pyproject",
        ),
    ],
)
def test_load_tool_config_from_pyproject_handles_various_inputs(
    pyproject_data: dict[str, Any],
    tool_name: str,
    expected: dict[str, Any],
) -> None:
    """Test load_tool_config_from_pyproject with various configurations.

    Args:
        pyproject_data: Mock pyproject.toml data.
        tool_name: Name of the tool to load config for.
        expected: Expected return value.
    """
    with patch("lintro.utils.config.load_pyproject", return_value=pyproject_data):
        result = load_tool_config_from_pyproject(tool_name)

    assert_that(result).is_equal_to(expected)
    assert_that(result).is_instance_of(dict)
