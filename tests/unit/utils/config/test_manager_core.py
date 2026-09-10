"""Unit tests for UnifiedConfigManager initialization, refresh, and getter methods."""

from __future__ import annotations

from unittest.mock import patch

from assertpy import assert_that

from lintro.utils.unified_config import (
    ToolConfigInfo,
    UnifiedConfigManager,
)

# =============================================================================
# Tests for UnifiedConfigManager initialization
# =============================================================================


def test_manager_initialization_loads_global_config() -> None:
    """Verify manager loads global config during initialization.

    The manager should call load_lintro_global_config and store the result.
    """
    with (
        patch(
            "lintro.utils.unified_config_manager.load_lintro_global_config",
            return_value={"line_length": 100},
        ),
        patch(
            "lintro.utils.unified_config_manager.get_tool_config_summary",
            return_value={},
        ),
        patch(
            "lintro.utils.unified_config_manager.validate_config_consistency",
            return_value=[],
        ),
    ):
        manager = UnifiedConfigManager()

        assert_that(manager.global_config).is_equal_to({"line_length": 100})


def test_manager_initialization_loads_tool_configs() -> None:
    """Verify manager loads tool configs during initialization.

    The manager should call get_tool_config_summary and store the result.
    """
    tool_configs = {"ruff": ToolConfigInfo(tool_name="ruff")}
    with (
        patch(
            "lintro.utils.unified_config_manager.load_lintro_global_config",
            return_value={},
        ),
        patch(
            "lintro.utils.unified_config_manager.get_tool_config_summary",
            return_value=tool_configs,
        ),
        patch(
            "lintro.utils.unified_config_manager.validate_config_consistency",
            return_value=[],
        ),
    ):
        manager = UnifiedConfigManager()

        assert_that(manager.tool_configs).contains_key("ruff")


def test_manager_initialization_loads_warnings() -> None:
    """Verify manager loads config warnings during initialization.

    The manager should call validate_config_consistency and store warnings.
    """
    warnings = ["Warning: config mismatch"]
    with (
        patch(
            "lintro.utils.unified_config_manager.load_lintro_global_config",
            return_value={},
        ),
        patch(
            "lintro.utils.unified_config_manager.get_tool_config_summary",
            return_value={},
        ),
        patch(
            "lintro.utils.unified_config_manager.validate_config_consistency",
            return_value=warnings,
        ),
    ):
        manager = UnifiedConfigManager()

        assert_that(manager.warnings).is_length(1)
        assert_that(manager.warnings[0]).contains("Warning")


# =============================================================================
# Tests for refresh method
# =============================================================================


def test_manager_refresh_reloads_all_config(manager: UnifiedConfigManager) -> None:
    """Verify refresh method reloads all configuration.

    After refresh, the manager should have updated config from all sources.


    Args:
        manager: Configuration manager instance.
    """
    with (
        patch(
            "lintro.utils.unified_config_manager.load_lintro_global_config",
            return_value={"line_length": 120},
        ),
        patch(
            "lintro.utils.unified_config_manager.get_tool_config_summary",
            return_value={"black": ToolConfigInfo(tool_name="black")},
        ),
        patch(
            "lintro.utils.unified_config_manager.validate_config_consistency",
            return_value=["New warning"],
        ),
    ):
        manager.refresh()

        assert_that(manager.global_config).is_equal_to({"line_length": 120})
        assert_that(manager.tool_configs).contains_key("black")
        assert_that(manager.warnings).is_length(1)


def test_manager_refresh_does_not_raise(manager: UnifiedConfigManager) -> None:
    """Verify refresh completes without raising exceptions.

    Even with default mocks, refresh should complete successfully.


    Args:
        manager: Configuration manager instance.
    """
    with (
        patch(
            "lintro.utils.unified_config_manager.load_lintro_global_config",
            return_value={},
        ),
        patch(
            "lintro.utils.unified_config_manager.get_tool_config_summary",
            return_value={},
        ),
        patch(
            "lintro.utils.unified_config_manager.validate_config_consistency",
            return_value=[],
        ),
    ):
        # Should not raise
        manager.refresh()


# =============================================================================
# Tests for get_effective_line_length method
# =============================================================================


def test_manager_get_effective_line_length_delegates_to_module_function(
    manager: UnifiedConfigManager,
) -> None:
    """Verify get_effective_line_length delegates to the module function.

    The manager method should call the standalone get_effective_line_length
    function and return its result.


    Args:
        manager: Configuration manager instance.
    """
    with patch(
        "lintro.utils.unified_config_manager.get_effective_line_length",
        return_value=120,
    ):
        result = manager.get_effective_line_length("ruff")

        assert_that(result).is_equal_to(120)


def test_manager_get_effective_line_length_returns_none_when_not_configured(
    manager: UnifiedConfigManager,
) -> None:
    """Verify None is returned when no line length is configured.

    When the underlying function returns None, the manager should too.


    Args:
        manager: Configuration manager instance.
    """
    with patch(
        "lintro.utils.unified_config_manager.get_effective_line_length",
        return_value=None,
    ):
        result = manager.get_effective_line_length("unknown")

        assert_that(result).is_none()


# =============================================================================
# Tests for get_tool_config method
# =============================================================================


def test_manager_get_tool_config_returns_existing_config(
    mock_manager_dependencies: None,
) -> None:
    """Verify get_tool_config returns existing tool config if present.

    When a tool is already in tool_configs, it should be returned directly.

    Args:
        mock_manager_dependencies: Mock manager dependencies.
    """
    tool_info = ToolConfigInfo(tool_name="ruff", is_injectable=True)
    with (
        patch(
            "lintro.utils.unified_config_manager.load_lintro_global_config",
            return_value={},
        ),
        patch(
            "lintro.utils.unified_config_manager.get_tool_config_summary",
            return_value={"ruff": tool_info},
        ),
        patch(
            "lintro.utils.unified_config_manager.validate_config_consistency",
            return_value=[],
        ),
    ):
        manager = UnifiedConfigManager()
        result = manager.get_tool_config("ruff")

        assert_that(result.tool_name).is_equal_to("ruff")


def test_manager_get_tool_config_creates_config_for_missing_tool(
    manager: UnifiedConfigManager,
) -> None:
    """Verify get_tool_config creates config for unknown tools.

    When requesting config for a tool not in tool_configs, a new
    ToolConfigInfo should be created with loaded native config.


    Args:
        manager: Configuration manager instance.
    """
    with (
        patch(
            "lintro.utils.unified_config_manager._load_native_tool_config",
            return_value={"option": "value"},
        ),
        patch(
            "lintro.utils.unified_config_manager.load_lintro_tool_config",
            return_value={},
        ),
    ):
        result = manager.get_tool_config("custom_tool")

        assert_that(result).is_instance_of(ToolConfigInfo)
        assert_that(result.tool_name).is_equal_to("custom_tool")
        assert_that(result.native_config).is_equal_to({"option": "value"})


def test_manager_get_tool_config_caches_created_config(
    manager: UnifiedConfigManager,
) -> None:
    """Verify newly created tool configs are cached.

    After get_tool_config creates a config, it should be stored in tool_configs.


    Args:
        manager: Configuration manager instance.
    """
    with (
        patch(
            "lintro.utils.unified_config_manager._load_native_tool_config",
            return_value={},
        ),
        patch(
            "lintro.utils.unified_config_manager.load_lintro_tool_config",
            return_value={},
        ),
    ):
        manager.get_tool_config("new_tool")

        assert_that(manager.tool_configs).contains_key("new_tool")
