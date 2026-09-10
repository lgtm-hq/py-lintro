"""Unit tests for UnifiedConfigManager apply_config, reporting, and integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that
from loguru import logger

from lintro.utils.unified_config import UnifiedConfigManager


@dataclass
class RecordingTool:
    """Minimal stand-in for a tool that records the options applied to it.

    Attributes:
        name: Tool name the manager reads to look up configuration.
        applied: Option mappings handed to :meth:`set_options`, in order.
    """

    name: str
    applied: list[dict[str, Any]] = field(default_factory=list)

    def set_options(self, **options: Any) -> None:
        """Record one set of applied options.

        Args:
            **options: Effective options the manager resolved for this tool.
        """
        self.applied.append(dict(options))


# =============================================================================
# Tests for apply_config_to_tool method
# =============================================================================


def test_manager_apply_config_does_nothing_for_tool_without_name(
    manager: UnifiedConfigManager,
) -> None:
    """Verify apply_config_to_tool skips tools without a name.

    A tool with an empty name has no configuration to look up, so it must come
    back with no options applied at all.

    Args:
        manager: Configuration manager instance.
    """
    tool = RecordingTool(name="")

    manager.apply_config_to_tool(tool)

    assert_that(tool.applied).is_empty()


def test_manager_apply_config_calls_set_options_with_effective_config(
    manager: UnifiedConfigManager,
) -> None:
    """Verify apply_config_to_tool merges every source into one option set.

    The line length, the ``[tool.lintro.<tool>]`` config and the CLI overrides
    must all arrive on the tool in a single call.

    Args:
        manager: Configuration manager instance.
    """
    tool = RecordingTool(name="ruff")

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
        manager.apply_config_to_tool(tool, cli_overrides={"debug": True})

    assert_that(tool.applied).is_equal_to(
        [{"line_length": 100, "strict": True, "debug": True}],
    )


def test_manager_apply_config_cli_overrides_take_precedence(
    manager: UnifiedConfigManager,
) -> None:
    """Verify CLI overrides have highest priority.

    Three sources name ``line_length`` at once: the effective line length,
    the tool's own config and the CLI. The value the tool actually receives
    must be the CLI one.

    Args:
        manager: Configuration manager instance.
    """
    tool = RecordingTool(name="ruff")

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
        manager.apply_config_to_tool(tool, cli_overrides={"line_length": 120})

    assert_that(tool.applied).is_equal_to([{"line_length": 120}])


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
    ):
        with pytest.raises(ValueError, match="Invalid value"):
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
    ):
        with pytest.raises(TypeError, match="Type mismatch"):
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
) -> None:
    """Verify line_length is not set for non-injectable tools.

    A tool that cannot take an injected line length receives only its own
    configuration, even though an effective line length is available.

    Args:
        manager: Configuration manager instance.
    """
    tool = RecordingTool(name="ruff")

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
        manager.apply_config_to_tool(tool)

    assert_that(tool.applied).is_equal_to([{"other_option": True}])


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


# =============================================================================
# Tests for print_report method
# =============================================================================


def test_manager_print_report_emits_the_report_lines(
    manager: UnifiedConfigManager,
) -> None:
    """Verify print_report emits the generated report through the logger.

    Args:
        manager: Configuration manager instance.
    """
    emitted: list[str] = []
    handler_id = logger.add(
        lambda message: emitted.append(str(message)),
        level="INFO",
        format="{message}",
    )
    try:
        with patch(
            "lintro.utils.config_reporting.get_config_report",
            return_value="first line\nsecond line",
        ):
            manager.print_report()
    finally:
        logger.remove(handler_id)

    joined = "".join(emitted)
    assert_that(joined).contains("first line")
    assert_that(joined).contains("second line")


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
