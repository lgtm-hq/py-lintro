"""Unit tests for UnifiedConfigManager apply_config, reporting, and integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.utils.unified_config import UnifiedConfigManager

# =============================================================================
# Tests for apply_config_to_tool method
# =============================================================================


def test_manager_apply_config_does_nothing_for_tool_without_name(
    manager: UnifiedConfigManager,
) -> None:
    """Verify apply_config_to_tool skips tools without a name.

    When tool.name is empty, set_options should not be called.


    Args:
        manager: Configuration manager instance.
    """
    mock_tool = MagicMock()
    mock_tool.name = ""

    manager.apply_config_to_tool(mock_tool)

    mock_tool.set_options.assert_not_called()


def test_manager_apply_config_calls_set_options_with_effective_config(
    manager: UnifiedConfigManager,
    mock_tool: MagicMock,
) -> None:
    """Verify apply_config_to_tool calls set_options on the tool.

    The tool's set_options method should be called with merged config
    from all sources.

            mock_tool: Mock tool instance.

            mock_tool: Mock tool instance.


    Args:
        manager: Configuration manager instance.
        mock_tool: Mock tool instance.
    """
    with (
        patch(
            "lintro.utils.unified_config_manager.is_tool_injectable",
            return_value=True,
        ),
        patch.object(manager, "get_effective_line_length", return_value=100),
        patch(
            "lintro.utils.unified_config_manager.load_lintro_tool_config",
            return_value={"strict": True},
        ),
    ):
        manager.apply_config_to_tool(mock_tool, cli_overrides={"debug": True})

        mock_tool.set_options.assert_called_once()


def test_manager_apply_config_includes_line_length_for_injectable_tools(
    manager: UnifiedConfigManager,
    mock_tool: MagicMock,
) -> None:
    """Verify line_length is included for injectable tools.

    When is_tool_injectable returns True, line_length should be passed
    to set_options.

            mock_tool: Mock tool instance.

            mock_tool: Mock tool instance.


    Args:
        manager: Configuration manager instance.
        mock_tool: Mock tool instance.
    """
    with (
        patch(
            "lintro.utils.unified_config_manager.is_tool_injectable",
            return_value=True,
        ),
        patch.object(manager, "get_effective_line_length", return_value=100),
        patch(
            "lintro.utils.unified_config_manager.load_lintro_tool_config",
            return_value={},
        ),
    ):
        manager.apply_config_to_tool(mock_tool)

        call_kwargs = mock_tool.set_options.call_args[1]
        assert_that(call_kwargs).contains_key("line_length")
        assert_that(call_kwargs["line_length"]).is_equal_to(100)


def test_manager_apply_config_cli_overrides_take_precedence(
    manager: UnifiedConfigManager,
    mock_tool: MagicMock,
) -> None:
    """Verify CLI overrides have highest priority.

    When cli_overrides conflict with other config sources, CLI values
    should win.

            mock_tool: Mock tool instance.

            mock_tool: Mock tool instance.


    Args:
        manager: Configuration manager instance.
        mock_tool: Mock tool instance.
    """
    with (
        patch(
            "lintro.utils.unified_config_manager.is_tool_injectable",
            return_value=True,
        ),
        patch.object(manager, "get_effective_line_length", return_value=100),
        patch(
            "lintro.utils.unified_config_manager.load_lintro_tool_config",
            return_value={"line_length": 80},
        ),
    ):
        manager.apply_config_to_tool(mock_tool, cli_overrides={"line_length": 120})

        call_kwargs = mock_tool.set_options.call_args[1]
        assert_that(call_kwargs["line_length"]).is_equal_to(120)


def test_manager_apply_config_raises_value_error_from_tool(
    manager: UnifiedConfigManager,
    mock_tool: MagicMock,
) -> None:
    """Verify ValueError from tool.set_options is re-raised.

    Configuration errors (ValueError, TypeError) should propagate to the caller.

            mock_tool: Mock tool instance.

            mock_tool: Mock tool instance.


    Args:
        manager: Configuration manager instance.
        mock_tool: Mock tool instance.
    """
    mock_tool.set_options.side_effect = ValueError("Invalid value")

    with (
        patch(
            "lintro.utils.unified_config_manager.is_tool_injectable",
            return_value=True,
        ),
        patch.object(manager, "get_effective_line_length", return_value=100),
        patch(
            "lintro.utils.unified_config_manager.load_lintro_tool_config",
            return_value={},
        ),
        pytest.raises(ValueError, match="Invalid value"),
    ):
        manager.apply_config_to_tool(mock_tool)


def test_manager_apply_config_raises_type_error_from_tool(
    manager: UnifiedConfigManager,
    mock_tool: MagicMock,
) -> None:
    """Verify TypeError from tool.set_options is re-raised.

    Configuration errors (ValueError, TypeError) should propagate to the caller.

            mock_tool: Mock tool instance.

            mock_tool: Mock tool instance.


    Args:
        manager: Configuration manager instance.
        mock_tool: Mock tool instance.
    """
    mock_tool.set_options.side_effect = TypeError("Type mismatch")

    with (
        patch(
            "lintro.utils.unified_config_manager.is_tool_injectable",
            return_value=True,
        ),
        patch.object(manager, "get_effective_line_length", return_value=100),
        patch(
            "lintro.utils.unified_config_manager.load_lintro_tool_config",
            return_value={},
        ),
        pytest.raises(TypeError, match="Type mismatch"),
    ):
        manager.apply_config_to_tool(mock_tool)


def test_manager_apply_config_handles_other_errors_gracefully(
    manager: UnifiedConfigManager,
    mock_tool: MagicMock,
) -> None:
    """Verify non-config errors are caught and logged.

    Unexpected errors (not ValueError/TypeError) should be caught and
    logged as warnings, not re-raised.

            mock_tool: Mock tool instance.

            mock_tool: Mock tool instance.


    Args:
        manager: Configuration manager instance.
        mock_tool: Mock tool instance.
    """
    mock_tool.set_options.side_effect = RuntimeError("Unexpected")

    with (
        patch(
            "lintro.utils.unified_config_manager.is_tool_injectable",
            return_value=True,
        ),
        patch.object(manager, "get_effective_line_length", return_value=100),
        patch(
            "lintro.utils.unified_config_manager.load_lintro_tool_config",
            return_value={},
        ),
    ):
        # Should not raise
        manager.apply_config_to_tool(mock_tool)


def test_manager_apply_config_skips_non_injectable_line_length(
    manager: UnifiedConfigManager,
    mock_tool: MagicMock,
) -> None:
    """Verify line_length is not set for non-injectable tools.

    When is_tool_injectable returns False, line_length should not be
    included in the options.

            mock_tool: Mock tool instance.

            mock_tool: Mock tool instance.


    Args:
        manager: Configuration manager instance.
        mock_tool: Mock tool instance.
    """
    with (
        patch(
            "lintro.utils.unified_config_manager.is_tool_injectable",
            return_value=False,
        ),
        patch.object(manager, "get_effective_line_length", return_value=100),
        patch(
            "lintro.utils.unified_config_manager.load_lintro_tool_config",
            return_value={"other_option": True},
        ),
    ):
        manager.apply_config_to_tool(mock_tool)

        call_kwargs = mock_tool.set_options.call_args[1]
        assert_that(call_kwargs).does_not_contain_key("line_length")
        assert_that(call_kwargs).contains_key("other_option")


# =============================================================================
# Tests for get_report method
# =============================================================================


def test_manager_get_report_returns_string(manager: UnifiedConfigManager) -> None:
    """Verify get_report returns a string.

    The report should be a formatted string containing configuration info.


    Args:
        manager: Configuration manager instance.
    """
    with patch(
        "lintro.utils.config_reporting.get_config_report",
        return_value="Report",
    ):
        result = manager.get_report()

        assert_that(result).is_equal_to("Report")


def test_manager_get_report_delegates_to_config_reporting(
    manager: UnifiedConfigManager,
) -> None:
    """Verify get_report calls the config_reporting module.

    The report generation is delegated to get_config_report function.


    Args:
        manager: Configuration manager instance.
    """
    with patch(
        "lintro.utils.config_reporting.get_config_report",
        return_value="Detailed Report",
    ) as mock_report:
        manager.get_report()

        mock_report.assert_called_once()


# =============================================================================
# Tests for print_report method
# =============================================================================


def test_manager_print_report_calls_config_reporting(
    manager: UnifiedConfigManager,
) -> None:
    """Verify print_report delegates to print_config_report.

    The print_report method should call the print_config_report function
    from the config_reporting module.


    Args:
        manager: Configuration manager instance.
    """
    with patch(
        "lintro.utils.config_reporting.print_config_report",
    ) as mock_print:
        manager.print_report()

        mock_print.assert_called_once()


def test_manager_print_report_does_not_return_value(
    manager: UnifiedConfigManager,
) -> None:
    """Verify print_report can be called successfully.

    The method prints to console but doesn't return a value.


    Args:
        manager: Configuration manager instance.
    """
    with patch("lintro.utils.config_reporting.print_config_report"):
        manager.print_report()  # Should complete without error


# =============================================================================
# Integration-style tests for UnifiedConfigManager
# =============================================================================


def test_manager_is_dataclass_instance(manager: UnifiedConfigManager) -> None:
    """Verify UnifiedConfigManager is a proper dataclass instance.

    The manager should be a dataclass with the expected fields.


    Args:
        manager: Configuration manager instance.
    """
    import dataclasses

    assert_that(dataclasses.is_dataclass(manager)).is_true()
    assert_that(dataclasses.fields(manager)).is_length(3)


def test_manager_fields_are_accessible(manager: UnifiedConfigManager) -> None:
    """Verify all manager fields are accessible after initialization.

    The global_config, tool_configs, and warnings fields should be accessible.


    Args:
        manager: Configuration manager instance.
    """
    assert_that(manager.global_config).is_instance_of(dict)
    assert_that(manager.tool_configs).is_instance_of(dict)
    assert_that(manager.warnings).is_instance_of(list)


def test_manager_can_be_created_with_default_factory_values() -> None:
    """Verify manager can be created and has default factory values.

    The dataclass default_factory functions should create empty containers.
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
        manager = UnifiedConfigManager()

        assert_that(manager.global_config).is_empty()
        assert_that(manager.tool_configs).is_empty()
        assert_that(manager.warnings).is_empty()
