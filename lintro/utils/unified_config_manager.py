"""Unified configuration manager class for Lintro.

This module provides the central configuration manager that coordinates
loading, merging, and applying configurations from multiple sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from lintro.utils.config import load_lintro_global_config, load_lintro_tool_config
from lintro.utils.config_constants import ToolConfigInfo
from lintro.utils.config_priority import get_effective_line_length
from lintro.utils.config_validation import (
    get_tool_config_summary,
    is_tool_injectable,  # Used in get_tool_config() fallback
    validate_config_consistency,
)
from lintro.utils.native_parsers import _load_native_tool_config


@dataclass
class UnifiedConfigManager:
    """Central configuration manager for Lintro.

    This class provides a unified interface for:
    - Loading and merging configurations from multiple sources.
    - Computing effective configurations for each tool.
    - Validating configuration consistency.
    - Managing tool execution order.

    Attributes:
        global_config: Global Lintro configuration from [tool.lintro].
        tool_configs: Per-tool configuration info.
        warnings: List of configuration warnings.
    """

    global_config: dict[str, Any] = field(default_factory=dict)
    tool_configs: dict[str, ToolConfigInfo] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialize configuration manager."""
        self.refresh()

    def refresh(self) -> None:
        """Reload all configuration from files."""
        self.global_config = load_lintro_global_config()
        self.tool_configs = get_tool_config_summary()
        self.warnings = validate_config_consistency()

    def get_effective_line_length(self, tool_name: str) -> int | None:
        """Get effective line length for a tool.

        Args:
            tool_name: Name of the tool.

        Returns:
            Effective line length or None.
        """
        return get_effective_line_length(tool_name)

    def get_tool_config(self, tool_name: str) -> ToolConfigInfo:
        """Get configuration info for a specific tool.

        Args:
            tool_name: Name of the tool.

        Returns:
            Tool configuration info.
        """
        if tool_name not in self.tool_configs:
            self.tool_configs[tool_name] = ToolConfigInfo(
                tool_name=tool_name,
                native_config=_load_native_tool_config(tool_name),
                lintro_tool_config=load_lintro_tool_config(tool_name),
                is_injectable=is_tool_injectable(tool_name),
            )
        return self.tool_configs[tool_name]

    def apply_config_to_tool(
        self,
        tool: Any,
        cli_overrides: dict[str, Any] | None = None,
    ) -> None:
        """Apply effective configuration to a tool instance.

        Priority order:
        1. CLI overrides (if provided).
        2. [tool.lintro.<tool>] config.
        3. Global [tool.lintro] settings.

        Args:
            tool: Tool instance with set_options method.
            cli_overrides: Optional CLI override options.

        Raises:
            TypeError: If tool configuration has type mismatches.
            ValueError: If tool configuration has invalid values.
        """
        tool_name = getattr(tool, "name", "").lower()
        if not tool_name:
            return

        # Start with global settings
        effective_opts: dict[str, Any] = {}

        # Get cached tool config to avoid repeated file reads
        tool_config = self.get_tool_config(tool_name)

        # Apply global line_length if tool supports it
        if tool_config.is_injectable:
            line_length = self.get_effective_line_length(tool_name)
            if line_length is not None:
                effective_opts["line_length"] = line_length

        # Apply tool-specific lintro config from cache
        effective_opts.update(tool_config.lintro_tool_config)

        # Apply CLI overrides last (highest priority)
        if cli_overrides:
            effective_opts.update(cli_overrides)

        # Apply to tool
        if effective_opts:
            try:
                tool.set_options(**effective_opts)
                logger.debug(f"Applied config to {tool_name}: {effective_opts}")
            except (ValueError, TypeError) as e:
                # Configuration errors should be visible and re-raised
                logger.warning(
                    f"Configuration error for {tool_name}: {e}",
                    exc_info=True,
                )
                raise
            except (AttributeError, KeyError, RuntimeError) as e:
                # Other unexpected errors - log at warning but allow execution
                logger.warning(
                    f"Failed to apply config to {tool_name}: {type(e).__name__}: {e}",
                    exc_info=True,
                )

    def get_report(self) -> str:
        """Get configuration report.

        Returns:
            Formatted configuration report string.
        """
        # Late import to avoid circular dependency
        from lintro.utils.config_reporting import get_config_report

        return str(get_config_report())

    def print_report(self) -> None:
        """Print configuration report."""
        # Late import to avoid circular dependency
        from lintro.utils.config_reporting import print_config_report

        print_config_report()
