"""Tool configuration utilities for execution.

This module provides functions for configuring tools before execution
and determining which tools to run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lintro.config.config_loader import get_config
from lintro.enums.action import Action, normalize_action
from lintro.enums.tool_name import ToolName
from lintro.enums.tools_value import ToolsValue
from lintro.tools import tool_manager
from lintro.utils.unified_config import UnifiedConfigManager

if TYPE_CHECKING:
    from lintro.config.lintro_config import LintroConfig
    from lintro.plugins.base import BaseToolPlugin


@dataclass(frozen=True)
class SkippedTool:
    """A tool that was skipped during tool selection."""

    name: str
    reason: str


@dataclass
class ToolsToRunResult:
    """Result of get_tools_to_run() with both active and skipped tools."""

    to_run: list[str] = field(default_factory=list)
    skipped: list[SkippedTool] = field(default_factory=list)


def _get_disabled_reason(config: LintroConfig, tool_name: str) -> str:
    """Determine why a tool is disabled.

    Args:
        config: Lintro configuration.
        tool_name: Name of the tool.

    Returns:
        Human-readable reason string.
    """
    tool_lower = tool_name.lower()

    # Check if excluded by enabled_tools allowlist
    if config.execution.enabled_tools:
        enabled_lower = [t.lower() for t in config.execution.enabled_tools]
        if tool_lower not in enabled_lower:
            return "not in enabled_tools"

    # Check tool-level enabled flag
    tool_config = config.get_tool_config(tool_lower)
    if not tool_config.enabled:
        return "disabled in config"

    return "disabled"


def configure_tool_for_execution(
    tool: BaseToolPlugin,
    tool_name: str,
    config_manager: UnifiedConfigManager,
    tool_option_dict: dict[str, dict[str, object]],
    exclude: str | None,
    include_venv: bool,
    incremental: bool,
    action: Action,
    post_tools: set[str],
    auto_install: bool = False,
    lintro_config: LintroConfig | None = None,
) -> None:
    """Configure a tool for execution.

    Applies CLI overrides, unified config, and common options.
    This eliminates duplication between parallel and sequential execution paths.

    Args:
        tool: The tool plugin instance to configure.
        tool_name: Name of the tool.
        config_manager: Unified config manager.
        tool_option_dict: Parsed tool options from CLI.
        exclude: Exclude patterns (comma-separated).
        include_venv: Whether to include virtual environment directories.
        incremental: Whether to only check changed files.
        action: The action being performed (check/fix).
        post_tools: Set of post-check tool names.
        auto_install: Whether to auto-install Node.js deps if missing (global default).
        lintro_config: Optional LintroConfig to reuse; fetched via get_config() if None.
    """
    # Reset accumulated state from prior runs (singleton instances)
    tool.reset_options()

    # Build CLI overrides from --tool-options
    cli_overrides: dict[str, object] = {}
    for option_key in get_tool_lookup_keys(tool_name):
        overrides = tool_option_dict.get(option_key)
        if overrides:
            cli_overrides.update(overrides)

    # Apply unified config with CLI overrides
    config_manager.apply_config_to_tool(
        tool=tool,
        cli_overrides=cli_overrides if cli_overrides else None,
    )

    # Set common options
    if exclude:
        exclude_patterns = [p.strip() for p in exclude.split(",")]
        tool.set_options(exclude_patterns=exclude_patterns)

    tool.set_options(include_venv=include_venv)

    # Set incremental mode if enabled
    if incremental:
        tool.set_options(incremental=True)

    # Resolve per-tool auto_install: per-tool config > global effective > False
    lintro_config = lintro_config or get_config()
    tool_cfg = lintro_config.get_tool_config(tool_name)
    if tool_cfg.auto_install is not None:
        effective_tool_auto_install = tool_cfg.auto_install
    else:
        effective_tool_auto_install = auto_install

    if effective_tool_auto_install:
        tool.set_options(auto_install=True)

    # Handle Black post-check coordination with Ruff
    # If Black is configured as a post-check, avoid double formatting by
    # disabling Ruff's formatting stages unless explicitly overridden.
    if "black" in post_tools and tool_name == ToolName.RUFF.value:
        tool_config = config_manager.get_tool_config(tool_name)
        lintro_tool_cfg = tool_config.lintro_tool_config or {}
        if action == Action.FIX:
            if "format" not in cli_overrides and "format" not in lintro_tool_cfg:
                tool.set_options(format=False)
        else:  # check
            if (
                "format_check" not in cli_overrides
                and "format_check" not in lintro_tool_cfg
            ):
                tool.set_options(format_check=False)


def get_tool_display_name(tool_name: str) -> str:
    """Get the canonical display name for a tool.

    Args:
        tool_name: The tool name (case-insensitive).

    Returns:
        The canonical display name for the tool.
    """
    return tool_name.lower()


def get_tool_lookup_keys(tool_name: str) -> set[str]:
    """Get all possible lookup keys for a tool in tool_option_dict.

    Args:
        tool_name: The canonical display name for the tool.

    Returns:
        Set of lowercase keys to check in tool_option_dict.
    """
    return {tool_name.lower()}


def get_tools_to_run(
    tools: str | ToolsValue | None,
    action: str | Action,
    *,
    ignore_conflicts: bool = False,
) -> ToolsToRunResult:
    """Get the list of tools to run based on the tools string and action.

    Args:
        tools: Comma-separated tool names, "all", or None.
        action: "check", "fmt", or "test".
        ignore_conflicts: If True, skip conflict checking between tools.

    Returns:
        ToolsToRunResult with tools to run and skipped tools with reasons.

    Raises:
        ValueError: If unknown tool names are provided.
    """
    action = normalize_action(action)
    if action == Action.TEST:
        # Test action only supports pytest
        if tools and tools.lower() != "pytest":
            raise ValueError(
                (
                    "Only 'pytest' is supported for the test action; "
                    "run 'lintro test' without --tools or "
                    "use '--tools pytest'"
                ),
            )
        # Use tool_manager to trigger discovery before checking registration
        if not tool_manager.is_tool_registered("pytest"):
            raise ValueError("pytest tool is not available")
        # Respect enabled/disabled config for pytest
        lintro_config = get_config()
        if not lintro_config.is_tool_enabled("pytest"):
            reason = _get_disabled_reason(lintro_config, "pytest")
            return ToolsToRunResult(
                skipped=[SkippedTool(name="pytest", reason=reason)],
            )
        return ToolsToRunResult(to_run=["pytest"])

    # Get lintro config for enabled/disabled tool checking
    lintro_config = get_config()

    if (
        tools is None
        or tools == ToolsValue.ALL
        or (isinstance(tools, str) and tools.lower() == "all")
    ):
        # Get all available tools for the action
        if action == Action.FIX:
            available_tools = tool_manager.get_fix_tools()
        else:  # check
            available_tools = tool_manager.get_check_tools()

        to_run: list[str] = []
        skipped: list[SkippedTool] = []
        for name in available_tools:
            if name.lower() == "pytest":
                continue
            if not lintro_config.is_tool_enabled(name):
                reason = _get_disabled_reason(lintro_config, name)
                skipped.append(SkippedTool(name=name, reason=reason))
            else:
                to_run.append(name)

        # Apply execution ordering and conflict resolution
        if to_run:
            ordered = tool_manager.get_tool_execution_order(
                to_run,
                ignore_conflicts=ignore_conflicts,
            )
            removed = set(to_run) - set(ordered)
            for name in removed:
                skipped.append(
                    SkippedTool(name=name, reason="removed by conflict resolution"),
                )
            to_run = ordered

        return ToolsToRunResult(to_run=to_run, skipped=skipped)

    # Parse specific tools
    tool_names: list[str] = [name.strip().lower() for name in tools.split(",")]
    to_run = []
    skipped = []

    for name in tool_names:
        # Reject pytest for check/fmt actions
        if name == ToolName.PYTEST.value.lower():
            raise ValueError(
                "pytest tool is not available for check/fmt actions. "
                "Use 'lintro test' instead.",
            )
        # Use tool_manager to trigger discovery before checking registration
        if not tool_manager.is_tool_registered(name):
            available_names = [
                n for n in tool_manager.get_tool_names() if n.lower() != "pytest"
            ]
            raise ValueError(
                f"Unknown tool '{name}'. Available tools: {available_names}",
            )
        # Track disabled tools with reason
        if not lintro_config.is_tool_enabled(name):
            reason = _get_disabled_reason(lintro_config, name)
            skipped.append(SkippedTool(name=name, reason=reason))
            continue
        # Verify the tool supports the requested action
        if action == Action.FIX:
            tool_instance = tool_manager.get_tool(name)
            if not tool_instance.definition.can_fix:
                raise ValueError(
                    f"Tool '{name}' does not support formatting",
                )
        to_run.append(name)

    # Apply execution ordering and conflict resolution
    if to_run:
        ordered = tool_manager.get_tool_execution_order(
            to_run,
            ignore_conflicts=ignore_conflicts,
        )
        removed = set(to_run) - set(ordered)
        for name in removed:
            skipped.append(
                SkippedTool(name=name, reason="removed by conflict resolution"),
            )
        to_run = ordered

    return ToolsToRunResult(to_run=to_run, skipped=skipped)
