"""Tool configuration utilities for execution.

This module provides functions for configuring tools before execution
and determining which tools to run.
"""

from __future__ import annotations

import difflib
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


def _tool_name_lookup_candidates(name: str) -> list[str]:
    """Build hyphen/underscore lookup candidates for a user-supplied tool name.

    Registry keys are inconsistently hyphenated or underscored (e.g.
    ``html_validate`` vs ``astro-check``). Accept either spelling by trying
    both forms, matching :func:`lintro.enums.tool_name.normalize_tool_name`'s
    hyphen→underscore tolerance without breaking hyphen-registered tools.

    Args:
        name: Raw tool name from ``--tools`` (already stripped/lowercased).

    Returns:
        Deduplicated candidate registry keys to try, in preference order.
    """
    candidates: list[str] = [name]
    underscored = name.replace("-", "_")
    if underscored != name:
        candidates.append(underscored)
    hyphenated = name.replace("_", "-")
    if hyphenated != name:
        candidates.append(hyphenated)
    return candidates


def _resolve_registered_tool_name(name: str) -> str | None:
    """Resolve a user-supplied tool name to the registered registry key.

    Args:
        name: Raw tool name from ``--tools`` (already stripped/lowercased).

    Returns:
        The registered tool name if found, otherwise ``None``.
    """
    for candidate in _tool_name_lookup_candidates(name=name):
        if tool_manager.is_tool_registered(candidate):
            return candidate
    return None


def _unknown_tool_error_message(*, name: str, available_names: list[str]) -> str:
    """Build an Unknown-tool error with an optional nearest-match hint.

    Args:
        name: The unresolved user-supplied name.
        available_names: Registered tool names (excluding pytest).

    Returns:
        Error message string for ``ValueError``.
    """
    message = f"Unknown tool '{name}'. Available tools: {available_names}"
    # Prefer matching against both registered names and underscore/hyphen
    # aliases so typos near either spelling still suggest well.
    suggestion_pool: list[str] = list(available_names)
    for registered in available_names:
        underscored = registered.replace("-", "_")
        hyphenated = registered.replace("_", "-")
        if underscored not in suggestion_pool:
            suggestion_pool.append(underscored)
        if hyphenated not in suggestion_pool:
            suggestion_pool.append(hyphenated)
    matches = difflib.get_close_matches(
        name,
        suggestion_pool,
        n=1,
        cutoff=0.6,
    )
    if matches:
        message = f"{message} Did you mean '{matches[0]}'?"
    return message


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


