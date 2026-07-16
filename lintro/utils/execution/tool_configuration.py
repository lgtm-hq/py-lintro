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
    detected_languages: list[str] = field(default_factory=list)
    scoped_by_detection: bool = False


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


def _detection_scoped_tool_names() -> tuple[list[str], set[str]]:
    """Compute the language-scoped toolset for a no-config first run.

    Detects the languages present in the current working directory and
    resolves them to the union of recommended tool names via the manifest
    registry's language map. Security tools are always included.

    Returns:
        Tuple of ``(detected_languages, scoped_tool_names)`` where
        ``detected_languages`` is the sorted list of detected language
        identifiers and ``scoped_tool_names`` is the lowercase set of tool
        names applicable to those languages.
    """
    from lintro.tools.core.tool_registry import ManifestRegistry
    from lintro.utils.project_detection import detect_project_languages

    detected_languages = detect_project_languages()
    registry = ManifestRegistry.load()
    scoped_tools = registry.tools_for_languages(detected_languages)
    scoped_names = {t.name.lower() for t in scoped_tools}
    return detected_languages, scoped_names


def format_detection_notice(
    detected_languages: list[str],
    to_run: list[str],
) -> str:
    """Build the informational line for a language-scoped no-config run.

    Groups the selected tools by the detected language they belong to so the
    user sees, e.g., ``(python: bandit, black, mypy, ruff)``.

    Args:
        detected_languages: Languages detected in the project.
        to_run: Tool names selected for execution.

    Returns:
        A single-line notice pointing the user at ``lintro init``.
    """
    from lintro.tools.core.tool_registry import ManifestRegistry

    registry = ManifestRegistry.load()
    language_map = registry.language_map
    run_set = {name.lower() for name in to_run}

    groups: list[str] = []
    seen: set[str] = set()
    for lang in detected_languages:
        mapped = language_map.get(lang.lower(), [])
        lang_tools = sorted(
            name for name in mapped if name.lower() in run_set and name not in seen
        )
        seen.update(lang_tools)
        if lang_tools:
            groups.append(f"{lang}: {', '.join(lang_tools)}")

    # Include any remaining tools (e.g. always-on security tools) not tied to a
    # detected language so the notice matches what actually runs.
    remaining = sorted(name for name in to_run if name not in seen)
    if remaining:
        groups.append(f"security: {', '.join(remaining)}")

    detail = "; ".join(groups) if groups else "none"
    return (
        f"No config found — using detected toolset ({detail}). "
        "Run 'lintro init' to customize."
    )


def get_tools_to_run(
    tools: str | ToolsValue | None,
    action: str | Action,
    *,
    ignore_conflicts: bool = False,
    lintro_config: LintroConfig | None = None,
) -> ToolsToRunResult:
    """Get the list of tools to run based on the tools string and action.

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
        # Respect enabled/disabled config for pytest
        config = lintro_config or get_config()
        if not config.is_tool_enabled("pytest"):
            reason = _get_disabled_reason(config, "pytest")
            return ToolsToRunResult(
                skipped=[SkippedTool(name="pytest", reason=reason)],
            )
        return ToolsToRunResult(to_run=["pytest"])

    # Get lintro config for enabled/disabled tool checking
    config = lintro_config or get_config()

    is_explicit_all = tools == ToolsValue.ALL or (
        isinstance(tools, str) and tools.lower() == "all"
    )

    if tools is None or is_explicit_all:
        # Get all available tools for the action
        if action == Action.FIX:
            available_tools = tool_manager.get_fix_tools()
        else:  # check
            available_tools = tool_manager.get_check_tools()

        # On a no-config default run (``tools`` is None and no config file was
        # discovered), scope the toolset to the languages actually present in
        # the project. Tools not applicable to the detected languages are
        # omitted entirely rather than surfaced as SKIP rows, so a Python-only
        # project no longer fires (and lists) ~30 irrelevant tools. Explicit
        # ``--tools all`` and any configured project keep the full behavior.
        detected_languages: list[str] = []
        scoped_names: set[str] | None = None
        scoped_by_detection = False
        if tools is None and config.config_path is None:
            detected_languages, scoped_names = _detection_scoped_tool_names()
            scoped_by_detection = True

        to_run: list[str] = []
        skipped: list[SkippedTool] = []
        for name in available_tools:
            if name.lower() == "pytest":
                continue
            # Silently drop tools that do not apply to detected languages.
            if scoped_names is not None and name.lower() not in scoped_names:
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

        return ToolsToRunResult(
            to_run=to_run,
            skipped=skipped,
            detected_languages=detected_languages,
            scoped_by_detection=scoped_by_detection,
        )

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
        if not config.is_tool_enabled(name):
            reason = _get_disabled_reason(config, name)
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

    to_run = _apply_conflict_resolution(
        to_run,
        skipped,
        ignore_conflicts=ignore_conflicts,
    )

    return ToolsToRunResult(to_run=to_run, skipped=skipped)