def _apply_conflict_resolution(
    to_run: list[str],
    skipped: list[SkippedTool],
    *,
    ignore_conflicts: bool,
) -> list[str]:
    """Apply execution ordering and conflict resolution to a tool list.

    Mutates *skipped* in place by appending tools removed during
    conflict resolution.

    Args:
        to_run: Candidate tool names.
        skipped: Accumulator for skipped tools (mutated in place).
        ignore_conflicts: Whether to ignore tool conflicts.

    Returns:
        The ordered list of tools to run.
    """
    if not to_run:
        return to_run
    ordered = tool_manager.get_tool_execution_order(
        to_run,
        ignore_conflicts=ignore_conflicts,
    )
    removed = set(to_run) - set(ordered)
    for name in sorted(removed):
        skipped.append(
            SkippedTool(name=name, reason="removed by conflict resolution"),
        )
    return ordered


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
    diff_base: str | None = None,
) -> BaseToolPlugin:
    """Configure a tool for execution.

    Applies CLI overrides, unified config, and common options.
    This eliminates duplication between parallel and sequential execution paths.

    Configuration is applied to a private per-invocation copy of ``tool``
    rather than to the shared registry singleton, so concurrent logical
    invocations do not clobber one another's option state. The passed-in
    ``tool`` instance is left untouched; callers must execute against the
    returned instance.

    Args:
        tool: The tool plugin instance to configure (used as a template; not
            mutated).
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
        diff_base: Resolved git base ref for ``--diff`` scanning, or None to scan
            all discovered files.

    Returns:
        The configured per-invocation tool copy to run.
    """
    # Operate on an isolated per-invocation copy so parallel executions do
    # not race on the shared singleton's option state.
    tool = tool.copy_for_execution()

    # Reset accumulated state from prior runs (defensive; the copy already
    # reflects the template's baseline state).
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

    # Set git-diff base so discovery restricts to changed files.
    if diff_base:
        tool.set_options(diff_base=diff_base)

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

    return tool


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
    lintro_config: LintroConfig | None = None,
) -> ToolsToRunResult:
    """Get the list of tools to run based on the tools string and action.

    ``execution.enabled_tools`` filters default / ``all`` runs only. An
    explicit ``--tools`` list bypasses that allowlist so named tools still
    run; per-tool ``tools.<name>.enabled: false`` continues to apply.

    Args:
        tools: Comma-separated tool names, "all", or None.
        action: "check", "fmt", or "test".
        ignore_conflicts: If True, skip conflict checking between tools.
        lintro_config: Optional config override; uses global config when omitted.

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
        # Explicit --tools pytest bypasses execution.enabled_tools; default
        # runs (tools is None) still apply the allowlist. Per-tool
        # tools.pytest.enabled: false always applies.
        config = lintro_config or get_config()
        if tools is None:
            if not config.is_tool_enabled("pytest"):
                reason = _get_disabled_reason(config, "pytest")
                return ToolsToRunResult(
                    skipped=[SkippedTool(name="pytest", reason=reason)],
                )
        elif not config.get_tool_config("pytest").enabled:
            return ToolsToRunResult(
                skipped=[
                    SkippedTool(
                        name="pytest",
                        reason="disabled in config",
                    ),
                ],
            )
        return ToolsToRunResult(to_run=["pytest"])

    # Get lintro config for enabled/disabled tool checking
    config = lintro_config or get_config()

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
            if not config.is_tool_enabled(name):
                reason = _get_disabled_reason(config, name)
                skipped.append(SkippedTool(name=name, reason=reason))
            else:
                to_run.append(name)

        to_run = _apply_conflict_resolution(
            to_run,
            skipped,
            ignore_conflicts=ignore_conflicts,
        )

        return ToolsToRunResult(to_run=to_run, skipped=skipped)

    # Parse specific tools (accept hyphen or underscore spellings)
    tool_names: list[str] = [name.strip().lower() for name in tools.split(",")]
    to_run = []
    skipped = []

    for raw_name in tool_names:
        # Reject pytest for check/fmt actions (either spelling)
        if raw_name.replace("-", "_") == ToolName.PYTEST.value.lower():
            raise ValueError(
                "pytest tool is not available for check/fmt actions. "
                "Use 'lintro test' instead.",
            )
        # Resolve hyphen↔underscore aliases to the registered registry key
        resolved_name = _resolve_registered_tool_name(name=raw_name)
        if resolved_name is None:
            available_names = [
                n for n in tool_manager.get_tool_names() if n.lower() != "pytest"
            ]
            raise ValueError(
                _unknown_tool_error_message(
                    name=raw_name,
                    available_names=available_names,
                ),
            )
        # Explicit --tools bypasses execution.enabled_tools (that allowlist
        # scopes default / --tools all runs only). Still honor per-tool
        # tools.<name>.enabled: false.
        tool_config = config.get_tool_config(resolved_name)
        if not tool_config.enabled:
            skipped.append(
                SkippedTool(name=resolved_name, reason="disabled in config"),
            )
            continue
        # Verify the tool supports the requested action
        if action == Action.FIX:
            tool_instance = tool_manager.get_tool(resolved_name)
            if not tool_instance.definition.can_fix:
                raise ValueError(
                    f"Tool '{resolved_name}' does not support formatting",
                )
        to_run.append(resolved_name)

    to_run = _apply_conflict_resolution(
        to_run,
        skipped,
        ignore_conflicts=ignore_conflicts,
    )

    return ToolsToRunResult(to_run=to_run, skipped=skipped)
